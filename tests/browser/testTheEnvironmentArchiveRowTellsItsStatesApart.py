"""The archive row's two reds mean opposite things, and differ in SHAPE.

These assertions are about the SCREEN, so a Python test of the gate
proves none of them. Four properties, and each one is a way the row
could look right while saying something false:

* DIVERGED and CLOSED are both red and must not be the same mark. One
  says "fix your deposit", the other says "nothing can be done" — and
  colour alone reaches no colour-blind reader.
* UNCHECKED is neither. Vaibify could not compare, so the row must not
  wear the colour that means the deposit disagrees.
* A deposit in flight shows its BYTES. A silent multi-minute upload is
  indistinguishable from a hang from the chair.
* An archived row names the platform and the DOI. A bare check claims
  a deposit without saying which one.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test."""
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


_S_DRIVE_ROW = """(dictArchive) => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {dictImageArchive: dictArchive},
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['artifacts']),
        setExpandedRequirementRows: new Set(['environmentArchive']),
        setToggledFileGroups: new Set(),
    });
    const elHost = document.createElement('div');
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const elRow = Array.from(elHost.querySelectorAll(
        '.requirement-row-header')).find(
            el => (el.dataset.req || '') === 'environmentArchive');
    const dictSeen = elRow ? {
        sMarkup: elRow.innerHTML,
        sText: elRow.closest('.requirement-row').textContent,
    } : null;
    elHost.remove();
    return dictSeen;
}"""


def _fdictArchivePayload(sState, **dictExtra):
    """Return the poll payload the backend builds for one row state."""
    dictPayload = {
        "sState": sState, "dictRecord": None, "listIssues": [],
        "dictDeposit": None,
    }
    dictPayload.update(dictExtra)
    return dictPayload


@pytest.mark.falsification
def test_diverged_and_closed_are_told_apart_by_shape_not_colour(
    pageDashboard, serverHub,
):
    """Their remedies are opposite, so their marks must differ.

    DIVERGED means the deposit on record does not cover this envelope
    and the researcher can fix it. CLOSED means the image is gone and
    Level 3 is out of reach for this result. Rendering both as the
    same red circle tells a researcher to go and fix something that
    cannot be fixed — and a reader who cannot distinguish the two
    reds gets no signal at all.

    Kills: dropping the ``diverged``/``closed`` branches from
    ``fsBuildLevelCell``, which returns both to the generic circle.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    sDiverged = pageDashboard.evaluate(
        _S_DRIVE_ROW, _fdictArchivePayload("diverged"))["sMarkup"]
    sClosed = pageDashboard.evaluate(
        _S_DRIVE_ROW, _fdictArchivePayload("closed"))["sMarkup"]

    assert "level-cell-warning" in sDiverged, (
        "a diverged deposit does not render its own mark: " + sDiverged
    )
    assert "level-cell-closed" in sClosed, (
        "a closed archive does not render its own mark: " + sClosed
    )
    assert sDiverged != sClosed, (
        "the two reds render identically, so shape carries nothing"
    )


@pytest.mark.falsification
def test_an_unchecked_archive_never_wears_a_divergence_mark(
    pageDashboard, serverHub,
):
    """Red is a claim about the deposit; nothing was compared.

    Kills: mapping ``unknown`` onto a red state in the row's colour
    table, which reports a divergence vaibify never established.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_DRIVE_ROW, _fdictArchivePayload("unknown"))

    assert "level-cell-warning" not in dictSeen["sMarkup"]
    assert "level-cell-closed" not in dictSeen["sMarkup"]
    assert "level-cell-none" not in dictSeen["sMarkup"], (
        "an uncheckable archive reads as unmet: " + dictSeen["sMarkup"]
    )
    assert "could not check" in dictSeen["sText"], (
        "the row does not say that nothing was compared"
    )


def test_a_running_deposit_shows_the_bytes_it_has_moved(
    pageDashboard, serverHub,
):
    """A silent upload and a hung one look the same from the chair."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_ROW, _fdictArchivePayload(
        "running",
        dictDeposit={
            "sPhase": "saving", "iBytesRead": 2 * 1024 ** 3,
            "iBytesTotal": 4 * 1024 ** 3, "sReason": "",
        },
    ))

    assert "2.00 GB" in dictSeen["sText"], (
        "the row shows no progress: " + dictSeen["sText"]
    )
    assert "4.00 GB" in dictSeen["sText"]


def test_an_archived_row_names_the_platform_and_the_doi(
    pageDashboard, serverHub,
):
    """Never a bare check: a deposit nobody can identify is a claim."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_ROW, _fdictArchivePayload(
        "attained",
        dictRecord={
            "sVersionDoi": "10.5281/zenodo.7000001",
            "sArchitecture": "arm64",
            "sProvenance": "original",
        },
    ))

    assert "10.5281/zenodo.7000001" in dictSeen["sText"]
    assert "arm64" in dictSeen["sText"]
    assert "level-cell-attained" in dictSeen["sMarkup"]


def test_a_failed_deposit_says_why(pageDashboard, serverHub):
    """The reason outlives the task; nothing else records it."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_ROW, _fdictArchivePayload(
        "unknown",
        dictDeposit={
            "sPhase": "failed", "iBytesRead": 0, "iBytesTotal": 0,
            "sReason": "HTTP 403 from the archive",
        },
    ))

    assert "HTTP 403 from the archive" in dictSeen["sText"], (
        "a failed deposit reports no reason: " + dictSeen["sText"]
    )
