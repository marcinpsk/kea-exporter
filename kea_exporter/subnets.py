"""Provide subnet indexing shared by the Kea adapters."""

from kea_exporter import DHCPVersion

DAEMON_SECTIONS: dict[DHCPVersion, tuple[str, str]] = {
    DHCPVersion.DHCP4: ("Dhcp4", "subnet4"),
    DHCPVersion.DHCP6: ("Dhcp6", "subnet6"),
}


def subnet_index(section: dict, subnet_key: str) -> dict[int, dict]:
    """Index subnets by ID from the top level and every shared network."""
    indexed = {subnet["id"]: subnet for subnet in section.get(subnet_key, []) if "id" in subnet}
    for network in section.get("shared-networks", []):
        indexed.update({subnet["id"]: subnet for subnet in network.get(subnet_key, []) if "id" in subnet})
    return indexed
