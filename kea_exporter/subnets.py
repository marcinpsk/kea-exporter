"""Provide subnet indexing shared by the Kea adapters."""

SubnetIndex = dict[int, dict]


def subnet_index(section: dict, subnet_key: str) -> SubnetIndex:
    """Index subnets by ID from the top level and every shared network."""
    indexed = {subnet["id"]: subnet for subnet in section.get(subnet_key, []) if "id" in subnet}
    for network in section.get("shared-networks", []):
        indexed.update({subnet["id"]: subnet for subnet in network.get(subnet_key, []) if "id" in subnet})
    return indexed
