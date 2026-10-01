"""Offline tests of the council runner's pure half and the egress values.

``agentCouncilRunner`` holds the containment vocabulary, the limit and
sandbox validation, the tar ownership-stamping discipline and the
socket pumps that drive an already-opened exec stream. Its live suite
needs a daemon; everything here is pure, so it is driven directly with
real tarballs and a scripted socket double standing in for the exec
stream (the only external boundary). ``agentCouncilEgress`` composes the
proxy argv and the runner network arguments from server-owned values;
the refusals are what keep a crafted allowlist out of a container.
"""

import io
import socket
import tarfile
import time

import pytest

from vaibify.gui import agentCouncilEgress, agentCouncilRunner
from vaibify.gui.agentCouncilEgress import EgressSetupError


I_COUNCIL_UID = 1000
I_COUNCIL_GID = 1000


# ----- helpers -------------------------------------------------------------


def fbaBuildTarball(listMembers):
    """Return tar bytes from ``(name, kind, payload)`` member tuples.

    ``kind`` is "file", "directory", "symlink" or "hardlink"; the payload
    is file bytes for a file and the link target for a link. Members are
    written owned by root (uid 0) so the repack's stamping is observable.
    """
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        for sName, sKind, payload in listMembers:
            infoMember = tarfile.TarInfo(name=sName)
            infoMember.uid = 0
            infoMember.gid = 0
            infoMember.uname = "root"
            infoMember.gname = "root"
            if sKind == "file":
                infoMember.size = len(payload)
                fileTar.addfile(infoMember, io.BytesIO(payload))
                continue
            if sKind == "directory":
                infoMember.type = tarfile.DIRTYPE
            elif sKind == "symlink":
                infoMember.type = tarfile.SYMTYPE
                infoMember.linkname = payload
            else:
                infoMember.type = tarfile.LNKTYPE
                infoMember.linkname = payload
            fileTar.addfile(infoMember)
    return bufferTar.getvalue()


def fdictReadTarMembers(baTar):
    """Return ``{name: (uid, gid, uname, gname, content-or-None)}``."""
    dictMembers = {}
    with tarfile.open(fileobj=io.BytesIO(baTar), mode="r:*") as fileTar:
        for infoMember in fileTar:
            baContent = None
            if infoMember.isreg():
                baContent = fileTar.extractfile(infoMember).read()
            dictMembers[infoMember.name] = (
                infoMember.uid, infoMember.gid, infoMember.uname,
                infoMember.gname, baContent)
    return dictMembers


def fbaFrame(baPayload, iStreamType=1):
    """Return one Docker multiplexed-stream frame around a payload."""
    return (bytes([iStreamType, 0, 0, 0])
            + len(baPayload).to_bytes(4, "big") + baPayload)


class ScriptedExecSocket:
    """An already-opened exec socket double replaying scripted events.

    Each script entry is bytes to return from ``recv``, or an exception
    instance to raise. An exhausted script answers ``b""`` (the far end
    closed). ``send`` accepts at most ``iSendChunkBytes`` per call and can
    raise ``socket.timeout`` on scripted calls, which is the partial-send
    behavior a real non-blocking socket shows.
    """

    def __init__(self, listReceiveScript=None, iSendChunkBytes=65536,
                 setSendTimeoutCalls=None):
        self.listReceiveScript = list(listReceiveScript or [])
        self.iSendChunkBytes = iSendChunkBytes
        self.setSendTimeoutCalls = set(setSendTimeoutCalls or ())
        self.iSendCalls = 0
        self.baSent = b""

    def recv(self, iMaximumBytes):
        if not self.listReceiveScript:
            return b""
        valueNext = self.listReceiveScript.pop(0)
        if isinstance(valueNext, BaseException):
            raise valueNext
        return valueNext

    def send(self, baChunk):
        self.iSendCalls += 1
        if self.iSendCalls in self.setSendTimeoutCalls:
            raise socket.timeout("scripted send timeout")
        baAccepted = baChunk[:self.iSendChunkBytes]
        self.baSent += baAccepted
        return len(baAccepted)


