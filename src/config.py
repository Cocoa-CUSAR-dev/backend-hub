"""Every setting this service reads, in one place (CONVENTIONS.md §9).

Nothing else in src/ reads environment variables -- import `settings` from
here instead. A new env var goes into this class, into .env.sample, and into
the team's env registry sheet in the same PR.
"""

from enum import StrEnum
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"
    TESTING = "testing"

    @property
    def is_deployed(self) -> bool:
        return self in (Environment.STAGING, Environment.PRODUCTION)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- app ------------------------------------------------------------
    ENVIRONMENT: Environment = Environment.LOCAL
    APP_VERSION: str = "0.1.0"
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]
    # Swagger UI (/docs), ReDoc (/redoc) and /openapi.json. Unset means "on
    # everywhere except production"; set true/false to force it.
    API_DOCS_ENABLED: bool | None = None
    # Set by Vercel itself on every deployment. Only main.py's lifespan reads
    # it: the in-process scheduler can't run on serverless (see
    # src/jobs/router.py), so it is skipped there.
    VERCEL: str = ""

    # ---- database -------------------------------------------------------
    DATABASE_URL: str

    # ---- auth (src/auth, CONVENTIONS.md §5) -----------------------------
    # Signs every access token. Must be the same value web-backend and
    # mobile-backend use while they still run, so they accept hub tokens.
    JWT_KEY: str
    # Access tokens are short because roles live inside them; clients refresh
    # automatically. The refresh token slides: each use issues a new one with
    # a fresh lifetime, so a farmer who opens the app at least once every
    # REFRESH_TOKEN_TTL_DAYS never has to type the password again.
    ACCESS_TOKEN_TTL_SECONDS: int = 3600
    REFRESH_TOKEN_TTL_DAYS: int = 90
    # One-time token in the LINE diary card's "open the web app" link.
    SSO_TOKEN_TTL_SECONDS: int = 900
    # Researchers sign themselves up with this code (given out by the team).
    # Empty disables researcher self sign-up.
    RESEARCHER_SIGNUP_CODE: str = ""

    # ---- old backends (until the merge is finished) ---------------------
    KOTLIN_BACKEND_URL: str
    GO_BACKEND_URL: str
    # Outbound keys this service presents to Kotlin / Go. Must match each
    # backend's own CHATBOT_SERVICE_KEY -- see web-backend's ServiceKeyFilter
    # and mobile-backend's service_auth_middleware.go.
    KOTLIN_SERVICE_KEY: str
    GO_SERVICE_KEY: str
    # Inbound key: first-party callers (web-backend) present this as the
    # X-Service-Key header on /service/* routes here.
    CHATBOT_SERVICE_KEY: str

    # ---- jobs -----------------------------------------------------------
    # Gates /internal/cron/* -- the external trigger (cron-job.org, a manual
    # curl) must send `Authorization: Bearer <CRON_SECRET>`.
    CRON_SECRET: str

    # ---- LINE -----------------------------------------------------------
    LINE_CHANNEL_SECRET: str
    LINE_CHANNEL_ACCESS_TOKEN: str
    LIFF_ID: str = ""
    # LINE Login channels whose LIFF ID tokens may sign in through
    # POST /api/v1/auth/line/*, besides the one LIFF_ID belongs to -- e.g.
    # the mobile app's own LIFF link page if it lives on another channel.
    # JSON list in .env: LINE_LOGIN_CHANNEL_IDS=["1234567890"]
    LINE_LOGIN_CHANNEL_IDS: list[str] = []
    # US2-6 (docs-and-plan#133): base URL for the diary card's "view full
    # history" button; src/sso/client.py mints the one-time #token=.
    WEB_APP_URL: str = ""
    # US2-13 (docs-and-plan#242): "farmer" (production) offers only the farms
    # a farmer is linked to through agriculture.farmer_farm, and only those
    # farms' plots. "all" offers every farm and plot, for dev/test accounts
    # that aren't linked to any farm.
    PLOT_SCOPE_MODE: Literal["farmer", "all"] = "farmer"

    # ---- LLM ------------------------------------------------------------
    # LiteLLM model string, e.g. "gpt-4o-mini" or "gemini/gemini-1.5-flash".
    # Switching providers is this one string, not code (ADR 0004).
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_API_KEY: str = ""
    # A farmer is waiting in LINE for the reply, so a slow provider is treated
    # the same as an unavailable one (LLMUnavailable) rather than waited on.
    LLM_TIMEOUT_SECONDS: float = 6.0
    # The daily diary's polish is pushed to LINE after the farmer's reply, so it
    # may take longer; past this (with two retries) the plain template is used.
    DIARY_LLM_TIMEOUT_SECONDS: float = 20.0
    # US2-11 kill switch: False makes intent.classify return UNKNOWN without
    # calling the provider, reverting free text to the fixed hint message.
    INTENT_ROUTING_ENABLED: bool = True
    # Below this self-reported confidence a message is treated as UNKNOWN.
    INTENT_MIN_CONFIDENCE: float = 0.6

    # ---- object storage (Cloudflare R2, S3-compatible) -------------------
    # Farm photos. Leave any of the four blank and uploads answer 503 while
    # everything else keeps working (same as mobile-backend did).
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = ""

    # ---- error tracking (X-2d) ------------------------------------------
    # Blank DSN disables the SDK entirely. No separate SENTRY_ENVIRONMENT:
    # main.py passes ENVIRONMENT.value straight through.
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0

    @property
    def r2_configured(self) -> bool:
        return all(
            (self.R2_ACCOUNT_ID, self.R2_ACCESS_KEY_ID, self.R2_SECRET_ACCESS_KEY, self.R2_BUCKET)
        )

    @property
    def docs_enabled(self) -> bool:
        if self.API_DOCS_ENABLED is not None:
            return self.API_DOCS_ENABLED
        return self.ENVIRONMENT != Environment.PRODUCTION


settings = Settings()  # type: ignore[call-arg]
