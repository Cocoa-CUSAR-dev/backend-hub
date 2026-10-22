"""What Swagger UI (/docs) and ReDoc (/redoc) show about this API.

Every router sets `tags=[...]` with one of the names below, so endpoints are
grouped and explained in the docs. A new feature folder adds its tag here in
the same PR (CONVENTIONS.md §7).
"""

from typing import Any

TITLE = "Cocoa Backend Hub API"

DESCRIPTION = """
The one backend for the Cocoa Supply Chain Databank (Is Thai Cacao): the
researcher web app, the mobile app and the LINE OA all call this service.

**Auth.** Web sends the session cookie; mobile sends `Authorization: Bearer <token>`.
Both come from the same `POST /api/v1/auth/login`. Use **Authorize** (top right)
to try protected endpoints here.

**Errors.** Every error is `{"detail": "..."}`. Validation errors (422) also carry
`"fields": {"<field>": "<message>"}` so the client can show the message next to
the field.

**Times.** Stored and returned in UTC unless an endpoint says otherwise.
"""

TAGS: list[dict[str, Any]] = [
    {"name": "system", "description": "Health check for the host."},
    {
        "name": "auth",
        "description": "Sign in, refresh, sign out. Every login-like call returns the same "
        "token pair in the body; send `access_token` as `Authorization: Bearer`.",
    },
    {
        "name": "me",
        "description": "My account. One person can hold several roles (farmer, hub collector, "
        "processor, researcher); each role has its own profile.",
    },
    {"name": "admin", "description": "Admin only: grant or remove researcher / admin roles."},
    {
        "name": "agriculture",
        "description": "Farms, plots and farm photos: register them and list mine. Photos are "
        "private; lists carry a link to each farm's newest photo, valid for an hour.",
    },
    {"name": "line", "description": "LINE webhook: every chat event from the LINE OA."},
    {"name": "liff", "description": "Endpoints the LIFF pages call from inside LINE."},
    {
        "name": "reminders",
        "description": "Reminder schedules per task (service key; moving under /api/v1/forms).",
    },
    {
        "name": "notifications",
        "description": "Push a LINE message to users (service key; becomes an internal function).",
    },
    {"name": "internal", "description": "Cron triggers for scheduled jobs (CRON_SECRET)."},
    {"name": "conversation-test", "description": "Chatbot test harness. Local only."},
]

SWAGGER_UI_PARAMETERS: dict[str, Any] = {
    # Keep the Authorize token across page reloads while testing.
    "persistAuthorization": True,
    "displayRequestDuration": True,
    "tryItOutEnabled": True,
    "filter": True,
}
