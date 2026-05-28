"""EdLink connector.

Implements the Connector protocol against the EdLink Events API
(https://ed.link/docs/api/v1.0/graph/events). In production this is a real
HTTP client; in the POC, the client is fixture-backed so the connector can
be exercised end-to-end without partner credentials.

Per the design at docs/design/edlink-oneroster-rostering.md.
"""

from edlink_rostering.connectors.edlink.client import (
    EdLinkClient,
    EdLinkEvent,
    EdLinkEventsResponse,
)
from edlink_rostering.connectors.edlink.connector import EdLinkConnector

__all__ = [
    "EdLinkClient",
    "EdLinkConnector",
    "EdLinkEvent",
    "EdLinkEventsResponse",
]
