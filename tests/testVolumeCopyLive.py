"""The volume copier against a real daemon: who it runs as, and what it copies.

Two claims only a real daemon can settle. First, IDENTITY: an exec in
the volume copier runs as uid 0 with exactly the four approved
capabilities, while an exec in any other disposable runs as the
unprivileged user with none -- the exec user comes from the profile
recorded with the reservation. Second, FIDELITY: a source volume
holding files owned by root, by the container user and by an arbitrary
third uid, a 0600 file the copier does not own, a symlink and a
hard-linked pair arrives intact. Fidelity is judged by an INDEPENDENT
oracle -- ``find -printf`` and ``sha256sum`` run in a plain container
over both volumes -- never by the copier's own manifest.
"""

import secrets

import pytest

from tests.liveContainerLabels import fdictLabels
from vaibify.docker import disposableContainer, volumeCopy


pytestmark = pytest.mark.docker_live

S_PROBE_IMAGE = "python:3.12-slim"
S_RESOURCE = "volumeCopyLiveProbe"
# CHOWN, DAC_OVERRIDE, DAC_READ_SEARCH, FOWNER: capability bits 0-3.
S_EXPECTED_COPIER_CAPABILITIES = "000000000000000f"
S_NO_CAPABILITIES = "0000000000000000"

S_POPULATE_SCRIPT = r"""
set -e
cd /volume
mkdir -p data/nested
printf 'root file\n' > data/rootOwned.txt
printf 'container user file\n' > data/userOwned.txt
printf 'third user secret\n' > data/nested/thirdUser.key
ln data/userOwned.txt data/userLinked.txt
ln -s data/rootOwned.txt pointer
chown 1000:1000 data/userOwned.txt
chown 4242:4242 data/nested/thirdUser.key data/nested
chmod 0600 data/nested/thirdUser.key
chmod 0750 data/nested
touch -h -d '2020-02-02 02:02:02' pointer data/rootOwned.txt
"""

S_ORACLE_SCRIPT = (
    "cd /volume && find . -printf '%P|%y|%U|%G|%m|%s|%T@|%l|%n\\n' | sort "
    "&& find . -type f -exec sha256sum {} + | sort"
)


@pytest.fixture
def dockerLive():
    from tests.testDockerConnectionLive import fnRequireDaemonReachable
    fnRequireDaemonReachable()
    dockerClient = disposableContainer.fdockerCreateDisposableClient()
    try:
        dockerClient.images.get(S_PROBE_IMAGE)
    except Exception:
        pytest.skip(f"`docker pull {S_PROBE_IMAGE}` to run this lane")
    return dockerClient


@pytest.fixture
def listVolumes(dockerLive):
    """Two fresh volume names, removed afterwards whatever happened."""
    sSuffix = secrets.token_hex(4)
    listNames = [f"vaibifyCopySource{sSuffix}", f"vaibifyCopyTarget{sSuffix}"]
    for sName in listNames:
        dockerLive.volumes.create(sName, labels=fdictLabels())
    yield listNames
    for sName in listNames:
        try:
            dockerLive.volumes.get(sName).remove(force=True)
        except Exception:
            pass


def _fsRunAsRoot(dockerLive, sVolume, sScript, sMode="rw"):
    """Run a script as root in a plain container over one volume."""
    baOutput = dockerLive.containers.run(
        S_PROBE_IMAGE, ["sh", "-c", sScript], user="0:0", remove=True,
        network_mode="none", labels=fdictLabels(), volumes={sVolume: {"bind": "/volume",
                                                "mode": sMode}},
    )
    return baOutput.decode("utf-8")


def _fsImageId(dockerLive):
    return dockerLive.images.get(S_PROBE_IMAGE).id


def _ftIdentityOf(dictGateway, sHandle):
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, sHandle,
        ["sh", "-c", "id -u; grep CapEff /proc/self/status"])
    listLines = dictOutcome["sOutput"].split()
    return listLines[0], listLines[-1]


@pytest.mark.falsification
def testOnlyTheVolumeCopierExecsAsRootWithItsFourCapabilities(
    dockerLive, listVolumes,
):
    """Kills: every disposable exec running as root."""
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerLive, S_RESOURCE)
    dictCopier = disposableContainer.fdictReserveAndCreateVolumeCopier(
        dictGateway, _fsImageId(dockerLive), *listVolumes)
    dictOrdinary = disposableContainer.fdictReserveAndCreateContainer(
        dictGateway, "shadow", _fsImageId(dockerLive))
    try:
        assert _ftIdentityOf(dictGateway, dictCopier["sHandle"]) == (
            "0", S_EXPECTED_COPIER_CAPABILITIES)
        assert _ftIdentityOf(dictGateway, dictOrdinary["sHandle"]) == (
            "1000", S_NO_CAPABILITIES)
    finally:
        for dictCreated in (dictCopier, dictOrdinary):
            disposableContainer.fdictDestroyAndSettle(
                dictGateway, dictCreated["sHandle"])


@pytest.mark.falsification
def testAVolumeOfMixedOwnershipArrivesIntact(dockerLive, listVolumes):
    """Kills: the volume copier's execs left as the unprivileged user."""
    sSource, sDestination = listVolumes
    _fsRunAsRoot(dockerLive, sSource, S_POPULATE_SCRIPT)
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerLive, S_RESOURCE)
    dictCopied = volumeCopy.fdictCopyVolumeAndVerify(
        dictGateway, _fsImageId(dockerLive), sSource, sDestination,
        "workspace")
    assert dictCopied["iEntries"] == 8
    sOracleSource = _fsRunAsRoot(dockerLive, sSource, S_ORACLE_SCRIPT, "ro")
    sOracleTarget = _fsRunAsRoot(
        dockerLive, sDestination, S_ORACLE_SCRIPT, "ro")
    assert sOracleSource == sOracleTarget
    assert "|4242|4242|600|" in sOracleTarget
    assert "data/userLinked.txt|f|1000|1000|644|20|" in sOracleTarget
    assert "pointer|l|0|0|777|18|" in sOracleTarget
    assert dictGateway["dictHandlesById"] == {}
