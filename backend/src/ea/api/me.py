"""Who the SPA is talking to, and whether it should offer to write.

The SPA asks rather than reading the token: the API decides, and this is the
same decision the services make. See docs/adr/0032.
"""

from fastapi import APIRouter

from ea.api.auth import CurrentCaller
from ea.api.schemas import ErrorResponse, MeRead

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=MeRead, responses={401: {"model": ErrorResponse}})
async def read_me(caller: CurrentCaller) -> MeRead:
    return MeRead(username=caller.username, can_write=caller.can_write)
