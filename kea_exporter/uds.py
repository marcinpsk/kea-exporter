import json
import os
import socket
import sys

import click

from kea_exporter import DHCPVersion


class KeaSocketClient:
    def __init__(self, sock_path, **kwargs):
        """
        Initialize the KeaSocketClient with a Unix domain socket path and validate access.
        
        Parameters:
            sock_path (str): Path to the Unix domain socket used to communicate with the Kea server.
        
        Description:
            Validates that the socket exists and is readable/writable, stores the absolute socket path,
            and initializes internal state (version, config, subnets, subnet_missing_info_sent, dhcp_version).
            The absolute socket path is also recorded as the client/server identifier.
        
        Raises:
            FileNotFoundError: If no socket exists at `sock_path`.
            PermissionError: If the socket exists but is not readable and writable by the current process.
        """
        super().__init__()

        if not os.access(sock_path, os.F_OK):
            raise FileNotFoundError(f"Unix domain socket does not exist at {sock_path}")
        if not os.access(sock_path, os.R_OK | os.W_OK):
            raise PermissionError(f"No read/write permissions on Unix domain socket at {sock_path}")

        self.sock_path = os.path.abspath(sock_path)
        self._server_id = self.sock_path  # Use socket path as server identifier

        self.version = None
        self.config = None
        self.subnets = None
        self.subnet_missing_info_sent = []
        self.dhcp_version = None

    def query(self, command):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(self.sock_path)
            sock.send(bytes(json.dumps({"command": command}), "utf-8"))
            response = json.loads(sock.makefile().read(-1))

        if response["result"] != 0:
            raise ValueError

        return response

    def stats(self):
        # I don't currently know how to detect a changed configuration, so
        # unfortunately we're reloading more often now as a workaround.
        """
        Reloads the Kea configuration and yields current server statistics together with the subnet map.
        
        Yields:
            tuple: (server_id, dhcp_version, arguments, subnets)
                - server_id (str): Absolute path identifying the Unix domain socket server.
                - dhcp_version (DHCPVersion): Detected DHCP version for the server.
                - arguments (dict): Statistic values returned by the server's "statistic-get-all" command.
                - subnets (dict): Mapping from subnet ID to subnet configuration objects.
        """
        self.reload()

        arguments = self.query("statistic-get-all").get("arguments", {})

        yield self._server_id, self.dhcp_version, arguments, self.subnets

    def reload(self):
        """
        Refresh stored configuration from the Kea socket, update DHCP version, and build the subnet map.
        
        Queries the server for the current configuration and stores the response's "arguments" in self.config. If a "Dhcp4" section is present, sets self.dhcp_version to DHCPVersion.DHCP4 and uses its "subnet4" list; if a "Dhcp6" section is present, sets self.dhcp_version to DHCPVersion.DHCP6 and uses its "subnet6" list. Populates self.subnets as a dictionary mapping each subnet's "id" to the subnet object. If neither Dhcp4 nor Dhcp6 is present, writes an error to stderr and exits the process with status 1.
        """
        self.config = self.query("config-get")["arguments"]

        if "Dhcp4" in self.config:
            self.dhcp_version = DHCPVersion.DHCP4
            subnets = self.config["Dhcp4"]["subnet4"]
        elif "Dhcp6" in self.config:
            self.dhcp_version = DHCPVersion.DHCP6
            subnets = self.config["Dhcp6"]["subnet6"]
        else:
            click.echo(
                f"Socket {self.sock_path} has no supported configuration",
                file=sys.stderr,
            )
            sys.exit(1)

        # create subnet map
        self.subnets = {subnet["id"]: subnet for subnet in subnets}