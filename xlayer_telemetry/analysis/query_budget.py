"""Per-analysis backend request budget, without threads or persistent breakers."""
from __future__ import annotations

from dataclasses import replace
import time
from urllib.error import HTTPError

from ..measurements import finite_number


class QueryBudgetExceeded(TimeoutError):
    pass


class BackendUnavailable(ConnectionError):
    pass


class QueryBudget:
    def __init__(self, seconds=30, *, clock=None):
        if finite_number(seconds) is None or seconds <= 0:
            raise ValueError("query_budget_seconds must be finite and positive")
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.seconds = seconds
        self.sources = {}

    def wrap(self, client, name, *, configurable_timeout=False):
        budget = self
        state = self.sources.setdefault(name, dict(attempted=0, failed=0, skipped=0, unavailable=False))

        class Client:
            def __getattr__(self, attribute):
                method = getattr(client, attribute)
                if attribute not in {"query_range", "query_range_detail", "query_window", "query_counters",
                                     "query_distribution_series", "query_counter_series"}:
                    return method

                def query(*args, **kwargs):
                    remaining = budget.seconds - (budget.clock() - budget.started)
                    if remaining <= 0:
                        state["skipped"] += 1
                        raise QueryBudgetExceeded("backend query budget exhausted")
                    if state["unavailable"]:
                        state["skipped"] += 1
                        raise BackendUnavailable("backend unavailable in this analysis")
                    target = replace(client, timeout=min(client.timeout, remaining)) if configurable_timeout else client
                    state["attempted"] += 1
                    try:
                        return getattr(target, attribute)(*args, **kwargs)
                    except (OSError, RuntimeError, ValueError) as error:
                        state["failed"] += 1
                        # Invalid PromQL/JSON must not suppress unrelated queries.
                        if isinstance(error, OSError) and (not isinstance(error, HTTPError) or error.code == 429 or error.code >= 500):
                            state["unavailable"] = True
                        raise
                return query
        return Client()

    def summary(self):
        elapsed = max(0, self.clock() - self.started)
        return {"budget_seconds": self.seconds, "elapsed_seconds": elapsed,
                "budget_exhausted": elapsed >= self.seconds,
                "sources": {name: dict(state) for name, state in self.sources.items()}}
