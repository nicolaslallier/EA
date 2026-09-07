"""The OpenAPI document, built as a standalone artefact.

`frontend/src/api/` is generated from this document and committed, so CI can
fail when a backend change moves the schema without the client following — see
`CLAUDE.md`, "Front/back contract". Two things follow from it being committed:

* it must build without a database. Nothing here enters the application
  lifespan, so no driver is ever opened;
* it must be the same document on every machine. Only two settings reach the
  document — the title and the debug flag — and both are passed explicitly, so
  a developer's `.env` cannot produce a diff nobody else can reproduce.

Run it as `python -m ea.openapi`, or through `make openapi`.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ea.core.config import Settings
from ea.main import create_app

#: The title the document carries, taken from the field default rather than
#: repeated, so renaming the application still renames the document.
DOCUMENT_TITLE: str = str(Settings.model_fields["app_name"].default)


def openapi_document() -> dict[str, Any]:
    """The OpenAPI schema of the application, independent of any deployment."""
    settings = Settings(app_name=DOCUMENT_TITLE, debug=True)
    return create_app(settings).openapi()


def main() -> None:
    """Write the document to stdout, stable enough to diff between two runs."""
    json.dump(openapi_document(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
