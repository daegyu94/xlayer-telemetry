"""Read a small opt-in query catalogue from already packaged dashboard assets.

This is not a Grafana query client. Canonical expression, histogram guard and
native unit remain owned by the existing dashboard JSON.
"""
from __future__ import annotations

import json
import re
from typing import Mapping


MAX_DASHBOARD_BYTES = 2 * 1024 * 1024
_UNITS = {"s": {"seconds"}, "Bps": {"bytes/s"}, "ops": {"keys/s", "requests/s"}, "bytes": {"bytes"}}
_MACROS = re.compile(r"\$(?:\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*)")


def borrow_mooncake_queries(references: Mapping[str, tuple[int, str, str]], *, cluster: bool) -> dict[str, str]:
    """Resolve only fixed panel/ref selections; fail closed on contract drift."""
    # Import/resolve assets only when this optional profile is selected. The
    # established resolver supports checkout, wheel, --user and --target installs.
    from ..operations.config import assets_root

    try:
        path = assets_root() / "examples/dashboards/agent-rl-stages.json"
        with path.open("rb") as stream:
            body = stream.read(MAX_DASHBOARD_BYTES + 1)
    except (OSError, ValueError) as error:
        raise ValueError("Canonical Mooncake dashboard asset is unavailable; reinstall runtime assets") from error
    if len(body) > MAX_DASHBOARD_BYTES:
        raise ValueError("Canonical Mooncake dashboard exceeds 2 MiB limit")
    try:
        document = json.loads(body)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid canonical Mooncake dashboard JSON") from error
    if not isinstance(document, dict) or not isinstance(document.get("panels"), list):
        raise ValueError("Invalid canonical Mooncake dashboard panels")
    index = {}
    stack = [(p, 0) for p in document["panels"]]
    while stack:
        row, depth = stack.pop()
        if not isinstance(row, dict) or depth > 8 or len(index) >= 1000:
            raise ValueError("Invalid or excessive canonical dashboard nesting")
        identity = row.get("id")
        if type(identity) is not int or identity in index:
            raise ValueError("Duplicate or invalid canonical dashboard panel ID")
        index[identity] = row
        children = row.get("panels", [])
        if not isinstance(children, list):
            raise ValueError("Invalid canonical child panels")
        stack.extend((child, depth + 1) for child in children)

    queries = {}
    for signal, (panel_id, ref_id, expected_unit) in references.items():
        row = index.get(panel_id, {})
        fields = row.get("fieldConfig", {})
        defaults = fields.get("defaults", {}) if isinstance(fields, dict) else {}
        native_unit = defaults.get("unit") if isinstance(defaults, dict) else None
        if expected_unit not in _UNITS.get(native_unit, set()):
            raise ValueError(f"Canonical Mooncake unit contract changed for {signal}")
        targets = row.get("targets", [])
        if not isinstance(targets, list) or any(not isinstance(t, dict) for t in targets):
            raise ValueError("Invalid canonical query targets")
        matches = [t.get("expr") for t in targets if t.get("refId") == ref_id]
        if len(matches) != 1 or not isinstance(matches[0], str) or not matches[0].strip():
            raise ValueError(f"Canonical Mooncake panel/ref contract changed for {signal}")
        expression = matches[0]
        if set(_MACROS.findall(expression)) - {"$cluster", "$node", "$engine", "$__rate_interval"}:
            raise ValueError(f"Unsupported canonical query macro for {signal}")
        # Diagnosis context is literal identity, not Grafana regex selection.
        # escape_label later protects PromQL string syntax, not regex semantics.
        if cluster:
            expression = expression.replace('cluster=~"$cluster"', 'cluster="{cluster}"')
        else:
            expression = expression.replace('cluster=~"$cluster",', '')
        expression = expression.replace('node=~"$node"', 'node="{rollout_node}"')
        expression = expression.replace('=~"$engine"', '=~".*"')
        expression = expression.replace("$__rate_interval", "1m")
        if "$" in expression:
            raise ValueError(f"Unresolved canonical query macro for {signal}")
        queries[signal] = expression
    return queries