def fdictLimitsWithTmpfsBytes(iWorkingTreeBytes, iScratchBytes,
                              iMemoryBytes):
    """Return a runner limit set with explicit tmpfs and memory sizes."""
    return {"fCpuCount": 1.0, "iMemoryBytes": iMemoryBytes,
            "iPidsLimit": 64, "iWorkingTreeBytes": iWorkingTreeBytes,
            "iScratchBytes": iScratchBytes}


# ----- runner limits and the sandbox contract ------------------------------


def testRunnerLimitsRefuseTmpfsThatReachesTheMemoryLimit():
    """A writable surface as large as memory would make disk an OOM kill."""
    dictLimits = fdictLimitsWithTmpfsBytes(600, 400, 1000)
    with pytest.raises(ValueError) as excInfo:
        agentCouncilRunner.fdictComposeRunnerCreateSpecification(
            "sha256:imageAlpha", "reservation-1", dictLimits=dictLimits)
    assert "(1000 bytes)" in str(excInfo.value)
    assert "memory limit" in str(excInfo.value)


def testRunnerLimitsAdmitTmpfsJustBelowTheMemoryLimit():
    dictLimits = fdictLimitsWithTmpfsBytes(600, 399, 1000)
    dictSpecification = (
        agentCouncilRunner.fdictComposeRunnerCreateSpecification(
            "sha256:imageAlpha", "reservation-1", dictLimits=dictLimits))
    dictKeywords = dictSpecification["dictCreateKeywords"]
    assert dictKeywords["mem_limit"] == 1000
    assert dictKeywords["memswap_limit"] == 1000
    assert dictKeywords["tmpfs"]["/council"].startswith("size=600,")
    assert dictKeywords["tmpfs"]["/tmp"] == "size=399,mode=1777"


def testSandboxRefusesAnyNetwork():
    with pytest.raises(ValueError) as excInfo:
        agentCouncilRunner.fdictComposeRunnerCreateSpecification(
            "sha256:imageAlpha", "reservation-1", bSandbox=True,
            sNetworkName="vaibifyCouncilEgress-campaignAlpha")
    assert "no network at all" in str(excInfo.value)


@pytest.mark.parametrize("dictWidening", [
    {"dictEnvironment": {"HTTPS_PROXY": "http://10.0.0.2:8888"}},
    {"listDnsServers": ["192.0.2.1"]},
    {"listDnsOptions": ["timeout:1"]},
])
def testSandboxRefusesEnvironmentOrResolver(dictWidening):
    with pytest.raises(ValueError) as excInfo:
        agentCouncilRunner.fdictComposeRunnerCreateSpecification(
            "sha256:imageAlpha", "reservation-1", bSandbox=True,
            **dictWidening)
    assert "carries no credential" in str(excInfo.value)


def testSandboxSpecificationCarriesNoNetworkAndItsOwnRole():
    dictSpecification = (
        agentCouncilRunner.fdictComposeRunnerCreateSpecification(
            "sha256:imageAlpha", "reservation-7", bSandbox=True,
            sResourceName="projectAlpha"))
    dictKeywords = dictSpecification["dictCreateKeywords"]
    assert dictSpecification["sRole"] == agentCouncilRunner.S_ROLE_SANDBOX
    assert dictSpecification["sContainerName"].startswith(
        "vaibifyCouncilSandbox")
    assert dictKeywords["network_mode"] == "none"
    assert dictKeywords["environment"] is None
    assert dictKeywords["dns"] is None and dictKeywords["dns_opt"] is None
    assert dictKeywords["labels"] == {
        agentCouncilRunner.S_COUNCIL_LABEL: "reservation-7",
        agentCouncilRunner.S_COUNCIL_ROLE_LABEL: "sandbox",
        agentCouncilRunner.S_COUNCIL_RESOURCE_LABEL: "projectAlpha",
    }


