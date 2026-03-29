import json
import os
import socket
from contextlib import closing

from kea_exporter import DHCPVersion


class KeaConfigError(Exception):
    pass


class KeaSocketClient:
    def __init__(self, sock_path: str, **kwargs) -> None:
        # **kwargs intentionally unused; allows Exporter to pass same
        # arguments to both KeaHTTPClient and KeaSocketClient (e.g.,
        # timeout, client_cert) without errors
        """
        Initialize the KeaSocketClient with a Unix domain socket path and
        validate access.

        Parameters:
            sock_path (str): Path to the Unix domain socket used to
                communicate with the Kea server.

        Description:
            Validates that the socket exists and is readable/writable,
            stores the absolute socket path, and initializes internal state
            (version, config, subnets, subnet_missing_info_sent,
            dhcp_version). The absolute socket path is also recorded as the
            client/server identifier.

        Raises:
            FileNotFoundError: If no socket exists at `sock_path`.
            PermissionError: If the socket exists but is not readable and
                writable by the current process.
        """
        super().__init__()

        if not os.access(sock_path, os.F_OK):
            raise FileNotFoundError(f"Unix domain socket does not exist at {sock_path}")
        if not os.access(sock_path, os.R_OK | os.W_OK):
            raise PermissionError(f"No read/write permissions on Unix domain socket at {sock_path}")

        self.sock_path = os.path.abspath(sock_path)
        # Use socket path as server identifier
        self._server_id = self.sock_path
        self.timeout = kwargs.get("timeout", 10)

        self.version = None
        self.config = None
        self.subnets = None
        self.subnet_missing_info_sent = set()
        self.dhcp_version = None

    def query(self, command):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(self.sock_path)
            sock.send(bytes(json.dumps({"command": command}), "utf-8"))
            with closing(sock.makefile()) as f:
                try:
                    response = json.loads(f.read())
                except json.JSONDecodeError as e:
                    raise ValueError(f"Kea returned invalid JSON on '{command}': {e}") from e

        if response["result"] != 0:
            raise ValueError(response.get("text") or f"Query '{command}' failed with result {response['result']}")

        return response

    def stats(self):
        # I don't currently know how to detect a changed configuration, so
        # unfortunately we're reloading more often now as a workaround.
        """
        Yield current server statistics and subnet mapping after
        reloading the configuration.

        Yields:
            tuple: (server_id, dhcp_version, arguments, subnets)
                - server_id (str): Unix domain socket path.
                - dhcp_version (DHCPVersion): Detected DHCP version.
                - arguments (dict): Statistics from statistic-get-all.
                - subnets (dict): Subnet ID to subnet config mapping.
        """
        self.reload()

        arguments = self.query("statistic-get-all").get("arguments", {})

        yield self._server_id, self.dhcp_version, arguments, self.subnets

    def reload(self):
        """
        Refresh the client's configuration from the Kea server and update
        the DHCP version and subnet mapping.

        Retrieves the server configuration and stores its "arguments" in
        self.config. Sets self.dhcp_version to DHCPVersion.DHCP4 when a
        "Dhcp4" section is present (using its "subnet4" list) or to
        DHCPVersion.DHCP6 when a "Dhcp6" section is present (using its
        "subnet6" list). Populates self.subnets as a dictionary mapping
        each subnet's "id" to the subnet object.

        Raises:
            KeaConfigError: If neither "Dhcp4" nor "Dhcp6" is found in
                the configuration.
        """
        self.config = self.query("config-get")["arguments"]

        if "Dhcp4" in self.config:
            self.dhcp_version = DHCPVersion.DHCP4
            subnets = self.config["Dhcp4"].get("subnet4", [])
            self.subnets = {subnet["id"]: subnet for subnet in subnets}
            for network in self.config["Dhcp4"].get("shared-networks", []):
                for subnet in network.get("subnet4", []):
                    self.subnets[subnet["id"]] = subnet
        elif "Dhcp6" in self.config:
            self.dhcp_version = DHCPVersion.DHCP6
            subnets = self.config["Dhcp6"].get("subnet6", [])
            self.subnets = {subnet["id"]: subnet for subnet in subnets}
            for network in self.config["Dhcp6"].get("shared-networks", []):
                for subnet in network.get("subnet6", []):
                    self.subnets[subnet["id"]] = subnet
        else:
            raise KeaConfigError(f"Socket {self.sock_path} has no supported configuration")
