"""Provider-agnostic protocols and shared types for infrastructure services.

Production code imports types and protocols from here. Concrete implementations
(azure_mocks for dev, real Azure SDK / AWS / GCP for production) satisfy these
protocols. The factory functions in api/dependencies.py choose which
implementation to instantiate based on the runtime profile.

The goal: swapping from dev mocks to a production provider is a one-file change
in dependencies.py, not a 15-file import refactor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


# ── Secret Store ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SecretValue:
    """A secret retrieved from the secret store.

    Shape matches ``azure.keyvault.secrets.KeyVaultSecret`` (name + value).
    """

    name: str
    value: str


class SecretNotFound(KeyError):
    """Raised when a secret does not exist in the store.

    Subclasses ``KeyError`` so the retry policy's exception classification
    treats it as a permanent failure (not retryable).
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        return f"Secret {self.name!r} not found in Key Vault."


@runtime_checkable
class SecretStore(Protocol):
    """Provider-agnostic secret store.

    Dev: ``KeyVaultClient`` (in-memory, env-var-backed).
    Production: Azure Key Vault ``SecretClient``, AWS Secrets Manager, etc.
    """

    def get_secret(self, name: str) -> SecretValue: ...


# ── Telemetry ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TelemetryRecord:
    """One emitted telemetry event.

    ``kind`` is ``"event" | "metric" | "exception"``. ``properties`` carries
    string key-value pairs. ``measurements`` carries numeric metrics. Both
    match Application Insights conventions.
    """

    kind: str
    name: str
    occurred_at: datetime
    properties: dict[str, str] = field(default_factory=dict)
    measurements: dict[str, float] = field(default_factory=dict)


class TelemetrySink(Protocol):
    """Pluggable destination for telemetry records.

    Dev: ``StdoutSink``, ``FileSink``, ``MemorySink``.
    Production: Azure Monitor exporter, Datadog, etc.
    """

    def emit(self, record: TelemetryRecord) -> None: ...


@runtime_checkable
class TelemetryFacade(Protocol):
    """Provider-agnostic telemetry facade.

    Dev: stdout + file sinks via ``Telemetry`` class.
    Production: Azure Monitor, Datadog, etc., implementing the same three
    methods.
    """

    def track_event(
        self,
        name: str,
        properties: dict[str, str] | None = None,
        measurements: dict[str, float] | None = None,
    ) -> None: ...

    def track_metric(self, name: str, value: float) -> None: ...

    def track_exception(
        self,
        exc: BaseException,
        properties: dict[str, str] | None = None,
    ) -> None: ...


# ── JWT Validation ───────────────────────────────────────────────────────────


class JWTValidationError(Exception):
    """Raised by a ``JWTValidator`` when a token cannot be validated.

    Carries a human-readable ``detail`` for the HTTP 401 response body.
    """

    def __init__(self, detail: str, *, expired: bool = False) -> None:
        super().__init__(detail)
        self.detail = detail
        self.expired = expired


@runtime_checkable
class JWTValidator(Protocol):
    """Provider-agnostic JWT validator.

    Dev: HS256 against ``DEV_JWT_SECRET``.
    Production: JWKS-backed RS256 lookup once the IdP is selected
    (Azure Entra, Auth0, Okta, etc.).

    Implementations raise ``JWTValidationError`` on any validation failure
    (expired, bad signature, missing claims). The calling dependency
    translates that to an HTTP 401.
    """

    def decode(self, token: str) -> dict[str, object]: ...


__all__ = [
    "JWTValidationError",
    "JWTValidator",
    "SecretNotFound",
    "SecretStore",
    "SecretValue",
    "TelemetryFacade",
    "TelemetryRecord",
    "TelemetrySink",
]
