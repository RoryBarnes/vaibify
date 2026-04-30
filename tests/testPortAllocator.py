"""Tests for vaibify.cli.portAllocator."""

import socket

import pytest


def _sockBindOn(iPort):
    """Bind a socket on 127.0.0.1:iPort and return it (caller closes)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", iPort))
    return sock


def test_fbIsPortFree_true_when_unbound():
    from vaibify.cli.portAllocator import fbIsPortFree
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iPort = sock.getsockname()[1]
    sock.close()
    assert fbIsPortFree(iPort) is True


def test_fbIsPortFree_false_when_bound():
    from vaibify.cli.portAllocator import fbIsPortFree
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iPort = sock.getsockname()[1]
    try:
        assert fbIsPortFree(iPort) is False
    finally:
        sock.close()


def test_fiPickFreePort_returns_preferred_when_free():
    from vaibify.cli.portAllocator import fiPickFreePort
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iPreferred = sock.getsockname()[1]
    sock.close()
    assert fiPickFreePort(iPreferred=iPreferred, iMaxAttempts=5) == iPreferred


def test_fiPickFreePort_shifts_when_preferred_bound():
    from vaibify.cli.portAllocator import fiPickFreePort
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iBound = sock.getsockname()[1]
    try:
        iResolved = fiPickFreePort(iPreferred=iBound, iMaxAttempts=20)
        assert iResolved != iBound
        assert iBound < iResolved <= iBound + 19
    finally:
        sock.close()


def test_fiPickFreePort_raises_when_no_port_free():
    from vaibify.cli.portAllocator import fiPickFreePort
    sockets = []
    try:
        sockFirst = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sockFirst.bind(("127.0.0.1", 0))
        iStart = sockFirst.getsockname()[1]
        sockets.append(sockFirst)
        for iOffset in range(1, 3):
            sockExtra = _sockBindOn(iStart + iOffset)
            sockets.append(sockExtra)
        with pytest.raises(RuntimeError, match="No free TCP port"):
            fiPickFreePort(iPreferred=iStart, iMaxAttempts=3)
    except OSError:
        pytest.skip("Could not bind contiguous ports for this test.")
    finally:
        for sock in sockets:
            sock.close()


def test_fiResolvePort_returns_explicit_unchanged():
    from vaibify.cli.portAllocator import fiResolvePort
    assert fiResolvePort(9999) == 9999


def test_fiResolvePort_autopicks_when_none(capsys):
    from vaibify.cli.portAllocator import fiResolvePort
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iPreferred = sock.getsockname()[1]
    sock.close()
    assert fiResolvePort(None, iPreferred=iPreferred) == iPreferred


def test_fiResolvePort_announces_fallback_on_stderr(capsys):
    from vaibify.cli.portAllocator import fiResolvePort
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    iBound = sock.getsockname()[1]
    try:
        iResolved = fiResolvePort(None, iPreferred=iBound)
        assert iResolved != iBound
        sErr = capsys.readouterr().err
        assert f"Port {iBound} in use" in sErr
    finally:
        sock.close()