def testRunnerSpecificationWiresTheEgressNetworkAndResolver():
    dictSpecification = (
        agentCouncilRunner.fdictComposeRunnerCreateSpecification(
            "sha256:imageAlpha", "reservation-2",
            sNetworkName="vaibifyCouncilEgress-campaignAlpha",
            dictEnvironment={"HTTPS_PROXY": "http://10.0.0.2:8888"},
            listDnsServers=("192.0.2.1",),
            listDnsOptions=("timeout:1", "attempts:1")))
    dictKeywords = dictSpecification["dictCreateKeywords"]
    assert dictKeywords["network_mode"] == (
        "vaibifyCouncilEgress-campaignAlpha")
    assert dictKeywords["dns"] == ["192.0.2.1"]
    assert dictKeywords["dns_opt"] == ["timeout:1", "attempts:1"]
    assert dictKeywords["cap_drop"] == ["ALL"]
    assert dictKeywords["read_only"] is True
    assert dictKeywords["user"] == f"{I_COUNCIL_UID}:{I_COUNCIL_GID}"


# ----- the snapshot repack -------------------------------------------------


def testRepackStampsEveryMemberToTheCouncilUserAndKeepsContent():
    baSource = fbaBuildTarball([
        ("stepAlpha", "directory", None),
        ("stepAlpha/dataFile.csv", "file", b"column\n1\n"),
        ("stepAlpha/latest.csv", "symlink", "dataFile.csv"),
        ("stepAlpha/copy.csv", "hardlink", "stepAlpha/dataFile.csv"),
    ])
    bufferRepacked = agentCouncilRunner.fbufferRepackSnapshotStamped(
        baSource)
    dictMembers = fdictReadTarMembers(bufferRepacked.getvalue())
    assert set(dictMembers) == {
        "stepAlpha", "stepAlpha/dataFile.csv", "stepAlpha/latest.csv",
        "stepAlpha/copy.csv"}
    for tMember in dictMembers.values():
        assert tMember[:4] == (I_COUNCIL_UID, I_COUNCIL_GID, "", "")
    assert dictMembers["stepAlpha/dataFile.csv"][4] == b"column\n1\n"


@pytest.mark.parametrize("sEscapingName", [
    "../outsideFile.txt", "/etc/absoluteFile.txt",
    "stepAlpha/../../outsideFile.txt",
])
def testRepackRefusesAMemberEscapingTheRoot(sEscapingName):
    baSource = fbaBuildTarball([(sEscapingName, "file", b"x")])
    with pytest.raises(ValueError) as excInfo:
        agentCouncilRunner.fbufferRepackSnapshotStamped(baSource)
    assert "escapes the extraction root" in str(excInfo.value)
    assert repr(sEscapingName) in str(excInfo.value)


@pytest.mark.parametrize("sKind,sTarget", [
    ("symlink", "/etc/passwd"),
    ("symlink", "../../outsideFile.txt"),
    ("hardlink", "../../outsideFile.txt"),
])
def testRepackRefusesALinkTargetingOutsideTheRoot(sKind, sTarget):
    baSource = fbaBuildTarball([("stepAlpha/link", sKind, sTarget)])
    with pytest.raises(ValueError) as excInfo:
        agentCouncilRunner.fbufferRepackSnapshotStamped(baSource)
    assert "outside the extraction root" in str(excInfo.value)


def testRepackRefusesANestedHardLinkWhoseArchiveTargetEscapes():
    baSource = fbaBuildTarball([
        ("stepAlpha/link", "hardlink", "../outsideFile.txt")])
    with pytest.raises(ValueError):
        agentCouncilRunner.fbufferRepackSnapshotStamped(baSource)


def testRepackAdmitsALinkThatStaysInsideTheRoot():
    baSource = fbaBuildTarball([
        ("stepAlpha/deeper/link", "symlink", "../dataFile.csv")])
    dictMembers = fdictReadTarMembers(
        agentCouncilRunner.fbufferRepackSnapshotStamped(baSource)
        .getvalue())
    assert list(dictMembers) == ["stepAlpha/deeper/link"]


# ----- the bounded send ----------------------------------------------------


