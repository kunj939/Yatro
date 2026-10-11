"""Errors raised by the gateway. All are ProviderUnavailable, so every existing caller's fallback applies (I3)."""
from __future__ import annotations

from datetime import datetime

from app.core.errors import ProviderUnavailable


class GatewayError(ProviderUnavailable):
    """A call the gateway refused or could not complete. `reason` is for logs and degraded-plan notes; `retry_at` is when
    trying again is sensible (shown to the user as the time new plans reopen)."""
    reason = "provider_busy"

    def __init__(self, message: str, *, provider: str = "", endpoint: str = "", retry_at: datetime | None = None):
        super().__init__(message)
        self.provider, self.endpoint, self.retry_at = provider, endpoint, retry_at


class CircuitOpen(GatewayError):
    reason = "circuit_open"


class QuotaExhausted(CircuitOpen):
    reason = "quota"


class BudgetExhausted(GatewayError):
    reason = "budget"


class DeadlineExceeded(GatewayError):
    reason = "deadline"


class BulkheadFull(GatewayError):
    reason = "bulkhead"
