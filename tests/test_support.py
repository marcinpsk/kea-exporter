"""The test doubles themselves.

A double that quietly substitutes its own data would make the tests that use
it assert against a configuration the test never set up.
"""

from kea_exporter import DHCPVersion
from tests.support import InMemoryTarget


def test_an_empty_subnet_mapping_is_the_one_the_test_passed():
    """`subnets or {}` swaps an empty mapping for a different object.

    A test that fills the mapping after add() would then scrape an empty one.
    """
    subnets = {}
    target = InMemoryTarget().add(DHCPVersion.DHCP4, {}, subnets)

    subnets[1] = {"id": 1, "subnet": "10.0.0.0/24"}

    _server_id, _version, _arguments, scraped = next(iter(target.stats()))
    assert scraped is subnets, "add() stored a different mapping than the test passed"


def test_omitting_the_subnet_mapping_still_yields_one():
    target = InMemoryTarget().add(DHCPVersion.DHCP4, {})

    assert next(iter(target.stats()))[3] == {}
