"""Local mocks for Azure runtime surfaces.

Every class here exposes the subset of the matching Azure SDK interface that
the application code actually uses. The swap from mock to real Azure is a
configuration change (which class gets instantiated in api/dependencies.py),
not a refactor of calling code.

Production code should import protocols and shared types from
``edlink_rostering.infrastructure.ports`` for type hints, and receive
concrete instances via FastAPI ``Depends``. Direct imports from this
package are reserved for tests and the DI factory layer.

Coverage policy: a method is mocked only if the application calls it.
Out-of-scope SDK methods raise NotImplementedError with a pointer to which
ticket would add them. That way the gap is obvious, not silently absent.
"""

from edlink_rostering.infrastructure.azure_mocks.app_insights import (
    MemorySink,
    Telemetry,
)
from edlink_rostering.infrastructure.azure_mocks.function_context import FunctionContext
from edlink_rostering.infrastructure.azure_mocks.key_vault import (
    KeyVaultClient,
    KeyVaultSecret,
    SecretNotFound,
)
from edlink_rostering.infrastructure.ports import (
    SecretValue,
    TelemetryRecord,
    TelemetrySink,
)
from edlink_rostering.infrastructure.azure_mocks.service_bus import (
    ServiceBusClient,
    ServiceBusMessage,
    ServiceBusSessionReceiver,
)

__all__ = [
    "FunctionContext",
    "KeyVaultClient",
    "KeyVaultSecret",
    "MemorySink",
    "SecretNotFound",
    "SecretValue",
    "ServiceBusClient",
    "ServiceBusMessage",
    "ServiceBusSessionReceiver",
    "Telemetry",
    "TelemetryRecord",
    "TelemetrySink",
]
