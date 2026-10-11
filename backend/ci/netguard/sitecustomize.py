"""CI network guard: no Python process started with this directory on PYTHONPATH can open a connection to anything but this machine.

The verification workflow (.github/workflows/verify.yml) puts this on PYTHONPATH for every step that runs our code, so even a bug that
slipped past demo mode could not reach Google, OpenRouter, a rail API or anything else. PostgreSQL and Redis are service containers
published on localhost, so they still work. Each blocked attempt is raised as an error and appended to $NETGUARD_LOG.
Python loads `sitecustomize` by itself at start-up; nothing imports this file.
"""
import ipaddress
import os
import socket
import time

_LOG = os.environ.get("NETGUARD_LOG")


def _is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if host in ("", "localhost", "0.0.0.0", "::", "::1"):
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False  # a name that is not "localhost": by now it should already be an address, so refuse


def _guard(sock, address) -> None:
    if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and not _is_local(address[0]):
        if _LOG:
            try:
                with open(_LOG, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} pid={os.getpid()} blocked connection to {address[0]}:{address[1]}\n")
            except OSError:
                pass
        raise OSError(f"outbound connection to {address[0]}:{address[1]} blocked by the CI network guard")


_connect, _connect_ex = socket.socket.connect, socket.socket.connect_ex


def connect(self, address):
    _guard(self, address)
    return _connect(self, address)


def connect_ex(self, address):
    _guard(self, address)
    return _connect_ex(self, address)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
