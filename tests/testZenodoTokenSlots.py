"""Zenodo tokens live in per-project host slots, and only there.

Until 2026-10-29 a container project's Zenodo token lived in the
container keyring, which the in-container agent could read and write.
These tests pin the replacement (``vaibify/config/zenodoTokenSlots.py``
and ``vaibify/gui/zenodoTokenAccess.py``): a token is stored on the
HOST, in a slot keyed by the registry's random project identity, for a
container project as much as for a host project; a slot is written only
if it reads back equal; an unreadable keyring is never mistaken for an
empty one; and the probe that validates a token on the host never
discards or activates one.

The keyring is the suite's hermetic fake, so what is asserted is WHICH
slot was chosen and what it holds, not that a real keychain accepted it.
"""

import os

import pytest

from vaibify.config import registryManager, zenodoTokenSlots
from vaibify.config.zenodoTokenSlots import (
    TokenStoreError,
    TokenStoreUnreadable,
    fsActiveSlot,
    fsInstanceOfContainerSlot,
    fsPendingSlot,
)
from vaibify.gui import zenodoTokenAccess
from vaibify.reproducibility import zenodoClient

S_UUID_ALPHA = "a" * 32
S_UUID_BETA = "b" * 32
S_CONTAINER_ID = "cid-9f3e-not-the-project-name"
S_CONTAINER_PROJECT = "slotContainerProject"
S_HOST_PROJECT = "slotHostProject"


# ---------------------------------------------------------------------------
# Slot names
# ---------------------------------------------------------------------------


def testTheActiveSlotCarriesTheInstanceAndTheProjectIdentity():
    assert fsActiveSlot("sandbox", S_UUID_ALPHA) == (
        "zenodo_token_sandbox:" + S_UUID_ALPHA
    )
    assert fsActiveSlot("production", S_UUID_ALPHA) == (
        "zenodo_token_production:" + S_UUID_ALPHA
    )


def testThePendingSlotIsSeparateFromTheActiveOne():
    sPending = fsPendingSlot("zenodo_token_sandbox", S_UUID_ALPHA)
    assert sPending == "zenodo_token_sandbox:" + S_UUID_ALPHA + ":pending"
    assert sPending != fsActiveSlot("sandbox", S_UUID_ALPHA)
    assert fsPendingSlot("zenodo_token", S_UUID_ALPHA) == (
        "zenodo_token_unassigned:" + S_UUID_ALPHA + ":pending"
    )
    assert fsPendingSlot("zenodo_token_sandbox_backup", S_UUID_ALPHA) == (
        "zenodo_token_sandbox_backup:" + S_UUID_ALPHA + ":pending"
    )


def testTheInstanceOfAContainerSlotIsReadOffItsName():
    assert fsInstanceOfContainerSlot("zenodo_token_sandbox") == "sandbox"
    assert fsInstanceOfContainerSlot("zenodo_token_production_backup") == (
        "production"
    )
    assert fsInstanceOfContainerSlot("zenodo_token") == ""
    with pytest.raises(ValueError):
        fsInstanceOfContainerSlot("overleaf_token")


@pytest.mark.falsification
def testASlotNameNeverAcceptsAnUnknownInstance():
    """Kills: dropping the instance check from the slot-name builder."""
    with pytest.raises(ValueError, match="not a Zenodo instance"):
        fsActiveSlot("zenodo", S_UUID_ALPHA)
    with pytest.raises(ValueError, match="not a Zenodo instance"):
        fsActiveSlot("", S_UUID_ALPHA)


@pytest.mark.falsification
def testASlotNameNeedsAProjectIdentity():
    """Kills: dropping the identity check from the slot-name builder.

    Without an identity the name would be the shared, project-free
    slot every project reads: the store this module exists to close.
    """
    for sBad in ("", None, "../x", "a:b"):
        with pytest.raises(ValueError, match="not a project identity"):
            fsActiveSlot("sandbox", sBad)


@pytest.mark.falsification
def testATokenIsStoredPerProjectNeverShared(fixtureHermeticKeyring):
    """Two projects' sandbox tokens are two slots; neither sees the other's.

    Kills: dropping the project identity from the active slot name, so
    every project reads and overwrites one shared token.
    """
    zenodoTokenSlots.fnStoreActiveToken("sandbox", S_UUID_ALPHA, "token-alpha")
    zenodoTokenSlots.fnStoreActiveToken("sandbox", S_UUID_BETA, "token-beta")
    assert zenodoTokenSlots.fsReadActiveToken("sandbox", S_UUID_ALPHA) == (
        "token-alpha"
    )
    assert zenodoTokenSlots.fsReadActiveToken("sandbox", S_UUID_BETA) == (
        "token-beta"
    )
    assert len(fixtureHermeticKeyring.dictStore) == 2


# ---------------------------------------------------------------------------
# Writes are verified; unreadable is not absent
# ---------------------------------------------------------------------------


