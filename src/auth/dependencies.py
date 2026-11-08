"""The HTTP door's way of identifying the caller (CONVENTIONS.md §5).

Every protected route takes `actor: CurrentActor`. Bearer only -- this
service sets and reads no cookies; the web app keeps its tokens in its own
Next.js cookies and forwards them as Bearer.
"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.actor import Actor
from src.auth import service
from src.database import Session, get_session
from src.exceptions import Unauthorized

# auto_error=False so a missing header becomes our Unauthorized (401 with the
# usual {"detail"} body) rather than FastAPI's own 403. Also gives Swagger UI
# its "Authorize" button.
_bearer = HTTPBearer(auto_error=False, description="Access token from POST /api/v1/auth/login")


async def current_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Actor:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthorized("Missing bearer token")
    return service.actor_from_access_token(credentials.credentials, channel="api")


CurrentActor = Annotated[Actor, Depends(current_actor)]
SessionDep = Annotated[Session, Depends(get_session)]
