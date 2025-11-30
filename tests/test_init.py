"""
Tests for kea_exporter.__init__ module
"""
import unittest
from kea_exporter import DHCPVersion, __version__, __project__


class TestDHCPVersion(unittest.TestCase):
    """Test DHCPVersion enum"""

    def test_dhcp4_value(self):
        """Test DHCP4 enum value"""
        self.assertEqual(DHCPVersion.DHCP4.value, 1)

    def test_dhcp6_value(self):
        """Test DHCP6 enum value"""
        self.assertEqual(DHCPVersion.DHCP6.value, 2)

    def test_ddns_value(self):
        """Test DDNS enum value (new in this branch)"""
        self.assertEqual(DHCPVersion.DDNS.value, 3)

    def test_enum_members_count(self):
        """Test that DHCPVersion has exactly 3 members"""
        self.assertEqual(len(DHCPVersion), 3)

    def test_enum_member_names(self):
        """Test all enum member names are present"""
        member_names = [member.name for member in DHCPVersion]
        self.assertIn('DHCP4', member_names)
        self.assertIn('DHCP6', member_names)
        self.assertIn('DDNS', member_names)

    def test_enum_uniqueness(self):
        """Test that each enum member has a unique value"""
        values = [member.value for member in DHCPVersion]
        self.assertEqual(len(values), len(set(values)))

    def test_dhcp_version_comparison(self):
        """Test DHCPVersion enum comparison"""
        self.assertNotEqual(DHCPVersion.DHCP4, DHCPVersion.DHCP6)
        self.assertNotEqual(DHCPVersion.DHCP4, DHCPVersion.DDNS)
        self.assertNotEqual(DHCPVersion.DHCP6, DHCPVersion.DDNS)

    def test_dhcp_version_identity(self):
        """Test DHCPVersion enum identity"""
        self.assertIs(DHCPVersion.DHCP4, DHCPVersion.DHCP4)
        self.assertIs(DHCPVersion.DHCP6, DHCPVersion.DHCP6)
        self.assertIs(DHCPVersion.DDNS, DHCPVersion.DDNS)


class TestModuleConstants(unittest.TestCase):
    """Test module-level constants"""

    def test_project_name(self):
        """Test __project__ constant"""
        self.assertEqual(__project__, "kea-exporter")
        self.assertIsInstance(__project__, str)

    def test_version_format(self):
        """Test __version__ follows semantic versioning"""
        self.assertIsInstance(__version__, str)
        # Check it has at least major.minor.patch
        parts = __version__.split('.')
        self.assertGreaterEqual(len(parts), 3)
        # Check each part is numeric
        for part in parts:
            self.assertTrue(part.isdigit(), f"Version part '{part}' is not numeric")

    def test_version_current(self):
        """Test current version matches expected"""
        self.assertEqual(__version__, "0.7.0")


if __name__ == '__main__':
    unittest.main()