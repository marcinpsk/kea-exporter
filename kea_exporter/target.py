"""The interface Exporter needs from a Kea target.

Two adapters ship, KeaHTTPClient and KeaSocketClient, and the tests add a
third. Declaring the seam here keeps the three in step, and stops Exporter
reaching across it for a private attribute to name a target by.
"""

from collections.abc import Iterator
from typing import NamedTuple, Protocol, runtime_checkable

from kea_exporter import DHCPVersion


class KeaCommandError(ValueError):
    """Kea returned a nonzero result for a control command."""


class SourceStatistics(NamedTuple):
    """Statistics returned by one source in one scrape cycle."""

    server_id: str
    daemon: DHCPVersion
    arguments: dict
    subnets: dict


class SourceFailure(NamedTuple):
    """A command failure returned by one source in one scrape cycle."""

    server_id: str
    daemon: DHCPVersion
    error: KeaCommandError


SourceResult = SourceStatistics | SourceFailure


@runtime_checkable
class KeaTarget(Protocol):
    """A source of Kea statistics.

    Construction records the address and performs no I/O. A target that is
    unreachable at start-up is therefore a target whose stats() raises, which
    the scrape loop already survives, rather than a second kind of thing the
    Exporter has to keep in its target list. A daemon command failure returns
    SourceFailure so another daemon on the same target can still succeed.
    """

    @property
    def server_id(self) -> str:
        """Names the target in logs, and in the `server` label of every series."""

    def stats(self) -> Iterator[SourceResult]:
        """Yield one success or failure result per daemon this target reports for."""
