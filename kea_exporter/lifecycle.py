"""Track label combinations and decide when to remove unreported ones from the registry."""

import click
from prometheus_client import Gauge

from kea_exporter import DHCPVersion

Source = tuple[str, DHCPVersion]
_Ledger = dict[int, tuple[Gauge, dict[tuple[str, ...], Source]]]


class LabelLifecycle:
    """Manage label combinations across scrape cycles."""

    def __init__(self, stale_timeout: int = 0) -> None:
        self.stale_timeout = stale_timeout
        self._current: _Ledger = {}
        self._previous: _Ledger = {}
        self._last_success: dict[Source, float] = {}

    def record(self, gauge: Gauge, source: Source, label_values: tuple[str, ...]) -> None:
        """Record one label combination exported by a source in this cycle."""
        gauge_id = id(gauge)
        if gauge_id not in self._current:
            self._current[gauge_id] = (gauge, {})
        self._current[gauge_id][1][label_values] = source

    def end_cycle(self, scraped: set[Source], now: float) -> int:
        """Remove stale combinations only after a source responds or its timeout passes.

        This rule keeps live series through transient scrape failures. Returns
        the number of stale series removed from the registry.
        """
        removed = 0
        next_previous: _Ledger = {
            gauge_id: (gauge, dict(current)) for gauge_id, (gauge, current) in self._current.items()
        }

        for gauge_id, (gauge, previous) in self._previous.items():
            current = self._current.get(gauge_id, (gauge, {}))[1]
            for label_values, source in previous.items():
                if label_values in current:
                    continue
                timed_out = (
                    self.stale_timeout > 0
                    and source in self._last_success
                    and now - self._last_success[source] > self.stale_timeout
                )
                if source in scraped or timed_out:
                    try:
                        gauge.remove(*label_values)
                    except Exception as e:
                        click.echo(f"Unexpected error removing gauge label: {e}", err=True)
                        next_previous.setdefault(gauge_id, (gauge, {}))[1][label_values] = source
                    else:
                        removed += 1
                else:
                    next_previous.setdefault(gauge_id, (gauge, {}))[1][label_values] = source

        self._previous = next_previous
        self._current = {}
        for source in scraped:
            self._last_success[source] = now
        return removed