def testSendAllBoundedDeliversEveryByteAcrossPartialSendsAndTimeouts():
    baPayload = bytes(range(256)) * 3
    socketDouble = ScriptedExecSocket(
        iSendChunkBytes=100, setSendTimeoutCalls={2, 5})
    agentCouncilRunner.fnSendAllBounded(
        socketDouble, baPayload, time.monotonic() + 30.0)
    assert socketDouble.baSent == baPayload
    assert socketDouble.iSendCalls == len(baPayload) // 100 + 1 + 2


def testSendAllBoundedRaisesOncePastTheDeadline():
    socketDouble = ScriptedExecSocket()
    with pytest.raises(RuntimeError) as excInfo:
        agentCouncilRunner.fnSendAllBounded(
            socketDouble, b"payload", time.monotonic() - 1.0)
    assert "timed out" in str(excInfo.value)
    assert socketDouble.baSent == b""


def testSendAllBoundedSendsNothingForAnEmptyPayload():
    socketDouble = ScriptedExecSocket()
    agentCouncilRunner.fnSendAllBounded(
        socketDouble, b"", time.monotonic() - 1.0)
    assert socketDouble.iSendCalls == 0


# ----- the bounded exec-stream pump ----------------------------------------


def testPumpReassemblesFramesSplitAcrossReceivesAndSurvivesTimeouts():
    baStream = fbaFrame(b"first line\n") + fbaFrame(b"second\n", 2)
    socketDouble = ScriptedExecSocket([
        baStream[:5], socket.timeout("quiet"), baStream[5:14],
        baStream[14:],
    ])
    dictPumped = agentCouncilRunner.fdictPumpBoundedExecStream(
        socketDouble, 1024, time.monotonic() + 30.0, fStallSeconds=30.0)
    assert dictPumped["baCaptured"] == b"first line\nsecond\n"
    assert dictPumped["bOutputCapExceeded"] is False
    assert dictPumped["bDeadlineExceeded"] is False
    assert dictPumped["bStalled"] is False
    assert dictPumped["fStallSeconds"] == 30.0


def testPumpDropsAnIncompleteTrailingFrame():
    baStream = fbaFrame(b"whole") + fbaFrame(b"truncated payload")[:12]
    dictPumped = agentCouncilRunner.fdictPumpBoundedExecStream(
        ScriptedExecSocket([baStream]), 1024, time.monotonic() + 30.0)
    assert dictPumped["baCaptured"] == b"whole"


def testPumpTruncatesAtTheOutputCapAndSaysSo():
    socketDouble = ScriptedExecSocket([
        fbaFrame(b"abcdef"), fbaFrame(b"ghijkl"), fbaFrame(b"never read")])
    dictPumped = agentCouncilRunner.fdictPumpBoundedExecStream(
        socketDouble, 8, time.monotonic() + 30.0)
    assert dictPumped["baCaptured"] == b"abcdefgh"
    assert dictPumped["bOutputCapExceeded"] is True
    assert socketDouble.listReceiveScript == [fbaFrame(b"never read")]


def testPumpReportsTheDeadlineBeforeReadingAnything():
    socketDouble = ScriptedExecSocket([fbaFrame(b"unread")])
    dictPumped = agentCouncilRunner.fdictPumpBoundedExecStream(
        socketDouble, 1024, time.monotonic() - 1.0)
    assert dictPumped["bDeadlineExceeded"] is True
    assert dictPumped["bStalled"] is False
    assert dictPumped["baCaptured"] == b""
    assert dictPumped["fStallSeconds"] == (
        agentCouncilRunner.F_DEFAULT_TURN_STALL_SECONDS)


def testPumpEndsQuietlyOnASocketError():
    socketDouble = ScriptedExecSocket([
        fbaFrame(b"before the reset"), ConnectionResetError("reset")])
    dictPumped = agentCouncilRunner.fdictPumpBoundedExecStream(
        socketDouble, 1024, time.monotonic() + 30.0)
    assert dictPumped["baCaptured"] == b"before the reset"
    assert dictPumped["bDeadlineExceeded"] is False
    assert dictPumped["bOutputCapExceeded"] is False


