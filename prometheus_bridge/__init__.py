"""Bridge from Athena to the Prometheus copy-trader.

Prometheus already runs the full ``SmartSignal → risk.check → execute``
pipeline; what it lacks is curated, validated watchlist data. Athena's
job here is to package recent watchlist trades into the ``smart_money``
shape Prometheus expects and POST them to ``/internal/push``.

Public API:

- :func:`push_smart_money` — send the latest watchlist + signal payload
"""

from prometheus_bridge.client import push_smart_money

__all__ = ["push_smart_money"]
