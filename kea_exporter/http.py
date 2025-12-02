from typing import Any, Optional

from urllib.parse import unquote, urlparse, urlunparse
import requests

from kea_exporter import DHCPVersion


class KeaHTTPClient:
    def __init__(
        self, target: str, client_cert: Optional[str], client_key: Optional[str], timeout: int = 10, **_kwargs: Any
    ) -> None:
        # kwargs allows passing additional arguments from CLI without breaking
        # this class
        """
        Create a KeaHTTPClient configured to communicate with a Kea server
        and initialize its module and subnet caches.

        Parameters:
            target (str): Kea server URL; may include embedded credentials
                (e.g. "https://user:pass@host:port"). Embedded credentials
                will be used for HTTP basic auth and removed from the
                effective request URL.
            client_cert (str | None): Path to the client TLS certificate
                file, or None to disable mutual TLS.
            client_key (str | None): Path to the client TLS key file, or
                None to disable mutual TLS.
            timeout (int | float): HTTP request timeout in seconds
                (default 10).
            **kwargs: Intentionally unused; allows Exporter to pass same
                arguments to both KeaHTTPClient and KeaSocketClient (e.g.,
                timeout, client_cert) without errors.

        Notes:
            Initializes internal state (modules, subnet maps) and triggers
            discovery of available modules and subnets.
        """
        super().__init__()

        # Parse URL to extract credentials
        parsed = urlparse(target)

        # Extract basic auth from URL if present
        if parsed.username and parsed.password:
            self._auth = (unquote(parsed.username), unquote(parsed.password))
            # Remove credentials from URL for actual requests and server ID
            netloc_without_auth = parsed.hostname
            if parsed.port:
                netloc_without_auth = f"{netloc_without_auth}:{parsed.port}"
            self._target = urlunparse(
                (parsed.scheme, netloc_without_auth, parsed.path, parsed.params, parsed.query, parsed.fragment)
            )
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
        """
        Discover available Kea services and populate self.modules.

        Queries the server configuration and fills self.modules with the
        detected service names. Prefers discovery via the Control-agent's
        `control-sockets` entry when present; otherwise inspects top-level
        service keys case-insensitively and adds any of "dhcp4", "dhcp6",
        or "ddns" as found. Treats the legacy key "d2" as "ddns".
        """
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
        control_sockets = config_args.get("Control-agent", {}).get("control-sockets", [])

        # control-sockets can be either a list (legacy) or a dict
        # (proper Kea API)
        if isinstance(control_sockets, dict):
            # Extract service names from dict keys
            modules = list(control_sockets.keys())
        else:
            # Use list as-is
            modules = control_sockets

        # Set modules if discovery succeeded
        if modules:
            self.modules = modules
        else:
            # Fallback for setups without Control Agent (Kea 2.7.2+ and
            # newer may not have it)
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
        """
        Load IPv4 and IPv6 subnet definitions for configured DHCP modules
        into the instance maps.

        Fetches configuration for any DHCP modules present in self.modules
        (only "dhcp4" and "dhcp6" are considered) and updates self.subnets
        with IPv4 subnet entries keyed by their `id` and self.subnets6 with
        IPv6 subnet entries keyed by their `id`. If no DHCP modules are
        configured, the method returns without modifying state.
        """
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
            # Skip non-dict responses (e.g., error strings)
            if not isinstance(module, dict):
                continue
            for subnet in module.get("arguments", {}).get("Dhcp4", {}).get("subnet4", []):
                self.subnets.update({subnet["id"]: subnet})
            for subnet in module.get("arguments", {}).get("Dhcp6", {}).get("subnet6", []):
                self.subnets6.update({subnet["id"]: subnet})

    def stats(self):
        # Reload subnets on update in case of configurational update
        """
        Fetch statistics from the Kea server and yield a record for each
        discovered module.

        Each yielded record corresponds to a module in the client's
        discovered module list and contains the server identifier, the
        module's DHCP version, the module-specific statistics/arguments,
        and the relevant subnet mapping. For the DDNS module the subnet
        mapping is an empty dict.

        Returns:
            iterator: Yields tuples of the form (server_id, dhcp_version,
                arguments, subnets) where
                - server_id (str): identifier for the Kea server (clean
                  target URL),
                - dhcp_version (DHCPVersion): enum value indicating DHCP4,
                  DHCP6, or DDNS,
                - arguments (dict): statistics/arguments returned by the
                  module,
                - subnets (dict): mapping of subnet id to subnet definition
                  (empty for DDNS).
        """
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