def testPumpCallsASilentStreamStalled():
    dictPumped = agentCouncilRunner.fdictPumpBoundedExecStream(
        ScriptedExecSocket([socket.timeout("quiet")] * 3), 1024,
        time.monotonic() + 30.0, fStallSeconds=0.0)
    assert dictPumped["bStalled"] is True
    assert dictPumped["bDeadlineExceeded"] is False


# ----- the stamped config tarballs -----------------------------------------


def testFilesTarballRefusesNonBytesContent():
    with pytest.raises(TypeError) as excInfo:
        agentCouncilRunner.fbaBuildStampedFilesTarball(
            {"configDirectory/settings.json": "not bytes"})
    assert "configDirectory/settings.json" in str(excInfo.value)


@pytest.mark.parametrize("sBadPath", [
    "", "/absolute/settings.json", "..", ".", "../settings.json"])
def testFilesTarballRefusesPathsThatDoNotStayRelative(sBadPath):
    with pytest.raises(ValueError) as excInfo:
        agentCouncilRunner.fbaBuildStampedFilesTarball({sBadPath: b"x"})
    assert "must stay relative" in str(excInfo.value)


def testFilesTarballCreatesParentsOnceAndStampsEveryEntry():
    baTar = agentCouncilRunner.fbaBuildStampedFilesTarball({
        "configDirectory/nested/settings.json": b"{}",
        "configDirectory/token.json": b"secret",
    }, iFileMode=0o600, iDirectoryMode=0o700)
    with tarfile.open(fileobj=io.BytesIO(baTar), mode="r") as fileTar:
        listMembers = fileTar.getmembers()
    listNames = [infoMember.name for infoMember in listMembers]
    assert listNames == [
        "configDirectory", "configDirectory/nested",
        "configDirectory/nested/settings.json", "configDirectory/token.json"]
    for infoMember in listMembers:
        assert (infoMember.uid, infoMember.gid) == (
            I_COUNCIL_UID, I_COUNCIL_GID)
        assert infoMember.mode == (
            0o700 if infoMember.isdir() else 0o600)


# ----- egress: identifiers and the allowlist -------------------------------


@pytest.mark.parametrize("valueCampaignId", [
    "", "-leadingHyphen", "has space", "semi;colon", "a" * 65, None, 7])
def testCampaignIdRefusesAnythingThatIsNotAPlainToken(valueCampaignId):
    with pytest.raises(EgressSetupError):
        agentCouncilEgress.fsComposeNetworkName(valueCampaignId)
    with pytest.raises(EgressSetupError):
        agentCouncilEgress.fsComposeProxyContainerName(valueCampaignId)


def testNetworkAndProxyNamesCarryTheCampaignId():
    assert agentCouncilEgress.fsComposeNetworkName("campaignAlpha1") == (
        "vaibifyCouncilEgress-campaignAlpha1")
    assert agentCouncilEgress.fsComposeProxyContainerName(
        "campaignAlpha1") == "vaibifyCouncilProxy-campaignAlpha1"


def testAllowlistRefusesAnEmptyList():
    with pytest.raises(EgressSetupError) as excInfo:
        agentCouncilEgress.fnValidateAllowlistOrRaise([], [443], {})
    assert "must not be empty" in str(excInfo.value)


def testAllowlistRefusesAMalformedHostname():
    with pytest.raises(EgressSetupError) as excInfo:
        agentCouncilEgress.fnValidateAllowlistOrRaise(
            ["api.example.org", "bad host,injected"], [443], {})
    assert "'bad host,injected' is not a valid hostname" in str(
        excInfo.value)


def testAllowlistRefusesARawAddressEvenThoughItParsesAsAName():
    with pytest.raises(EgressSetupError) as excInfo:
        agentCouncilEgress.fnValidateAllowlistOrRaise(
            ["10.20.30.40"], [443], {})
    assert "is a raw address" in str(excInfo.value)


@pytest.mark.parametrize("valuePort", [0, 65536, -1, "443", 443.0])
def testAllowlistRefusesAnInvalidPort(valuePort):
    with pytest.raises(EgressSetupError) as excInfo:
        agentCouncilEgress.fnValidateAllowlistOrRaise(
            ["api.example.org"], [443, valuePort], {})
    assert "is not valid" in str(excInfo.value)


