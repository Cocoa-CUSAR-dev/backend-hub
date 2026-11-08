from src.config import Environment, settings


def test_only_staging_and_production_are_deployed() -> None:
    assert Environment.LOCAL.is_deployed is False
    assert Environment.TESTING.is_deployed is False
    assert Environment.STAGING.is_deployed is True
    assert Environment.PRODUCTION.is_deployed is True


def test_settings_default_values() -> None:
    # DATABASE_URL/KOTLIN_BACKEND_URL/GO_BACKEND_URL have no default -- they
    # come from tests/conftest.py's os.environ.setdefault calls, required
    # before `import src.main` (and therefore this settings instance) works.
    assert settings.CORS_ORIGINS == ["http://localhost:5173"]
    assert settings.APP_VERSION == "0.1.0"
    assert settings.ENVIRONMENT == Environment.LOCAL


def test_api_docs_default_on_except_production() -> None:
    from src.config import Settings

    base = {"API_DOCS_ENABLED": None}
    assert settings.model_copy(update={**base, "ENVIRONMENT": Environment.STAGING}).docs_enabled
    assert not settings.model_copy(
        update={**base, "ENVIRONMENT": Environment.PRODUCTION}
    ).docs_enabled
    forced = settings.model_copy(
        update={"API_DOCS_ENABLED": True, "ENVIRONMENT": Environment.PRODUCTION}
    )
    assert isinstance(forced, Settings) and forced.docs_enabled