def testAWriteIsReportedOnlyWhenItReadsBackEqual(fixtureHermeticKeyring):
    zenodoTokenSlots.fnStoreSlotVerified("zenodo_token_sandbox:" + S_UUID_ALPHA, "x")
    assert zenodoTokenSlots.fsReadSlot("zenodo_token_sandbox:" + S_UUID_ALPHA) == "x"

    def fnDropTheWrite(sService, sName, sValue):
        del sService, sName, sValue

    fixtureHermeticKeyring.set_password = fnDropTheWrite
    with pytest.raises(TokenStoreError, match="different value|did not store"):
        zenodoTokenSlots.fnStoreSlotVerified("zenodo_token_production:" + S_UUID_ALPHA, "y")


def testAnEmptyValueIsNeverWritten():
    with pytest.raises(ValueError, match="never written empty"):
        zenodoTokenSlots.fnStoreSlotVerified("zenodo_token_sandbox:" + S_UUID_ALPHA, "")


def testAnUnreadableKeyringIsNotAnEmptySlot(fixtureHermeticKeyring):
    """A locked keychain raises; '' would tell the researcher to add a token."""
    def fnRefuse(sService, sName):
        raise RuntimeError("keychain locked")

    fixtureHermeticKeyring.get_password = fnRefuse
    with pytest.raises(TokenStoreUnreadable, match="could not be read"):
        zenodoTokenSlots.fsReadActiveToken("sandbox", S_UUID_ALPHA)


def testAnAbsentSlotReadsAsEmpty():
    assert zenodoTokenSlots.fsReadActiveToken("sandbox", S_UUID_ALPHA) == ""
    assert zenodoTokenSlots.fbHasActiveToken("sandbox", S_UUID_ALPHA) is False
    assert zenodoTokenSlots.flistPendingSlotsFor(S_UUID_ALPHA) == []


def testDeletingAnAbsentSlotIsNotAnError():
    zenodoTokenSlots.fnDeleteActiveToken("sandbox", S_UUID_ALPHA)


# ---------------------------------------------------------------------------
# The access layer: both project kinds store on the host, keyed by identity
# ---------------------------------------------------------------------------


class _PoisonConnection:
    """Raises on any use except resolving the container's name."""

    def fbDockerLegPresent(self):
        return True

    def flistGetRunningContainers(self):
        return [{"sContainerId": S_CONTAINER_ID, "sName": S_CONTAINER_PROJECT}]

    def __getattr__(self, sAttributeName):
        raise AssertionError(
            f"a container was consulted (.{sAttributeName}) for a Zenodo token"
        )


@pytest.fixture
def fixtureTwoRegisteredProjects(tmp_path, monkeypatch):
    """Register one host and one container project; return their identities."""
    sHome = str(tmp_path / "home")
    os.makedirs(sHome)
    monkeypatch.setenv("HOME", sHome)
    for sAttribute, sValue in (
        ("_S_REGISTRY_DIRECTORY", sHome),
        ("_S_REGISTRY_PATH", os.path.join(sHome, "registry.json")),
        ("_S_LOCK_PATH", os.path.join(sHome, "registry.lock")),
    ):
        monkeypatch.setattr(registryManager, sAttribute, sValue)
    dictUuids = {}
    for sName, sMode in ((S_HOST_PROJECT, "host"), (S_CONTAINER_PROJECT, "container")):
        sDirectory = os.path.join(sHome, sName)
        os.makedirs(sDirectory)
        with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileConfig:
            fileConfig.write(f"projectName: {sName}\n")
        registryManager.fnAddProject(sDirectory, sMode=sMode)
        dictUuids[sName] = registryManager.fdictGetProject(sName)["sProjectUuid"]
    return dictUuids


def testAContainerProjectsTokenGoesToTheHostSlotOfItsIdentity(
    fixtureTwoRegisteredProjects, fixtureHermeticKeyring,
):
    """No container is consulted; the slot is the project's, resolved from the id."""
    zenodoTokenAccess.fnStoreActiveZenodoToken(
        _PoisonConnection(), S_CONTAINER_ID, "sandbox", "a-real-looking-token",
    )
    sUuid = fixtureTwoRegisteredProjects[S_CONTAINER_PROJECT]
    assert fixtureHermeticKeyring.dictStore == {
        ("vaibify", "zenodo_token_sandbox:" + sUuid): "a-real-looking-token",
    }
    assert zenodoTokenAccess.fsActiveZenodoToken(
        _PoisonConnection(), S_CONTAINER_ID, "sandbox",
    ) == "a-real-looking-token"


def testAHostProjectsTokenGoesToTheHostSlotOfItsIdentity(
    fixtureTwoRegisteredProjects, fixtureHermeticKeyring,
):
    zenodoTokenAccess.fnStoreActiveZenodoToken(
        _PoisonConnection(), S_HOST_PROJECT, "production", "prod-token",
    )
    sUuid = fixtureTwoRegisteredProjects[S_HOST_PROJECT]
    assert fixtureHermeticKeyring.dictStore == {
        ("vaibify", "zenodo_token_production:" + sUuid): "prod-token",
    }
    assert zenodoTokenAccess.fbHasActiveZenodoToken(
        _PoisonConnection(), S_HOST_PROJECT, "production",
    )