def testAllowlistRefusesAnAddressMapWithABadKey():
    with pytest.raises(EgressSetupError) as excInfo:
        agentCouncilEgress.fnValidateAllowlistOrRaise(
            ["api.example.org"], [443], {"bad key=": "10.0.0.5"})
    assert "address-map key" in str(excInfo.value)


def testAllowlistRefusesAnAddressMapWithANonAddressValue():
    with pytest.raises(EgressSetupError) as excInfo:
        agentCouncilEgress.fnValidateAllowlistOrRaise(
            ["api.example.org"], [443],
            {"api.example.org": "not-an-address"})
    assert "'not-an-address'" in str(excInfo.value)
    assert "'api.example.org'" in str(excInfo.value)


def testAllowlistAdmitsHostnamesPortsAndAPinnedAddress():
    agentCouncilEgress.fnValidateAllowlistOrRaise(
        ["api.example.org", "auth.example.org"], [443, 8443],
        {"api.example.org": "203.0.113.9", "auth.example.org": "2001:db8::1"})


# ----- egress: composed argv and runner values -----------------------------


def testProxyCommandOmitsTheAddressMapWhenNoneIsPinned():
    listCommand = agentCouncilEgress.flistComposeProxyCommand(
        ["api.example.org", "auth.example.org"], [443, 8443], {})
    assert listCommand == [
        "python", "/vaibifyEgress/vaibifyEgressProxy.py",
        "--listen-port", str(agentCouncilEgress.I_PROXY_LISTEN_PORT),
        "--allowlist", "api.example.org,auth.example.org",
        "--allowed-ports", "443,8443",
    ]


def testProxyCommandCarriesEveryPinnedAddress():
    listCommand = agentCouncilEgress.flistComposeProxyCommand(
        ["api.example.org"], [443],
        {"api.example.org": "203.0.113.9", "auth.example.org": "203.0.113.10"})
    iMapIndex = listCommand.index("--address-map")
    assert set(listCommand[iMapIndex + 1].split(",")) == {
        "api.example.org=203.0.113.9", "auth.example.org=203.0.113.10"}


def testRunnerProxyEnvironmentGivesBothSpellingsOfTheNumericProxy():
    dictEnvironment = agentCouncilEgress.fdictBuildRunnerProxyEnvironment(
        "172.30.0.2", iProxyPort=9999)
    assert dictEnvironment == {
        sKey: "http://172.30.0.2:9999" for sKey in (
            "HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")}


def testRunnerNetworkArgumentsSealTheResolver():
    listArguments = agentCouncilEgress.flistBuildRunnerNetworkArguments(
        "campaignAlpha1")
    assert listArguments == [
        "--network", "vaibifyCouncilEgress-campaignAlpha1",
        "--dns", agentCouncilEgress.S_BLACK_HOLE_NAMESERVER,
        "--dns-option", "timeout:1", "--dns-option", "attempts:1",
    ]


def testRunnerNetworkArgumentsRefuseACraftedCampaignId():
    with pytest.raises(EgressSetupError):
        agentCouncilEgress.flistBuildRunnerNetworkArguments(
            "campaign --privileged")


def testProxyScriptTarballIsReadableByTheNonRootProxy():
    dictMembers = {}
    with tarfile.open(
            fileobj=io.BytesIO(
                agentCouncilEgress.fbaBuildProxyScriptTarball()),
            mode="r") as fileTar:
        for infoMember in fileTar:
            baContent = (fileTar.extractfile(infoMember).read()
                         if infoMember.isreg() else None)
            dictMembers[infoMember.name] = (infoMember, baContent)
    infoDirectory, _ = dictMembers["vaibifyEgress"]
    infoScript, baScript = dictMembers["vaibifyEgress/vaibifyEgressProxy.py"]
    assert infoDirectory.mode == 0o755 and infoScript.mode == 0o644
    assert (infoScript.uid, infoScript.gid) == (I_COUNCIL_UID, I_COUNCIL_GID)
    assert baScript == agentCouncilEgress.S_CONNECT_PROXY_SCRIPT.encode(
        "utf-8")
