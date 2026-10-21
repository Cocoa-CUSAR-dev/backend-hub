from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class LineConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    LINE_CHANNEL_SECRET: str
    LINE_CHANNEL_ACCESS_TOKEN: str
    LIFF_ID: str = ""
    # US2-6 (docs-and-plan#133): base URL for the diary card's "view full
    # history" button. src/sso/client.py mints a short-lived token appended
    # as ?token=... so the link opens web-app already logged in (see
    # _generate_and_push_diary in router.py) -- this is a separate,
    # lightweight token-mint/exchange flow, not the LIFF-based
    # identity-linking already owned by mobile-backend (ADR 0002).
    WEB_APP_URL: str = ""
    # US2-13 (docs-and-plan#242): which farms and plots a farmer is offered.
    # "farmer" (production) offers only the farms they're linked to through
    # agriculture.farmer_farm, and only those farms' plots -- Kotlin sends the
    # global ref.farm_constant / ref.plot_constant lists, i.e. everyone's.
    # "all" offers every farm and plot, for dev/test accounts that aren't
    # linked to any farm and would otherwise see an empty list.
    PLOT_SCOPE_MODE: Literal["farmer", "all"] = "farmer"


line_settings = LineConfig()  # type: ignore[call-arg]
