"""Every mapped table, gathered in one import.

Alembic autogenerates by diffing `Base.metadata` against the live database, and
metadata only knows the classes that have been imported. `migrations/env.py`
imports this package and nothing else, so a table whose module is missing from
the list below is a table autogenerate will propose to *drop*.
"""

from __future__ import annotations

from ea.db.base import Base
from ea.db.models.chunk import DocumentChunk
from ea.db.models.document import ElementDocument

__all__ = ["Base", "DocumentChunk", "ElementDocument"]
