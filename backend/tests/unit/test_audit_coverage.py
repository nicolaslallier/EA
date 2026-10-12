"""No write to the model can slip past the audit trail by omission (docs/adr/0046).

Like `test_service_guards.py`, this reads the source. The analysers below take
source *text*, so the last tests can show them failing on a made-up unaudited
module — a guard that has never been seen to fail guards nothing.

What it enforces:
- A repository method that writes (opens `work(self._sessions)`, commits, adds,
  executes an INSERT/UPDATE/DELETE, or calls a private helper that does)
  takes `audit` as a required keyword-only parameter — on the PostgreSQL class,
  on the port and on the in-memory double.
- A service never calls such a write without naming its audit: not by omission,
  not with an empty literal, not with a variable that is not a function defined
  in that very use case. The one named exception is `UNAUDITED_REINDEX`.
- An `AuditEvent` is built only where the actor, the door and the moment are
  decided. Limit: this is syntax, not types — a value of another type that
  merely shares a name, or an event rebuilt through reflection, is beyond it;
  `replace(...)` naming an identity field is caught, whatever it is applied to.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

import tests.conftest as conftest_module
from ea.domain import ports
from ea.repositories import (
    architecture_store,
    baseline_store,
    diagram_store,
    document_store,
    package_store,
    saved_query_store,
)

SRC = Path(__file__).parents[2] / "src" / "ea"

#: Postgres class -> (port, in-memory double). Every store that writes the model.
AUDITED_STORES = {
    architecture_store.PostgresArchitectureRepository: (
        ports.ArchitectureRepository,
        conftest_module.InMemoryRepository,
    ),
    document_store.PostgresDocumentRepository: (
        ports.DocumentRepository,
        conftest_module.InMemoryDocuments,
    ),
    diagram_store.PostgresDiagramRepository: (
        ports.DiagramRepository,
        conftest_module.InMemoryDiagrams,
    ),
    package_store.PostgresPackageRepository: (
        ports.PackageRepository,
        conftest_module.InMemoryPackages,
    ),
    saved_query_store.PostgresSavedQueryRepository: (
        ports.SavedQueryRepository,
        conftest_module.InMemorySavedQueries,
    ),
    baseline_store.PostgresBaselineRepository: (
        ports.BaselineRepository,
        conftest_module.InMemoryBaselines,
    ),
}
#: The ports whose writes a service may not call without an audit.
AUDITED_PORTS = {port.__name__ for port, _ in AUDITED_STORES.values()}

#: Repository modules whose writes are not the model's, and why.
NOT_AUDITED = {
    "file_metadata_store.py": (
        "`file_metadata` records what is known about a bucket object: it carries its "
        "own uploader and dates, the bucket is the source of truth for what exists, and "
        "the spec (§1) leaves MinIO files out of the trail."
    ),
    "audit_store.py": "the trail itself: it inserts events, it has none",
}
#: Modules outside `repositories/` that write rows directly.
NOT_AUDITED_SCRIPTS = {
    "graph_import.py": "the one-off copy from Neo4j (docs/adr/0033), deleted after cut-over",
}
#: The only places allowed to name the unaudited reindex.
UNAUDITED_ALLOWED = {("documents.py", "reindex_all")}

#: Where an event gets its actor, its door and its moment.
EVENT_BUILDERS = {"services/audit.py", "repositories/audit_store.py", "domain/audit.py"}
IDENTITY_FIELDS = {"actor_sub", "actor_name", "request_id", "channel", "at", "action"}

WRITING_CALLS = {"commit", "add", "add_all", "merge", "flush_and_commit"}
WRITING_STATEMENTS = {"insert", "update", "delete"}


# --- Analysers over source text -------------------------------------------


def _direct_writer(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        function = child.func
        if isinstance(function, ast.Attribute):
            if function.attr == "begin" and "_sessions" in ast.unparse(function.value):
                return True
            if function.attr in WRITING_CALLS and "session" in ast.unparse(function.value):
                return True
            if function.attr == "delete" and "session" in ast.unparse(function.value):
                return True
        if isinstance(function, ast.Name) and function.id in WRITING_STATEMENTS | {"work"}:
            return True  # `work(self._sessions)` is the one way to open a write (adr/0052)
    return False


def writers(class_node: ast.ClassDef) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    """Every method that writes, directly or through a helper of the same class."""
    methods = {
        node.name: node
        for node in class_node.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    writing = {name for name, node in methods.items() if _direct_writer(node)}
    changed = True
    while changed:  # a helper that writes makes whoever calls it a writer
        changed = False
        for name, node in methods.items():
            if name in writing:
                continue
            called = {
                c.func.attr
                for c in ast.walk(node)
                if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and isinstance(c.func.value, ast.Name)
                and c.func.value.id == "self"
            }
            if called & writing:
                writing.add(name)
                changed = True
    return {name: methods[name] for name in writing}


def requires_audit(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    names = [arg.arg for arg in node.args.kwonlyargs]
    if "audit" not in names:
        return False
    return node.args.kw_defaults[names.index("audit")] is None  # no default


def unaudited_writers(source: str) -> list[str]:
    """Public methods of any class in `source` that write and do not require `audit`."""
    found: list[str] = []
    for cls in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ClassDef)):
        for name, node in writers(cls).items():
            if not name.startswith("_") and not requires_audit(node):
                found.append(f"{cls.name}.{name}")
    return found


def _audited_attributes(class_node: ast.ClassDef) -> set[str]:
    """`self._x` attributes initialised from a parameter annotated with an audited port."""
    init = next(
        (
            n
            for n in class_node.body
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == "__init__"
        ),
        None,
    )
    if init is None:
        return set()
    typed = {
        arg.arg
        for arg in [*init.args.args, *init.args.kwonlyargs]
        if arg.annotation is not None
        and any(port in ast.unparse(arg.annotation) for port in AUDITED_PORTS)
    }
    attributes: set[str] = set()
    for statement in ast.walk(init):
        if (
            isinstance(statement, ast.Assign)
            and isinstance(statement.value, ast.Name)
            and statement.value.id in typed
        ):
            for target in statement.targets:
                if isinstance(target, ast.Attribute) and ast.unparse(target.value) == "self":
                    attributes.add(target.attr)
    return attributes


def service_offenders(source: str, filename: str, write_names: set[str]) -> list[str]:
    """Calls of an audited port's write that omit `audit`, or pass one that can be empty."""
    offenders: list[str] = []
    for cls in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ClassDef)):
        attributes = _audited_attributes(cls)
        for function in (
            n for n in ast.walk(cls) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
        ):
            local_functions = {
                n.name
                for n in ast.walk(function)
                if isinstance(n, ast.FunctionDef) and n is not function
            }
            for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
                target = call.func
                if not (
                    isinstance(target, ast.Attribute)
                    and target.attr in write_names
                    and isinstance(target.value, ast.Attribute)
                    and ast.unparse(target.value.value) == "self"
                    and target.value.attr in attributes
                ):
                    continue
                where = f"{filename}:{call.lineno} ({function.name})"
                audit = next((k.value for k in call.keywords if k.arg == "audit"), None)
                if audit is None:
                    offenders.append(f"{where} omits audit")
                elif isinstance(audit, ast.Name) and audit.id in local_functions:
                    continue  # a function of the row, built in this very use case
                elif isinstance(audit, ast.Name) and audit.id == "UNAUDITED_REINDEX":
                    if (filename, function.name) not in UNAUDITED_ALLOWED:
                        offenders.append(f"{where} names UNAUDITED_REINDEX")
                elif isinstance(audit, ast.Tuple | ast.List) and audit.elts:
                    continue  # events written out
                else:
                    offenders.append(f"{where} passes `{ast.unparse(audit)}`, which may be empty")
    return offenders


