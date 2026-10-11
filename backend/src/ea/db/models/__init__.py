"""Every mapped table, gathered in one import.

Alembic autogenerates by diffing `Base.metadata` against the live database, and
metadata only knows the classes that have been imported. `migrations/env.py`
imports this package and nothing else, so a table whose module is missing from
the list below is a table autogenerate will propose to *drop*.
"""

from __future__ import annotations

from ea.db.base import Base
from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.db.models.audit import AuditEventRecord
from ea.db.models.baseline import (
    BaselineBoxRecord,
    BaselineElementRecord,
    BaselineRecord,
    BaselineRelationshipRecord,
)
from ea.db.models.chunk import DocumentChunk
from ea.db.models.diagram import (
    DiagramEdgeRecord,
    DiagramNodeRecord,
    DiagramObjectRecord,
    DiagramRecord,
)
from ea.db.models.diagram_template import DiagramTemplateRecord
from ea.db.models.document import ElementDocument
from ea.db.models.file import FileMetadataRecord
from ea.db.models.package import PackageRecord
from ea.db.models.profile import (
    ElementStereotypeRecord,
    ProfileRecord,
    PropertyRecord,
    StereotypeRecord,
)
from ea.db.models.saved_query import SavedQueryRecord

__all__ = [
    "AuditEventRecord",
    "Base",
    "BaselineBoxRecord",
    "BaselineElementRecord",
    "BaselineRecord",
    "BaselineRelationshipRecord",
    "DiagramEdgeRecord",
    "DiagramNodeRecord",
    "DiagramObjectRecord",
    "DiagramRecord",
    "DiagramTemplateRecord",
    "DocumentChunk",
    "ElementDocument",
    "ElementRecord",
    "ElementStereotypeRecord",
    "FileMetadataRecord",
    "PackageRecord",
    "ProfileRecord",
    "PropertyRecord",
    "RelationshipRecord",
    "SavedQueryRecord",
    "StereotypeRecord",
]
