"""Cross-module integration layer for healthcare AI platform.

Provides:
- EventBus: publish/subscribe for inter-module real-time communication
- ServiceClient: typed HTTP client for calling other module APIs
- SimClock: single-clock simulation-time abstraction
- CircuitBreaker: per-service failure gate (HSE cyber resilience)
- CensusDebouncer / GenericDebouncer: rate-limit noisy pushes
- ConversationBuffer: Clinical Chat session memory
- FHIRMapper: convert internal data models to HL7 FHIR R4 resources (TODO)
"""

from shared.integration.circuit_breaker import (
    BreakerState,
    CircuitBreaker,
    CircuitOpenError,
)
from shared.integration.conversation_buffer import (
    ConversationBuffer,
    ConversationTurn,
    get_conversation_buffer,
)
from shared.integration.debouncer import CensusDebouncer, GenericDebouncer
from shared.integration.event_bus import EventBus
from shared.integration.service_client import ServiceClient
from shared.integration.sim_clock import SimClock, get_sim_time, is_sim_running

__all__ = [
    "BreakerState",
    "CensusDebouncer",
    "CircuitBreaker",
    "CircuitOpenError",
    "ConversationBuffer",
    "ConversationTurn",
    "EventBus",
    "GenericDebouncer",
    "ServiceClient",
    "SimClock",
    "get_conversation_buffer",
    "get_sim_time",
    "is_sim_running",
]
