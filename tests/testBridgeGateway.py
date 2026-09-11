"""The bridge gateway comes from the daemon, and not knowing is ``""``.

A Linux container reaches the host at the default bridge network's
gateway. The resolver asks the daemon rather than reading ``docker0``,
so a renamed default bridge still answers correctly; and every way of
not knowing collapses to one empty answer, so the launcher can degrade
to loopback and SAY so instead of binding an address nobody confirmed.
"""

from types import SimpleNamespace
from unittest.mock import patch

from vaibify.docker import bridgeGateway


class _FakeNetworks:
    def __init__(self, dictAttributes=None, errorToRaise=None):
        self._dictAttributes = dictAttributes
        self._errorToRaise = errorToRaise
        self.listNamesAsked = []

    def get(self, sName):
        self.listNamesAsked.append(sName)
        if self._errorToRaise is not None:
            raise self._errorToRaise
        return SimpleNamespace(attrs=self._dictAttributes)


def _fdockerFake(dictAttributes=None, errorToRaise=None):
    return SimpleNamespace(
        networks=_FakeNetworks(dictAttributes, errorToRaise),
    )


def _fsResolveAgainst(dockerFake, errorFromEnv=None):
    """Resolve with the SDK constructor answering ``dockerFake``."""
    dictSeen = {}

    def fdockerFromEnv(timeout=None):
        dictSeen["iTimeout"] = timeout
        if errorFromEnv is not None:
            raise errorFromEnv
        return dockerFake

    with patch("docker.from_env", side_effect=fdockerFromEnv):
        sGateway = bridgeGateway.fsResolveDockerBridgeGateway(5)
    dictSeen["sGateway"] = sGateway
    return dictSeen


def test_the_default_bridge_gateway_is_returned():
    dockerFake = _fdockerFake({
        "IPAM": {"Config": [
            {"Subnet": "172.17.0.0/16", "Gateway": "172.17.0.1"},
        ]},
    })
    dictSeen = _fsResolveAgainst(dockerFake)
    assert dictSeen["sGateway"] == "172.17.0.1"
    assert dockerFake.networks.listNamesAsked == [
        bridgeGateway.S_DEFAULT_BRIDGE_NETWORK,
    ]


def test_the_timeout_reaches_the_sdk_client():
    """A hung daemon must not cost the hub the SDK's one-minute default."""
    dictSeen = _fsResolveAgainst(_fdockerFake({}))
    assert dictSeen["iTimeout"] == 5


def test_an_ipv6_only_gateway_is_not_an_answer():
    """The container dials IPv4; an IPv6 gateway would bind a dead socket."""
    dockerFake = _fdockerFake({
        "IPAM": {"Config": [
            {"Subnet": "fd00::/64", "Gateway": "fd00::1"},
        ]},
    })
    assert _fsResolveAgainst(dockerFake)["sGateway"] == ""


def test_the_ipv4_entry_wins_whatever_its_position():
    dockerFake = _fdockerFake({
        "IPAM": {"Config": [
            {"Subnet": "fd00::/64", "Gateway": "fd00::1"},
            {"Subnet": "10.9.0.0/24", "Gateway": "10.9.0.1"},
        ]},
    })
    assert _fsResolveAgainst(dockerFake)["sGateway"] == "10.9.0.1"


def test_every_way_of_not_knowing_is_empty():
    assert _fsResolveAgainst(
        None, errorFromEnv=RuntimeError("no daemon socket"),
    )["sGateway"] == ""
    assert _fsResolveAgainst(
        _fdockerFake(errorToRaise=RuntimeError("daemon down")),
    )["sGateway"] == ""
    assert _fsResolveAgainst(_fdockerFake({}))["sGateway"] == ""
    assert _fsResolveAgainst(
        _fdockerFake({"IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]}}),
    )["sGateway"] == ""
    assert _fsResolveAgainst(_fdockerFake({"IPAM": None}))["sGateway"] == ""
