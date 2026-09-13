"""Who is calling — the one fact about a request the services decide on.

Pure: no JWT, no Keycloak, no FastAPI. A token is turned into a `Caller` by
`repositories/keycloak.py`; what a caller may do is decided in `services/`.
See docs/adr/0032.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

#: The realm role that allows changing the catalogue. Reading needs none.
EDITOR_ROLE: Final = "ea-editor"


@dataclass(frozen=True, slots=True)
class Caller:
    """An authenticated principal: its stable id, a name to log, its realm roles."""

    subject: str
    username: str
    roles: frozenset[str] = field(default_factory=frozenset)

    @property
    def can_write(self) -> bool:
        return EDITOR_ROLE in self.roles


#: Who calls when `EA_AUTH_ENABLED` is off — which `Settings` allows in debug only.
LOCAL_DEVELOPER: Final = Caller("local-developer", "local-developer", frozenset({EDITOR_ROLE}))

#: Who calls from an operator's script with no request behind it (`ea.reindex`).
SYSTEM: Final = Caller("ea-system", "ea-system", frozenset({EDITOR_ROLE}))
