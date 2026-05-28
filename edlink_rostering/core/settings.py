"""Typed application settings.

Centralizes every environment variable the runtime reads. The previous
shape (scattered ``os.environ.get`` calls in ``engine.py``,
``dependencies.py``, ``auth.py``, ``dev.py``) made it hard to enumerate
the configuration surface; pulling it into one Pydantic model gives a
typed, documented, and (eventually) JSON-schema-able contract that the
deployment configuration validates against.

The variable names match the existing ``.env``/``.env.example`` so this
is a drop-in migration: no rename, no breakage in dev or test. The
KEYVAULT__* secret-prefix convention is owned by
:class:`edlink_rostering.infrastructure.azure_mocks.key_vault.KeyVaultClient`
and not duplicated here.

Tests that mutate environment variables after first calling
:func:`get_settings` must invoke ``get_settings.cache_clear()`` (the
``tests/fixtures/auth.py::ensure_test_secret`` helper does this for the
dev JWT secret).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or ``.env``.

    Variable resolution order (highest to lowest priority):

    1. Process environment (``os.environ``)
    2. ``.env`` file at the project root
    3. Pydantic field default

    Fields named in lower_snake_case map case-insensitively to the
    upper-snake-case env vars the project's ``.env.example`` documents.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database URLs. APP for the sync worker (edlink_app role), OPS for
    # the operator CLI and the HTTP surface (edlink_ops role). In dev
    # both point at the same Postgres. DATABASE_URL is the legacy
    # fall-through that some test harnesses still set.
    app_database_url: str | None = None
    ops_database_url: str | None = None
    database_url: str | None = None
    migration_database_url: str | None = None

    # Dev-only HS256 signing secret. None outside the dev profile.
    # Production swaps the validator to JWKS-backed RS256 once the IdP
    # is selected and this field stays empty.
    dev_jwt_secret: str | None = None

    # Profile gate. ``dev`` mounts the persona-switcher and test-event
    # routes. Any other value (or unset) keeps them returning 404.
    EDLINK_PROFILE: str = ""

    # ── Production provider config ────────────────────────────────────
    # These fields are None in dev (the mock implementations activate
    # instead). Setting any of them switches the corresponding
    # dependency factory in api/dependencies.py to the real provider.

    # Azure Key Vault (secret store)
    azure_keyvault_url: str | None = None

    # Azure Application Insights (telemetry)
    app_insights_connection_string: str | None = None

    # Azure Service Bus (message bus)
    service_bus_connection_string: str | None = None
    service_bus_queue_name: str | None = None

    # JWT / IdP (authentication)
    jwks_url: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None

    # Deterministic dev-server ports. PORT_API is the FastAPI admin
    # server; PORT_WEB is the Vite dev server. Both derive from
    # EDLINK_PORT_BASE in scripts/_lib.sh (PORT_API = BASE,
    # PORT_WEB = BASE + 1). The values are read at app composition so
    # CORS allow_origins can target the actual Vite port without
    # hardcoding 5173. Defaults match .env.example.
    EDLINK_PORT_BASE: int = 8000
    port_api: int = 8000
    port_web: int = 8001

    def app_db_url(self) -> str:
        """Resolved sync-worker URL with ``DATABASE_URL`` fallback."""

        url = self.app_database_url or self.database_url
        if not url:
            raise RuntimeError(
                "Set APP_DATABASE_URL (or DATABASE_URL) to a Postgres"
                " async URL: postgresql+psycopg://user:pass@host:5432/db"
            )
        return url

    def ops_db_url(self) -> str:
        """Resolved CLI/HTTP URL with ``DATABASE_URL`` fallback."""

        url = self.ops_database_url or self.database_url
        if not url:
            raise RuntimeError(
                "Set OPS_DATABASE_URL (or DATABASE_URL) to a Postgres"
                " async URL: postgresql+psycopg://user:pass@host:5432/db"
            )
        return url

    def is_dev_profile(self) -> bool:
        return self.EDLINK_PROFILE == "dev"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide Settings instance.

    Cached for the lifetime of the process; tests that mutate env vars
    after first call must invoke ``get_settings.cache_clear()`` to pick
    up the change.
    """

    return Settings()


__all__ = ["Settings", "get_settings"]
