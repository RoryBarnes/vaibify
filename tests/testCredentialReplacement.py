"""A token replaces the stored one only after its service accepted it.

Source: ``vaibify/config/credentialReplacement.py`` and the token box's
store, ``_fdictStoreValidateCredential`` in
``vaibify/gui/routes/syncRoutes.py``.

The store this replaced wrote the new token first, asked the service,
and restored a snapshot on a "no". An independent review (2026-11-24)
found two ways that lost a working token, and both were reproduced
before the redesign: a check that RAISED (a malformed Overleaf project
ID) skipped the restore and left the unchecked token installed, and two
projects sharing the one Overleaf slot under different container locks
let A's failed attempt restore the old token over B's success. The
sequences below are the reviewer's, driven through the real store path
against the hermetic in-memory keyring, with the service checks
replaced by stand-ins that block on events so the interleaving is
deterministic.
"""

import subprocess
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

from vaibify.config import credentialReplacement
from vaibify.gui.routes import syncRoutes
from tests.zenodoTokenFixtures import (  # noqa: F401  (autouse fixture)
    fixtureHostZenodoSlots,
)


S_OVERLEAF_CANDIDATE = "vaibify.gui.syncDispatcher.ftValidateOverleafCandidate"
S_ZENODO_CANDIDATE = "vaibify.reproducibility.zenodoClient.ftValidateZenodoCandidate"
T_OVERLEAF_KEY = ("vaibify", "overleaf_token")
S_PROJECT_ID = "abc123proj"
F_EVENT_WAIT_SECONDS = 10.0


def _fdictStoreOverleaf(sToken, sProjectId=S_PROJECT_ID):
    return syncRoutes._fdictStoreValidateCredential(
        {"docker": MagicMock()}, "cid", "overleaf", sToken, sProjectId,
    )


def _fthreadStoreInBackground(sToken, dictOutcomes):
    def fnStore():
        dictOutcomes[sToken] = _fdictStoreOverleaf(sToken)

    threadStore = threading.Thread(target=fnStore, daemon=True)
    threadStore.start()
    return threadStore


# ---------------------------------------------------------------------------
# The reviewer's two sequences
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testAFailedCheckNeverRemovesATokenAnotherProjectStoredMeanwhile(
    fixtureHermeticKeyring,
):
    """A's check fails AFTER B stored its token: B's token must survive.

    The reviewer's interleaving: A starts, B checks and stores a good
    token and reports success, then A's check fails. Under the old
    store-validate-restore order A wrote its own token first and its
    failure path restored (or deleted) over B's.

    Kills: deleting the slot when a check fails (the old cleanup lane).
    """
    fixtureHermeticKeyring.dictStore[T_OVERLEAF_KEY] = "old-token"
    eventAIsChecking = threading.Event()
    eventBHasStored = threading.Event()

    def ftCheckCandidate(sProjectId, sToken):
        if sToken == "token-a":
            eventAIsChecking.set()
            assert eventBHasStored.wait(F_EVENT_WAIT_SECONDS)
            return ("token-rejected", "fatal: Authentication failed")
        return ("", "")

    dictOutcomes = {}
    with patch(S_OVERLEAF_CANDIDATE, side_effect=ftCheckCandidate):
        threadA = _fthreadStoreInBackground("token-a", dictOutcomes)
        assert eventAIsChecking.wait(F_EVENT_WAIT_SECONDS)
        dictOutcomes["token-b"] = _fdictStoreOverleaf("token-b")
        eventBHasStored.set()
        threadA.join(F_EVENT_WAIT_SECONDS)
    assert dictOutcomes["token-b"]["bConnected"] is True
    assert dictOutcomes["token-a"]["bConnected"] is False
    assert fixtureHermeticKeyring.dictStore[T_OVERLEAF_KEY] == "token-b"