def event_builders(source: str) -> list[str]:
    """Where `source` builds an `AuditEvent`, or rewrites an identity field of one."""
    tree = ast.parse(source)
    aliases = {"AuditEvent"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases |= {a.asname for a in node.names if a.name == "AuditEvent" and a.asname}
        if isinstance(node, ast.Assign) and ast.unparse(node.value).endswith("AuditEvent"):
            aliases |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
        if name in aliases:
            found.append(f"line {node.lineno}: builds an AuditEvent")
        elif name == "replace" and {k.arg for k in node.keywords} & IDENTITY_FIELDS:
            found.append(f"line {node.lineno}: rewrites an identity field")
    return found


# --- The real code ----------------------------------------------------------


def _class_node(cls: type) -> ast.ClassDef:
    return ast.parse(textwrap.dedent(inspect.getsource(cls))).body[0]  # type: ignore[return-value]


def test_the_expected_writes_are_found() -> None:
    """A guard that finds nothing guards nothing."""
    found = {
        cls.__name__: {n for n in writers(_class_node(cls)) if not n.startswith("_")}
        for cls in AUDITED_STORES
    }
    assert found["PostgresArchitectureRepository"] == {
        "add_element",
        "update_element",
        "transition_element",
        "delete_element",
        "add_relationship",
        "update_relationship",
        "delete_relationship",
    }
    assert found["PostgresDocumentRepository"] == {"add", "replace", "delete"}
    assert found["PostgresDiagramRepository"] == {"add", "save", "delete", "replace_layout"}
    assert found["PostgresPackageRepository"] == {"add", "save", "delete", "move_elements"}
    assert found["PostgresSavedQueryRepository"] == {"add", "save", "delete"}
    assert found["PostgresBaselineRepository"] == {"create", "delete", "apply_restore"}


@pytest.mark.parametrize("store", list(AUDITED_STORES), ids=lambda c: c.__name__)
def test_every_write_requires_an_audit_on_the_store_the_port_and_the_double(store: type) -> None:
    port, double = AUDITED_STORES[store]
    for name in (n for n in writers(_class_node(store)) if not n.startswith("_")):
        for owner in (store, port, double):
            methods = {
                n.name: n
                for n in _class_node(owner).body
                if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
            }
            assert name in methods, f"{owner.__name__}.{name} is missing"
            assert requires_audit(methods[name]), (
                f"{owner.__name__}.{name} must take `*, audit` with no default"
            )


def test_no_repository_module_writes_unaudited_unless_it_is_excused() -> None:
    for path in sorted((SRC / "repositories").glob("*.py")):
        if path.name in NOT_AUDITED:
            continue
        assert unaudited_writers(path.read_text()) == [], path.name
    for name in NOT_AUDITED_SCRIPTS:
        assert (SRC / name).exists(), f"{name} no longer exists: drop it from the list"


def test_the_services_never_write_without_naming_their_audit() -> None:
    write_names = {
        name
        for cls in AUDITED_STORES
        for name in writers(_class_node(cls))
        if not name.startswith("_")
    }
    offenders: list[str] = []
    for path in sorted((SRC / "services").glob("*.py")):
        offenders += service_offenders(path.read_text(), path.name, write_names)
    assert offenders == []


def test_only_the_trail_and_the_stores_ever_build_an_event() -> None:
    offenders = {
        str(path.relative_to(SRC)): event_builders(path.read_text())
        for path in SRC.rglob("*.py")
        if str(path.relative_to(SRC)) not in EVENT_BUILDERS and event_builders(path.read_text())
    }
    assert offenders == {}


# --- The guard fails where it should ---------------------------------------

UNAUDITED_STORE = """
class SneakyRepository:
    async def add(self, thing):                       # writes, takes no audit
        async with self._sessions.begin() as session:
            session.add(thing)

    async def rename(self, thing_id, *, audit=()):    # an audit with a default
        async with self._sessions.begin() as session:
            await session.execute(update(Thing).values(name="x"))

    async def purge(self):                            # commits by hand
        async with self._sessions() as session:
            await session.execute(delete(Thing))
            await session.commit()

    async def move(self, thing_id):                   # writes through a helper
        await self._write(thing_id)

    async def _write(self, thing_id):
        async with self._sessions.begin() as session:
            session.add(thing_id)

    async def read(self, thing_id):                   # innocent
        async with self._sessions() as session:
            return await session.get(Thing, thing_id)
"""


def test_the_guard_fails_on_an_unaudited_writing_module() -> None:
    assert sorted(unaudited_writers(UNAUDITED_STORE)) == [
        "SneakyRepository.add",
        "SneakyRepository.move",
        "SneakyRepository.purge",
        "SneakyRepository.rename",
    ]


SNEAKY_SERVICE = """
class SneakyService:
    def __init__(self, repository: PackageRepository | None, other: ObjectStore) -> None:
        self._packages = repository
        self._store = other

    async def omitted(self):
        await self._packages.add(1)

    async def empty_literal(self):
        await self._packages.add(1, audit=())

    async def empty_variable(self):
        nothing = ()
        await self._packages.save(1, audit=nothing)

    async def honest(self):
        def events(before):
            return ()
        await self._packages.save(1, audit=events)
        await self._packages.add(1, audit=(make_event(),))

    async def not_a_model_store(self):
        await self._store.delete("key")
"""


def test_the_guard_fails_on_a_service_that_hides_behind_another_attribute() -> None:
    offenders = service_offenders(SNEAKY_SERVICE, "sneaky.py", {"add", "save", "delete"})

    assert [o.split(" (")[1].split(")")[0] for o in offenders] == [
        "omitted",
        "empty_literal",
        "empty_variable",
    ]


def test_the_guard_sees_an_aliased_event_and_a_rewritten_identity() -> None:
    source = (
        "from ea.domain.audit import AuditEvent as Evt\n"
        "from dataclasses import replace\n"
        "def forge(e):\n"
        "    Evt(id=1)\n"
        "    return replace(e, actor_sub='someone else')\n"
        "def innocent(element):\n"
        "    return replace(element, name='x')\n"
    )

    assert len(event_builders(source)) == 2
