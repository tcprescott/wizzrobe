"""Application metrics shipped to Sentry Metrics (Explore > Metrics in Sentry).

Three sources feed it:

* :mod:`application.utils.metrics_sampler` reads :func:`runtime_stats.collect`
  every :data:`SAMPLE_SECONDS` and sends the process figures as gauges (loop
  lag, CPU, memory, open tabs, in-flight requests, DB pool, queue depths, watch
  page audience).
* ``middleware/request_metrics.py`` counts and times every HTTP request by its
  route template, so a spike names the page that caused it.
* :class:`~application.utils.background_loop.BackgroundLoop` times every worker
  tick.

Every call is a no-op until :func:`init_sentry` turns metrics on (``SENTRY_DSN``
set and ``SENTRY_METRICS`` not ``off``), and none of them ever raises: a metric
is never worth breaking the request or tick that produced it.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from sentry_sdk import metrics as _metrics

SAMPLE_SECONDS = 30.0

_enabled = False


def metrics_requested() -> bool:
    """Whether ``SENTRY_METRICS`` leaves metrics on (default: on)."""
    return os.environ.get('SENTRY_METRICS', '').strip().lower() not in ('0', 'false', 'no', 'off')


def configure(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


def enabled() -> bool:
    return _enabled


def gauge(name: str, value: Optional[float], unit: Optional[str] = None,
          attributes: Optional[Dict[str, Any]] = None) -> None:
    if not _enabled or value is None:
        return
    try:
        _metrics.gauge(name, value, unit=unit, attributes=attributes)
    except Exception:
        pass


def count(name: str, value: float = 1, unit: Optional[str] = None,
          attributes: Optional[Dict[str, Any]] = None) -> None:
    if not _enabled or not value:
        return
    try:
        _metrics.count(name, value, unit=unit, attributes=attributes)
    except Exception:
        pass


def distribution(name: str, value: Optional[float], unit: Optional[str] = None,
                 attributes: Optional[Dict[str, Any]] = None) -> None:
    if not _enabled or value is None:
        return
    try:
        _metrics.distribution(name, value, unit=unit, attributes=attributes)
    except Exception:
        pass