@pytest.mark.falsification
def testACheckThatPassesAfterTheSlotMovedIsRefusedNotWritten(
    fixtureHermeticKeyring,
):
    """A passes its check only after B stored: A must not overwrite B.

    B already told its researcher "Connected". Writing A over it would
    make that answer false without a word, so A is refused with the
    sentence that says why, and the slot keeps B's token.

    Kills: skipping the fingerprint comparison at the commit.
    """
    fixtureHermeticKeyring.dictStore[T_OVERLEAF_KEY] = "old-token"
    eventAIsChecking = threading.Event()
    eventBHasStored = threading.Event()

    def ftCheckCandidate(sProjectId, sToken):
        if sToken == "token-a":
            eventAIsChecking.set()
            assert eventBHasStored.wait(F_EVENT_WAIT_SECONDS)
        return ("", "")

    dictOutcomes = {}
    with patch(S_OVERLEAF_CANDIDATE, side_effect=ftCheckCandidate):
        threadA = _fthreadStoreInBackground("token-a", dictOutcomes)
        assert eventAIsChecking.wait(F_EVENT_WAIT_SECONDS)
        dictOutcomes["token-b"] = _fdictStoreOverleaf("token-b")
        eventBHasStored.set()
        threadA.join(F_EVENT_WAIT_SECONDS)
    assert dictOutcomes["token-b"]["bConnected"] is True
    assert dictOutcomes["token-a"]["sClass"] == "slot-changed"
    assert "was not stored" in dictOutcomes["token-a"]["sMessage"]
    assert fixtureHermeticKeyring.dictStore[T_OVERLEAF_KEY] == "token-b"


# ---------------------------------------------------------------------------
# A check that cannot run, or raises, writes nothing and raises nothing
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testAMalformedProjectIdIsRefusedBeforeAnyCheckOrWrite(fixtureHermeticKeyring):
    """The reviewer's reproduction: a malformed ID left the new token installed.

    The ID is refused by its shape before the service is asked and
    before the slot is touched, with the sentence that says what an
    Overleaf project ID is.

    Kills: removing the project-ID shape check from the input refusal.
    """
    fixtureHermeticKeyring.dictStore[T_OVERLEAF_KEY] = "old-token"
    with patch(S_OVERLEAF_CANDIDATE, return_value=("", "")) as mockCheck:
        dictResult = _fdictStoreOverleaf("new-token", "not a valid id!")
    assert dictResult["sClass"] == "input-invalid"
    assert "not an Overleaf project ID" in dictResult["sMessage"]
    mockCheck.assert_not_called()
    assert fixtureHermeticKeyring.dictStore[T_OVERLEAF_KEY] == "old-token"


@pytest.mark.falsification
def testACheckThatRaisesIsReportedAndNeverRaisedIntoTheCarrier(
    fixtureHermeticKeyring,
):
    """An exception from the service check is an answer, not a raise.

    Raised into the setup's carrier, it would mark the operation for
    reconciliation and quarantine a container this flow never wrote.
    Nothing was written, so "no answer" is the honest report.

    Kills: narrowing the handler around the check so the exception escapes.
    """
    fixtureHermeticKeyring.dictStore[T_OVERLEAF_KEY] = "old-token"
    with patch(S_OVERLEAF_CANDIDATE, side_effect=RuntimeError("socket closed")):
        dictResult = _fdictStoreOverleaf("new-token")
    assert dictResult["bConnected"] is False
    assert dictResult["sClass"] == "no-answer"
    assert fixtureHermeticKeyring.dictStore[T_OVERLEAF_KEY] == "old-token"


def testAnUnreadableKeychainIsSaidAndNothingIsChecked(fixtureHostZenodoSlots):
    """A locked keychain is "could not read", never "no token stored"."""
    fixtureHostZenodoSlots.fnBreakEveryRead()
    with patch(S_ZENODO_CANDIDATE, return_value=("", "")) as mockCheck:
        dictResult = syncRoutes._fdictStoreValidateCredential(
            {"docker": MagicMock()}, "cid", "zenodo", "new-token", "",
            "sandbox",
        )
    assert dictResult["sClass"] == "keyring-unreadable"
    assert "could not read this computer's keychain" in dictResult["sMessage"]
    mockCheck.assert_not_called()


def testTheConnectionCheckSaysWhenTheKeychainCannotBeRead(fixtureHermeticKeyring):
    """The presence check is three-state: stored, absent, or unreadable."""
    from vaibify.gui.syncDispatcher import _fdictCheckHostKeyring

    def fnRefuse(sService, sName):
        raise RuntimeError("keychain locked")

    fixtureHermeticKeyring.get_password = fnRefuse
    dictResult = _fdictCheckHostKeyring("overleaf_token")
    assert dictResult["bConnected"] is False
    assert dictResult["sMessage"].startswith("Could not check")


# ---------------------------------------------------------------------------
# The lock excludes another PROCESS, not only another thread
# ---------------------------------------------------------------------------


_S_HOLD_THE_LOCK_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
from vaibify.config import credentialReplacement
credentialReplacement._S_CREDENTIAL_LOCK_DIRECTORY = sys.argv[2]
with credentialReplacement.fcontextHoldSlotLock("overleaf_token"):
    print("held", flush=True)
    sys.stdin.readline()
