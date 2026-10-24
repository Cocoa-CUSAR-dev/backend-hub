"""SSO token for the diary card's "view full history" deep link (US2-6,
docs-and-plan#133).

Minted here, in-process, by src/auth (no HTTP call to web-backend any more).
The web app redeems it at backend-hub's POST /api/v1/auth/sso/exchange:
15 minutes, single use, never usable as an API token. The chatbot already
knows this is the right user_id (it just generated their diary), so minting
isn't a new trust decision, just proof of one already made.
"""

from uuid import UUID

from src.auth import service as auth_service


async def mint_sso_token(user_id: str | UUID) -> str:
    return auth_service.sso_mint(user_id if isinstance(user_id, UUID) else UUID(str(user_id)))
