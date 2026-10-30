"""The shared egress proxy: what its script refuses, where it binds, what it logs.

The proxy script is server-owned text executed inside a container.
Here it is executed into a namespace of its own (never as ``__main__``,
so nothing listens) and its decision functions are driven directly,
with a distinct address for every class the non-public check names.
The council's gateway test drives the composed argv; the live council
tests drive the proxy for real.
"""

import asyncio
import ipaddress
import json

import pytest

from vaibify.docker import egressProxy
from vaibify.gui import agentCouncilDockerGateway, agentCouncilEgress


@pytest.fixture
def dictScript():
    """The proxy script's namespace, imported but not run."""
    dictNamespace = {"__name__": "vaibifyEgressProxyUnderTest"}
    exec(compile(egressProxy.S_CONNECT_PROXY_SCRIPT, "vaibifyEgressProxy.py", "exec"),  # noqa: S102 -- the server-owned script, tested as the module it is
         dictNamespace)
    return dictNamespace


@pytest.mark.parametrize("sAddress", [
    "127.0.0.1", "::1",                      # loopback
    "10.0.0.1", "172.16.0.1", "192.168.1.1", "fd00::1",  # private
    "169.254.1.1", "fe80::1",                # link-local
    "169.254.169.254",                       # the cloud metadata service
    "100.64.0.1", "100.127.255.254",         # carrier-grade NAT
    "224.0.0.1", "0.0.0.0", "240.0.0.1",     # multicast, unspecified, reserved
])
def testEveryNonPublicAddressClassIsRecognized(dictScript, sAddress):
    assert dictScript["fbIsNonPublicAddress"](sAddress) is True


@pytest.mark.parametrize("sAddress", [
    "93.184.216.34", "151.101.1.69", "2606:4700::1111", "100.63.255.255",
    "100.128.0.1",
])
def testAPublicAddressIsNotRefused(dictScript, sAddress):
    assert dictScript["fbIsNonPublicAddress"](sAddress) is False


def _fsResolve(dictScript, sHost, listAnswers):
    """Run the script's resolver against a loop whose getaddrinfo is scripted."""
    async def fsRunResolve():
        async def fnAnswer(sHostAsked, iPort):
            return [(None, None, None, "", (sAnswer, iPort)) for sAnswer in listAnswers]

        asyncio.get_running_loop().getaddrinfo = fnAnswer
        return await dictScript["fsResolveAllowlistedHost"](sHost, 443)

    return asyncio.run(fsRunResolve())


@pytest.mark.falsification
def testAnAllowedNameResolvingToANonPublicAddressIsRefused(dictScript):
    """A name an attacker controls could point the proxy's bridge side at the host, the VM or the LAN.

    Kills: dialing whatever an allowed name resolves to.
    """
    with pytest.raises(dictScript["NonPublicResolution"]):
        _fsResolve(dictScript, "api.example.org", ["93.184.216.34", "10.0.0.5"])
    with pytest.raises(dictScript["NonPublicResolution"]):
        _fsResolve(dictScript, "api.example.org", ["169.254.169.254"])


def testAPublicResolutionIsDialedAndAPinnedPrivateAddressIsAServerDecision(dictScript):
    assert _fsResolve(dictScript, "api.example.org", ["93.184.216.34"]) == "93.184.216.34"
    dictScript["DICT_HOSTNAME_ADDRESS_MAP"]["host.docker.internal"] = "172.17.0.1"
    assert _fsResolve(dictScript, "host.docker.internal", []) == "172.17.0.1"


@pytest.mark.falsification
def testTheProxyBindsOnlyItsInternalAddress(dictScript, monkeypatch):
    """Bound to every interface, the proxy would serve the default bridge too.

    Kills: binding every interface regardless of the subnet it was given.
    """
    monkeypatch.setattr(
        dictScript["socket"], "getaddrinfo",
        lambda sHost, iPort, iFamily: [
            (None, None, None, "", ("172.17.0.9", 0)),
            (None, None, None, "", ("10.9.8.7", 0)),
        ])
    assert dictScript["fsSelectBindAddress"]("10.9.8.0/24") == "10.9.8.7"
    assert dictScript["fsSelectBindAddress"]("") == "0.0.0.0"
    with pytest.raises(SystemExit):
        dictScript["fsSelectBindAddress"]("192.0.2.0/24")