"""


def testAnotherProcessHoldingTheSlotKeepsThisOneOut(tmp_path, monkeypatch):
    """Hubs and the CLI are separate processes; the lock must span them."""
    import pathlib
    sLockDirectory = str(tmp_path / "credentialLocks")
    monkeypatch.setattr(
        credentialReplacement, "_S_CREDENTIAL_LOCK_DIRECTORY", sLockDirectory,
    )
    sRepository = str(pathlib.Path(__file__).resolve().parent.parent)
    processHolder = subprocess.Popen(
        [sys.executable, "-c", _S_HOLD_THE_LOCK_SCRIPT, sRepository,
         sLockDirectory],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert processHolder.stdout.readline().strip() == "held"
        with pytest.raises(credentialReplacement.CredentialSlotBusy):
            with credentialReplacement.fcontextHoldSlotLock(
                "overleaf_token", fWaitSeconds=0.3,
            ):
                pass
    finally:
        processHolder.communicate("\n", timeout=F_EVENT_WAIT_SECONDS)
    with credentialReplacement.fcontextHoldSlotLock(
        "overleaf_token", fWaitSeconds=F_EVENT_WAIT_SECONDS,
    ):
        pass


# ---------------------------------------------------------------------------
# No secret reaches a response or a log line
# ---------------------------------------------------------------------------


S_SECRET_MARKER = "VaibifyMarker-9f2c41e7"


@pytest.mark.falsification
@pytest.mark.parametrize("tCheckAnswer", [
    ("no-answer", f"ConnectionError: peer echoed {S_SECRET_MARKER}"),
    ("token-rejected", f"fatal: Authentication failed near {S_SECRET_MARKER}"),
    ("service-refused", f"remote: refused {S_SECRET_MARKER}"),
])
def testTheEnteredTokenNeverComesBackInTheAnswerOrTheLog(
    tCheckAnswer, fixtureHermeticKeyring, caplog,
):
    """A service that echoes the token verbatim must not echo it to the page.

    The marker has no credential SHAPE, so the pattern redactor alone
    passes it; only removing the exact value the store sent catches it.

    Kills: dropping the known-secret scrub from the store's failure path.
    """
    caplog.set_level("INFO", logger="vaibify")
    with patch(S_OVERLEAF_CANDIDATE, return_value=tCheckAnswer):
        dictResult = _fdictStoreOverleaf(S_SECRET_MARKER)
    assert dictResult["bConnected"] is False
    assert S_SECRET_MARKER not in repr(dictResult)
    assert S_SECRET_MARKER not in caplog.text


@pytest.mark.parametrize("dictPatch", [
    {"target": S_OVERLEAF_CANDIDATE,
     "side_effect": RuntimeError(f"boom {S_SECRET_MARKER}")},
    {"target": "vaibify.config.secretManager.fnStoreSecret",
     "side_effect": RuntimeError(f"keyring said {S_SECRET_MARKER}")},
])
def testNoExceptionTextCarriesTheTokenOut(dictPatch, fixtureHermeticKeyring, caplog):
    """An exception's own text never joins the sentence; its class may."""
    caplog.set_level("INFO", logger="vaibify")
    dictKeywords = dict(dictPatch)
    sTarget = dictKeywords.pop("target")
    with patch(S_OVERLEAF_CANDIDATE, return_value=("", "")), \
            patch(sTarget, **dictKeywords):
        dictResult = _fdictStoreOverleaf(S_SECRET_MARKER)
    assert dictResult["bConnected"] is False
    assert S_SECRET_MARKER not in repr(dictResult)
    assert S_SECRET_MARKER not in caplog.text


def testTheReviewEvidenceNeverEchoesTheStoredToken(fixtureHostZenodoSlots, monkeypatch):
    """The review's Zenodo evidence removes the stored token from any detail."""
    from vaibify.gui.routes import bindingRoutes
    from vaibify.reproducibility import zenodoClient
    fixtureHostZenodoSlots.fnStoreActive("sandbox", S_SECRET_MARKER)
    monkeypatch.setattr(
        zenodoClient, "fdictProbeToken",
        lambda sService, sToken: {
            "sOutcome": "undetermined", "sClass": "no-answer",
            "sDetail": f"ZenodoServerError: body echoed {sToken}",
            "listDepositTitles": [],
        },
    )
    dictEvidence = bindingRoutes._fdictFetchZenodoEvidence(
        {"docker": None}, "cid", "environment", {"sInstance": "sandbox"},
    )
    assert dictEvidence["sState"] == "no-answer"
    assert S_SECRET_MARKER not in repr(dictEvidence)
