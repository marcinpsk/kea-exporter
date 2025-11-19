from sys import modules
from urllib.parse import urlparse, urlunparse
import requests

from kea_exporter import DHCPVersion


class KeaHTTPClient:
    def __init__(self, target, client_cert, client_key, timeout=10, **kwargs):
        super().__init__()

        # Parse URL to extract credentials
        parsed = urlparse(target)

        # Extract basic auth from URL if present
        if parsed.username and parsed.password:
            self._auth = (parsed.username, parsed.password)
            # Remove credentials from URL for actual requests and server ID
            netloc_without_auth = parsed.hostname
            if parsed.port:
                netloc_without_auth = f"{netloc_without_auth}:{parsed.port}"
            self._target = urlunparse((
                parsed.scheme,
                netloc_without_auth,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment
            ))
            # Use clean URL (without credentials) as server identifier
            self._server_id = self._target
        else:
            self._target = target
            self._server_id = target
            self._auth = None

        if client_cert and client_key:
            self._cert = (
                client_cert,
                client_key,
            )
        else:
            self._cert = None

        self.timeout = timeout
        self.modules = []
        self.subnets = {}
        self.subnets6 = {}

        self.load_modules()
        self.load_subnets()

    def load_modules(self):
        r = requests.post(
            self._target,
            cert=self._cert,
            auth=self._auth,
            json={"command": "config-get"},
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        config = r.json()
        config_args = config[0].get("arguments", {})

        # Try Control Agent discovery first (legacy)
        modules = config_args.get("Control-agent", {}).get("control-sockets", [])

        # Fallback for setups without Control Agent (Kea 2.7.2+ and newer may not have it)
        if not modules:
            # Normalize keys to lowercase for case-insensitive detection
            lower_args = {k.lower(): v for k, v in config_args.items()}
            for service in ["dhcp4", "dhcp6", "ddns", "d2"]:
                if service in lower_args:
                    # Normalize d2 to ddns
                    if service == "d2":
                        self.modules.append("ddns")
                    else:
                        self.modules.append(service)

    def load_subnets(self):
        # Only load subnets for DHCP services (DDNS doesn't have subnets)
        dhcp_modules = [m for m in self.modules if m in ["dhcp4", "dhcp6"]]
        if not dhcp_modules:
            return

        r = requests.post(
            self._target,
            cert=self._cert,
            auth=self._auth,
            json={"command": "config-get", "service": dhcp_modules},
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        config = r.json()
        for module in config:
            for subnet in module.get("arguments", {}).get("Dhcp4", {}).get("subnet4", {}):
                self.subnets.update({subnet["id"]: subnet})
            for subnet in module.get("arguments", {}).get("Dhcp6", {}).get("subnet6", {}):
                self.subnets6.update({subnet["id"]: subnet})

    def stats(self):
        # Reload subnets on update in case of configurational update
        self.load_subnets()
        # Note for future testing: pipe curl output to jq for an easier read
        r = requests.post(
            self._target,
            cert=self._cert,
            auth=self._auth,
            json={
                "command": "statistic-get-all",
                "arguments": {},
                "service": self.modules,
            },
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        response = r.json()

        for index, module in enumerate(self.modules):
            if module == "dhcp4":
                dhcp_version = DHCPVersion.DHCP4
                subnets = self.subnets
            elif module == "dhcp6":
                dhcp_version = DHCPVersion.DHCP6
                subnets = self.subnets6
            elif module == "ddns":
                dhcp_version = DHCPVersion.DDNS
                subnets = {}  # DDNS doesn't have subnets
            else:
                continue

            arguments = response[index].get("arguments", {})

            yield self._server_id, dhcp_version, arguments, subnets
