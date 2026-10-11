"""Single-flight coalescing (ADR 0007): identical calls in flight at the same time share one answer.

Off by default (GATEWAY_SINGLE_FLIGHT=false): duplicates were not measured in Phase 0. Off or on, every duplicate that arrives
while an identical call is in flight is counted in the ledger ("coalescible"), which is the number that justifies turning it on."""
from __future__ import annotations

import hashlib
import json
import threading

from app.gateway.ledger import ledger


def key_of(method: str, url: str, kwargs: dict) -> str:
    keep = {k: v for k, v in kwargs.items() if k in ("params", "json", "data", "content")}
    return hashlib.sha1(f"{method} {url} {json.dumps(keep, sort_keys=True, default=str)}".encode()).hexdigest()


class SingleFlight:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[str, dict] = {}

    def run(self, key: str, provider: str, endpoint: str, fn, enabled: bool, wait: float = 30.0):
        with self._lock:
            flight = self._flights.get(key)
            if flight is None:
                flight = self._flights[key] = {"event": threading.Event(), "result": None, "error": None, "n": 1}
                leader = True
            else:
                flight["n"] += 1
                leader = False
        if not leader:
            ledger.add(provider, endpoint, coalescible=1)
            if enabled and flight["event"].wait(wait):
                if flight["error"] is not None:
                    raise flight["error"]
                return flight["result"]
            return fn()  # not coalescing (or the leader took too long): make our own call
        try:
            flight["result"] = fn()
            return flight["result"]
        except Exception as exc:
            flight["error"] = exc
            raise
        finally:
            with self._lock:
                self._flights.pop(key, None)
            flight["event"].set()


single_flight = SingleFlight()
