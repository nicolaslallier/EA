"""The endpoints of diagram templates — typed skeletons, never an element in them.

Instantiating builds a diagram through `ArchitectureService`: the ArchiMate and
IP-addressing rules apply to every element and relationship it creates, and a
relationship is created only when `create_relationships` is true.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from ea.api.dependencies import Packages, Templates
from ea.api.schemas import (
    AsTemplateRequest,
    DiagramRead,
    DiagramTemplateCreate,
    DiagramTemplateRead,
    DiagramTemplateSummaryRead,
    DiagramTemplateUpdate,
    ErrorResponse,
    InstantiateRequest,
)

router = APIRouter(tags=["diagram-templates"])

_ERROR = {"model": ErrorResponse}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_409_CONFLICT: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
}


@router.get("/diagram-templates", response_model=list[DiagramTemplateSummaryRead])
async def list_templates(templates: Templates) -> list[DiagramTemplateSummaryRead]:
    return [DiagramTemplateSummaryRead.of(t) for t in await templates.list_templates()]


@router.post(
    "/diagram-templates",
    response_model=DiagramTemplateRead,
    status_code=status.HTTP_201_CREATED,
    responses=REJECTED,
)
async def create_template(body: DiagramTemplateCreate, templates: Templates) -> DiagramTemplateRead:
    return DiagramTemplateRead.of(
        await templates.create_template(
            name=body.name, description=body.description, body=body.body.raw()
        )
    )


@router.get(
    "/diagram-templates/{template_id}", response_model=DiagramTemplateRead, responses=REJECTED
)
async def read_template(template_id: UUID, templates: Templates) -> DiagramTemplateRead:
    return DiagramTemplateRead.of(await templates.get_template(template_id))


@router.put(
    "/diagram-templates/{template_id}", response_model=DiagramTemplateRead, responses=REJECTED
)
async def update_template(
    template_id: UUID, body: DiagramTemplateUpdate, templates: Templates
) -> DiagramTemplateRead:
    return DiagramTemplateRead.of(
        await templates.update_template(
            template_id,
            name=body.name,
            description=body.description,
            body=body.body.raw() if body.body else None,
        )
    )


@router.delete(
    "/diagram-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT, responses=REJECTED
)
async def delete_template(template_id: UUID, templates: Templates) -> Response:
    await templates.delete_template(template_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/diagram-templates/{template_id}/instantiate",
    response_model=DiagramRead,
    status_code=status.HTTP_201_CREATED,
    responses=REJECTED,
)
async def instantiate_template(
    template_id: UUID, body: InstantiateRequest, templates: Templates, packages: Packages
) -> DiagramRead:
    """Build a diagram from a template.

    Each slot is bound to an existing element of its type, to an element to
    create, or left out. Everything refusable is refused before anything is
    written; relationships are created only if `create_relationships` is true.
    """
    detail = await templates.instantiate(
        template_id,
        name=body.name,
        bindings=[binding.placed() for binding in body.bindings],
        create_relationships=body.create_relationships,
    )
    return DiagramRead.of(detail, freezes=await packages.freezes())


@router.post(
    "/diagrams/{diagram_id}/as-template",
    response_model=DiagramTemplateRead,
    status_code=status.HTTP_201_CREATED,
    responses=REJECTED,
)
async def save_as_template(
    diagram_id: UUID, body: AsTemplateRequest, templates: Templates
) -> DiagramTemplateRead:
    """Keep the shape of a diagram: each element becomes a slot of the same type."""
    return DiagramTemplateRead.of(
        await templates.create_from_diagram(
            diagram_id, name=body.name, description=body.description
        )
    )