def testTheJudgementRefusesRawAddressesUnlistedHostsPortsAndTooManyTunnels(dictScript):
    dictScript["SET_ALLOWED_HOSTNAMES"].update({"api.example.org"})
    dictScript["SET_ALLOWED_PORTS"].update({443})
    fsJudge = dictScript["fsJudgeTarget"]
    assert asyncio.run(fsJudge("203.0.113.9", 443)) == "raw-address CONNECT target refused"
    assert asyncio.run(fsJudge("[::1]", 443)) == "raw-address CONNECT target refused"
    assert asyncio.run(fsJudge("other.example.org", 443)) == "host not on allowlist"
    assert asyncio.run(fsJudge("api.example.org", 8080)) == "port not on allowlist"
    assert asyncio.run(fsJudge("api.example.org", 443)) == ""
    dictScript["DICT_STATE"]["iOpenTunnels"] = dictScript["I_MAXIMUM_CONCURRENT_TUNNELS"]
    assert asyncio.run(fsJudge("api.example.org", 443)) == "too many open tunnels"


def testEveryDecisionIsLoggedInTheLineTheHubParses(dictScript, capsys):
    dictScript["fnLogDecision"]("api.example.org", 443, "allowed")
    dictScript["fnLogDecision"]("evil.example.org", 443, "refused", "host not on allowlist")
    listLines = capsys.readouterr().out.strip().splitlines()
    assert egressProxy.fdictParseConnectLogLine(listLines[0]) == {
        "sHost": "api.example.org", "iPort": 443, "bAllowed": True, "sReason": "",
    }
    assert egressProxy.fdictParseConnectLogLine(listLines[1]) == {
        "sHost": "evil.example.org", "iPort": 443, "bAllowed": False,
        "sReason": "host not on allowlist",
    }
    assert egressProxy.fdictParseConnectLogLine("vaibify-egress: listening on x") is None
    assert egressProxy.fdictParseConnectLogLine("vaibify-egress: CONNECT nonsense") is None


def testTheComposedCommandCarriesTheBindSubnetOnlyWhenGiven():
    listPlain = egressProxy.flistComposeProxyCommand(["api.example.org"], [443], {})
    assert "--bind-cidr" not in listPlain
    listBound = egressProxy.flistComposeProxyCommand(
        ["api.example.org"], [443], {}, sBindCidr="10.9.8.7/24")
    assert listBound[-2:] == ["--bind-cidr", "10.9.8.0/24"]
    with pytest.raises(ValueError):
        egressProxy.flistComposeProxyCommand(["api.example.org"], [443], {}, "not a network")


def testTheCouncilReExportsTheSharedValuesUnchanged():
    assert agentCouncilEgress.S_PROXY_IMAGE == egressProxy.S_PROXY_IMAGE
    assert agentCouncilEgress.S_CONNECT_PROXY_SCRIPT is egressProxy.S_CONNECT_PROXY_SCRIPT
    assert agentCouncilEgress.EgressSetupError is egressProxy.EgressSetupError
    assert agentCouncilEgress.flistBuildRunnerNetworkArguments("campaignAlpha1") == (
        egressProxy.flistBuildSealedNetworkArguments("vaibifyCouncilEgress-campaignAlpha1"))


def testTheSubnetReaderPrefersTheIPv4Subnet():
    class _Api:
        def inspect_network(self, sNetworkName):
            return {"IPAM": {"Config": [
                {"Subnet": "fd00:1::/64"}, {"Subnet": "10.9.8.0/24"},
            ]}}

    class _Daemon:
        api = _Api()

    assert agentCouncilDockerGateway._fsReadInternalNetworkSubnet(
        _Daemon(), "vaibifyCouncilEgress-x") == "10.9.8.0/24"


def testAnUnreadableSubnetRefusesTheLaunchRatherThanWideningTheBind():
    class _Api:
        def inspect_network(self, sNetworkName):
            return {"IPAM": {"Config": []}}

    class _Daemon:
        api = _Api()

    with pytest.raises(egressProxy.EgressSetupError, match="no IPv4 subnet"):
        agentCouncilDockerGateway._fsReadInternalNetworkSubnet(_Daemon(), "net")


def testTheDnsBlackHoleIsNeverRoutable():
    assert ipaddress.ip_address(egressProxy.S_BLACK_HOLE_NAMESERVER) in (
        ipaddress.ip_network("192.0.2.0/24"))
    assert egressProxy.flistBuildBlackHoleDnsArguments()[:2] == [
        "--dns", egressProxy.S_BLACK_HOLE_NAMESERVER]
    assert json.dumps(egressProxy.fdictBuildProxyEnvironment("10.9.8.7")).count(
        "http://10.9.8.7:8888") == 4
