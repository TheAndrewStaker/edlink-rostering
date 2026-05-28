"""Shared FastAPI dependencies.

Three dependency factories live here:

1. ``get_session_factory`` returns the lru-cached async session factory
   the routers use to open Postgres sessions. The DB URL comes from
   ``OPS_DATABASE_URL`` (falling back to ``DATABASE_URL``), matching
   the operator CLI.

2. ``get_telemetry`` returns a process-wide telemetry facade so
   endpoints can emit structured events into the same stream the sync
   worker uses.

3. ``get_key_vault`` returns the process-wide secret store.

4. ``get_jwt_validator`` returns the JWT validator for the current
   profile.

All factories return protocol types from
``edlink_rostering.infrastructure.ports``. The concrete implementation
is selected by the runtime profile (``EDLINK_PROFILE``). Production
adds a branch for the real provider; callers never change.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from edlink_rostering.core.settings import get_settings
from edlink_rostering.infrastructure.ports import (
    JWTValidator,
    SecretStore,
    TelemetryFacade,
)


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        get_settings().ops_db_url(), echo=False, pool_pre_ping=True
    )
    return async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )


@lru_cache(maxsize=1)
def get_telemetry() -> TelemetryFacade:
    """Process-wide telemetry facade.

    Dev profile: stdout + file sinks via the in-memory Telemetry class.
    Production: replace the else branch with the real provider (e.g.
    ``AzureMonitorTelemetry(connection_string=...)``).
    """

    settings = get_settings()
    if settings.is_dev_profile() or not settings.app_insights_connection_string:
        from edlink_rostering.infrastructure.azure_mocks.app_insights import (
            Telemetry,
        )

        return Telemetry()

    # Production branch (activate by setting APP_INSIGHTS_CONNECTION_STRING):
    # from edlink_rostering.infrastructure.azure.app_insights import (
    #     AzureMonitorTelemetry,
    # )
    # return AzureMonitorTelemetry(
    #     connection_string=settings.app_insights_connection_string,
    # )
    from edlink_rostering.infrastructure.azure_mocks.app_insights import (
        Telemetry,
    )

    return Telemetry()


@lru_cache(maxsize=1)
def get_key_vault() -> SecretStore:
    """Process-wide secret store.

    Dev profile: in-memory mock that loads ``KEYVAULT__*`` env vars.
    Production: replace the else branch with the real provider (e.g.
    ``AzureKeyVaultClient(vault_url=...)``).
    """

    settings = get_settings()
    if settings.is_dev_profile() or not settings.azure_keyvault_url:
        from edlink_rostering.infrastructure.azure_mocks.key_vault import (
            KeyVaultClient,
        )

        return KeyVaultClient()

    # Production branch (activate by setting AZURE_KEYVAULT_URL):
    # from edlink_rostering.infrastructure.azure.key_vault import (
    #     AzureKeyVaultSecretStore,
    # )
    # return AzureKeyVaultSecretStore(vault_url=settings.azure_keyvault_url)
    from edlink_rostering.infrastructure.azure_mocks.key_vault import (
        KeyVaultClient,
    )

    return KeyVaultClient()


@lru_cache(maxsize=1)
def get_jwt_validator() -> JWTValidator:
    """JWT validator for the current profile.

    Dev profile: HS256 against ``DEV_JWT_SECRET``.
    Production: JWKS-backed RS256 lookup against the configured IdP.
    """

    settings = get_settings()
    if settings.is_dev_profile() or not settings.jwks_url:
        from edlink_rostering.api.auth import DevJWTValidator

        secret = settings.dev_jwt_secret or ""
        return DevJWTValidator(secret=secret)

    # Production branch (activate by setting JWKS_URL):
    # from edlink_rostering.infrastructure.azure.auth import JWKSValidator
    # return JWKSValidator(
    #     jwks_url=settings.jwks_url,
    #     issuer=settings.jwt_issuer,
    #     audience=settings.jwt_audience,
    # )
    from edlink_rostering.api.auth import DevJWTValidator

    secret = settings.dev_jwt_secret or ""
    return DevJWTValidator(secret=secret)


__all__ = [
    "get_jwt_validator",
    "get_key_vault",
    "get_session_factory",
    "get_telemetry",
]
