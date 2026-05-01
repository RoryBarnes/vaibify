"""Tests for vaibify.docker.fbForwardedHostPortFree port-bind probe."""

import socket

from vaibify.docker import fbForwardedHostPortFree


def _fiBindEphemeralPort():
    """Bind an OS-assigned ephemeral port and return (port, socket)."""
    sockHolder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sockHolder.bind(("127.0.0.1", 0))
    iPort = sockHolder.getsockname()[1]
    return iPort, sockHolder


def test_fbForwardedHostPortFree_true_for_unbound_port():
    iPort, sockHolder = _fiBindEphemeralPort()
    sockHolder.close()
    assert fbForwardedHostPortFree(iPort) is True


def test_fbForwardedHostPortFree_false_when_port_held():
    iPort, sockHolder = _fiBindEphemeralPort()
    try:
        assert fbForwardedHostPortFree(iPort) is False
    finally:
        sockHolder.close()