def testAnUnregisteredContainerHasNoTokenAndCannotStoreOne(
    fixtureTwoRegisteredProjects,
):
    class _UnknownConnection(_PoisonConnection):
        def flistGetRunningContainers(self):
            return []

    assert zenodoTokenAccess.fsActiveZenodoToken(
        _UnknownConnection(), "cid-unknown", "sandbox",
    ) == ""
    with pytest.raises(ValueError, match="not a registered project"):
        zenodoTokenAccess.fnStoreActiveZenodoToken(
            _UnknownConnection(), "cid-unknown", "sandbox", "x",
        )


def testTheMissingTokenSentenceNamesAPendingTokenWhenOneAwaits(
    fixtureTwoRegisteredProjects,
):
    sUuid = fixtureTwoRegisteredProjects[S_CONTAINER_PROJECT]
    assert "No sandbox Zenodo token is stored on this host" in (
        zenodoTokenAccess.fsDescribeMissingZenodoToken(
            _PoisonConnection(), S_CONTAINER_ID, "sandbox",
        )
    )
    zenodoTokenSlots.fnStoreSlotVerified(
        fsPendingSlot("zenodo_token_sandbox", sUuid), "sekrit-pending-value",
    )
    sSentence = zenodoTokenAccess.fsDescribeMissingZenodoToken(
        _PoisonConnection(), S_CONTAINER_ID, "sandbox",
    )
    assert "awaiting your confirmation" in sSentence
    assert "sekrit-pending-value" not in sSentence
    with pytest.raises(zenodoTokenAccess.ZenodoTokenUnavailable):
        zenodoTokenAccess.fsRequireActiveZenodoToken(
            _PoisonConnection(), S_CONTAINER_ID, "sandbox",
        )


# ---------------------------------------------------------------------------
# The host-side probe
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, iStatus, listBody):
        self.status_code = iStatus
        self._listBody = listBody
        self.text = "{}"
        self.headers = {}

    def json(self):
        return self._listBody

    def close(self):
        return None


def _fnAnswerEveryGet(monkeypatch, iStatus, listBody=None):
    listSeen = []

    def fresponseGet(sUrl, headers=None, **dictKeywords):
        listSeen.append((sUrl, dict(headers or {})))
        return _FakeResponse(iStatus, listBody or [])

    monkeypatch.setattr(zenodoClient.requests, "get", fresponseGet)
    return listSeen


def testAnAcceptedProbeReportsTheDepositTitles(monkeypatch):
    listSeen = _fnAnswerEveryGet(monkeypatch, 200, [
        {"metadata": {"title": "First"}}, {"metadata": {"title": "Second"}},
    ])
    dictProbe = zenodoClient.fdictProbeToken("sandbox", "tok")
    assert dictProbe["sOutcome"] == zenodoClient.S_PROBE_ACCEPTED
    assert dictProbe["listDepositTitles"] == ["First", "Second"]
    assert listSeen[0][0].startswith("https://sandbox.zenodo.org/api/deposit/depositions")
    assert listSeen[0][1]["Authorization"] == "Bearer tok"


def testARejectedProbeIsRejected(monkeypatch):
    _fnAnswerEveryGet(monkeypatch, 401)
    assert zenodoClient.fdictProbeToken("zenodo", "tok")["sOutcome"] == (
        zenodoClient.S_PROBE_REJECTED
    )


@pytest.mark.parametrize("iStatus", [429, 500, 503])
def testAnUnansweredProbeIsUndeterminedNotRejected(monkeypatch, iStatus):
    _fnAnswerEveryGet(monkeypatch, iStatus)
    assert zenodoClient.fdictProbeToken("sandbox", "tok")["sOutcome"] == (
        zenodoClient.S_PROBE_UNDETERMINED
    )


@pytest.mark.falsification
def testANetworkFailureIsUndetermined(monkeypatch):
    """Kills: the probe classifying a network failure as a rejection."""
    import requests

    def fresponseRaise(sUrl, headers=None, **dictKeywords):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(zenodoClient.requests, "get", fresponseRaise)
    dictProbe = zenodoClient.fdictProbeToken("sandbox", "tok")
    assert dictProbe["sOutcome"] == zenodoClient.S_PROBE_UNDETERMINED
    assert "ConnectionError" in dictProbe["sDetail"]


def testAnEmptyTokenIsRejectedWithoutANetworkCall(monkeypatch):
    listSeen = _fnAnswerEveryGet(monkeypatch, 200)
    assert zenodoClient.fdictProbeToken("sandbox", "")["sOutcome"] == (
        zenodoClient.S_PROBE_REJECTED
    )
    assert listSeen == []


def testTheClientNeverReachesIntoAKeyringForItsToken(fixtureHermeticKeyring):
    """A client built without a token has none; it does not look one up."""
    fixtureHermeticKeyring.dictStore[("vaibify", "zenodo_token_sandbox")] = "old"
    assert zenodoClient.ZenodoClient("sandbox")._fsGetToken() == ""
    assert not hasattr(zenodoClient, "_fsRetrieveToken")
