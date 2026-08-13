"""Define the Kea names that identify each supported daemon."""

from dataclasses import dataclass

from kea_exporter import DHCPVersion


@dataclass(frozen=True)
class DaemonSpec:
    """Map one exporter daemon identity to its Kea control names."""

    service: str
    section: str | None = None
    subnet_key: str | None = None


DAEMON_SPECS: dict[DHCPVersion, DaemonSpec] = {
    DHCPVersion.DHCP4: DaemonSpec("dhcp4", "Dhcp4", "subnet4"),
    DHCPVersion.DHCP6: DaemonSpec("dhcp6", "Dhcp6", "subnet6"),
    DHCPVersion.DDNS: DaemonSpec("ddns"),
}

_DAEMON_BY_SERVICE = {spec.service: daemon for daemon, spec in DAEMON_SPECS.items()}
_DAEMON_BY_SERVICE["d2"] = DHCPVersion.DDNS


def daemon_for_service(service: str) -> DHCPVersion | None:
    """Return the supported daemon named by a Kea service value."""
    return _DAEMON_BY_SERVICE.get(service.lower())
