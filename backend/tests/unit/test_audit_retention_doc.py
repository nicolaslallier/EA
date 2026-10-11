"""The retention procedure names what the migration and the Makefile hold, or it is a lie."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOC = (REPO / "docs" / "audit-retention.md").read_text(encoding="utf-8")
MIGRATION = (REPO / "backend" / "migrations" / "versions" / "0011_audit_events.py").read_text(
    encoding="utf-8"
)
MAKEFILE = (REPO / "Makefile").read_text(encoding="utf-8")


def test_every_trigger_the_doc_disables_exists_in_the_migration() -> None:
    for trigger in ("audit_events_no_update_delete", "audit_events_no_truncate"):
        assert f"CREATE TRIGGER {trigger}" in MIGRATION
        assert trigger in DOC


def test_the_doc_says_it_is_an_operator_job_outside_the_application() -> None:
    assert "hors application" in DOC
    assert "24 mois" in DOC


def test_the_doc_says_nothing_purges_and_nothing_caps() -> None:
    assert "aucune purge automatique" in DOC
    assert "aucun plafond" in DOC


def test_every_make_target_the_doc_runs_exists() -> None:
    for target in ("pg-backup", "baseline-capture"):
        assert f"make {target}" in DOC
        assert f"\n{target}:" in MAKEFILE


def test_the_doc_is_honest_about_what_the_purge_costs_and_leaves_untraced() -> None:
    assert "ACCESS EXCLUSIVE" in DOC
    assert "hors heures d'usage" in DOC
    assert "n'est pas tracée" in DOC
    assert "registre d'exploitation" in DOC
    assert "ne prouve rien" in DOC
