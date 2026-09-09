"""The attestation's GitHub mark is a control, not a picture.

The Rebuild attestation row is where a researcher reads about the
attestation, and it carried an octocat that only NAMED the problem --
the control that fixes it lived in the Published-copies block, several
rows away, behind an expander. A mark that states a problem beside no
way to act on it is the shape this repository already fixed once for
the Project-block file rows.

Rendered as a real badge on a `.detail-item` carrying
`data-resolved`, the one global badge handler picks it up and offers
"Sync now" for that exact file.

The published state is also said OUT LOUD. The row used to render
nothing once the attestation was on GitHub, so "published" and "this
row has no opinion" looked identical, and a researcher who had just
pushed got no confirmation that it landed.
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


_S_DRIVE_NUDGE = """(dictDetail) => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: dictDetail,
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['attestation']),
        setExpandedRequirementRows: new Set(['rebuildAttestation']),
        setToggledFileGroups: new Set(),
    });
    const elHost = document.createElement('div');
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const elNudge = elHost.querySelector('.attestation-nudge');
    const elActionable = elNudge
        ? elNudge.querySelector('[data-resolved]') : null;
    const dictSeen = {
        bPresent: Boolean(elNudge),
        sText: elNudge ? elNudge.textContent.trim() : '',
        sResolved: elActionable
            ? elActionable.getAttribute('data-resolved') : '',
        bHasBadge: Boolean(
            elNudge && elNudge.querySelector('.remote-badge')),
    };
    elHost.remove();
    return dictSeen;
}"""


def _fdictDetail(bGithub, **dictExtra):
    """Return the poll detail for a current attestation."""
    dictDetail = {
        "bRebuildAttestationCurrent": True,
        "dictAttestationPublication": {"github": bGithub},
        "sAttestationRepoPath": ".vaibify/l3_attestation.json",
    }
    dictDetail.update(dictExtra)
    return dictDetail


@pytest.mark.falsification
def test_the_unpublished_mark_is_a_control_not_a_picture(
    pageDashboard, serverHub,
):
    """The mark that names the problem must also fix it.

    `data-resolved` is what makes it actionable: the global badge
    handler reads it off the enclosing element and bails without it,
    which is exactly how these badges came to be inert once before.
    Asserting the attribute, not merely that an icon rendered, is the
    difference between a control and a decoration.

    Kills: rendering `_S_OCTOCAT_SVG` instead of
    `_fsRenderAttestationBadge`.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_NUDGE, _fdictDetail(False))

    assert dictSeen["bPresent"], "the nudge did not render at all"
    assert dictSeen["sResolved"] == ".vaibify/l3_attestation.json", (
        "the mark carries no path, so the badge handler cannot act "
        "on it: " + str(dictSeen)
    )
    assert dictSeen["bHasBadge"], (
        "no remote badge rendered, so there is nothing to click"
    )


@pytest.mark.falsification
def test_a_published_attestation_says_so_instead_of_vanishing(
    pageDashboard, serverHub,
):
    """Absence is not confirmation.

    Rendering nothing made "published" indistinguishable from "this
    row has no opinion", so a researcher who had just pushed could
    not tell whether it landed.

    Kills: restoring the bare `if (dictWhere.github === true) return
    "";` early exit.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_NUDGE, _fdictDetail(True))

    assert dictSeen["bPresent"], (
        "a published attestation reports nothing at all"
    )
    assert "Synced to GitHub" in dictSeen["sText"], (
        "the published state is not stated: " + dictSeen["sText"]
    )
    assert "not on GitHub" not in dictSeen["sText"], (
        "a published attestation is still being nudged"
    )


def test_an_unchecked_attestation_makes_no_claim(
    pageDashboard, serverHub,
):
    """`null` is "nobody looked", and must not read as published.

    The three states of `dictAttestationPublication` are kept apart on
    purpose; collapsing the unchecked one into either neighbour is a
    claim nobody earned.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_NUDGE, _fdictDetail(None))

    assert "No verification has checked" in dictSeen["sText"], (
        "an unchecked attestation does not say so: "
        + dictSeen["sText"]
    )
    assert "Synced to GitHub" not in dictSeen["sText"]


@pytest.mark.falsification
def test_the_badge_sits_beside_its_sentence(pageDashboard, serverHub):
    """The mark is inline here, not a file row's pinned gutter badge.

    `.detail-item` is the hook the one global badge handler resolves
    `data-resolved` from, so the host must carry it -- but that class
    is built for a FILE ROW: it reserves a 76px left gutter and pins
    an absolutely positioned badge strip into it. Dropped into a
    sentence, both halves misfire, and they misfire DIFFERENTLY:
    leaving the strip absolute pushed the text away from the mark,
    and undoing only that left 76px of blank space to the mark's left
    (both researcher-reported, 2026-09-08).

    Asserted as computed style rather than as a rule in the
    stylesheet, because `.detail-item` is defined later in the file
    and a single-class override loses to it silently -- the CSS reads
    correct and the gutter survives.

    Kills: dropping either compound selector back to a single class.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate("""() => {
        const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
            dictWorkflowEnvelopeDetail: {
                bRebuildAttestationCurrent: true,
                dictAttestationPublication: {github: false},
                sAttestationRepoPath: '.vaibify/l3_attestation.json'},
            dictRemoteChecks: {},
            setExpandedRequirementGroups: new Set(['attestation']),
            setExpandedRequirementRows: new Set(['rebuildAttestation']),
            setToggledFileGroups: new Set()});
        const elHost = document.createElement('div');
        elHost.innerHTML = sHtml;
        document.body.appendChild(elHost);
        const elNudge = elHost.querySelector('.attestation-nudge');
        const elBadgeHost = elNudge.querySelector(
            '.attestation-badge-host');
        const elStrip = elNudge.querySelector('.remote-badges');
        const dictSeen = {
            sPaddingLeft: getComputedStyle(elBadgeHost).paddingLeft,
            sStripPosition: getComputedStyle(elStrip).position,
        };
        elHost.remove();
        return dictSeen;
    }""")

    assert dictSeen["sPaddingLeft"] == "0px", (
        "the file-row gutter survived, so the mark sits behind blank "
        "space: " + dictSeen["sPaddingLeft"]
    )
    assert dictSeen["sStripPosition"] == "static", (
        "the badge strip is still pinned, so it does not flow with "
        "the sentence: " + dictSeen["sStripPosition"]
    )
