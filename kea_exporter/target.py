"""The interface Exporter needs from a Kea target.

Two adapters ship, KeaHTTPClient and KeaSocketClient, and the tests add a
third. Declaring the seam here keeps the three in step, and stops Exporter
reaching across it for a private attribute to name a target by.
"""

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from kea_exporter import DHCPVersion

# What one daemon reported in one scrape.
StatsRow = tuple[str, DHCPVersion, dict, dict]


@runtime_checkable
class KeaTarget(Protocol):
    """A source of Kea statistics.

    Construction records the address and performs no I/O. A target that is
    unreachable at start-up is therefore a target whose stats() raises, which
    the scrape loop already survives, rather than a second kind of thing the
    Exporter has to keep in its target list.
    """

    @property
    def server_id(self) -> str:
        """Names the target in logs, and in the `server` label of every series."""

    def stats(self) -> Iterator[StatsRow]:
        """Yield one row per daemon this target reports for."""
