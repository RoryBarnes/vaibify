"""The octocat reports agreement with GitHub, and says so honestly.

A researcher confirmed with the in-container agent that every file in
their project was byte-identical to the GitHub copy -- 19 of 19
matching -- and the octocat beside each file stayed dark. Two separate
defects, both fixed here, both of the same family: a value that means
"I have no answer" was being rendered as a value that means "the
answer is no".

**The badge asked the wrong question.** ``sGithub`` was
``_fsGitBadge``, which reads ``git status --porcelain``. That is local
working-tree cleanliness, not agreement with a remote, and it fails in
the direction that matters: a file committed but never pushed has no
porcelain entry, so the octocat lit as "in sync with remote" for a
remote that had never seen it. Every other remote column -- Overleaf,
Zenodo, arXiv -- was already a real comparison; GitHub was the one
that was not. It now reads the same cached verify the Level 2 cells
read.

**There was no way to say "not checked".** The vocabulary held
``synced`` / ``drifted`` / ``none`` and nothing else, so a file nobody
had verified rendered as ``none``, whose tooltip reads "not synced to
this remote" -- a positive negative claim about a file the system had
never looked at. ``unknown`` now carries that, and
``listComparedPaths`` is what makes it separable from ``synced``:
absence from ``listDiverged`` means "matched" only for a path the
verify actually compared.

The local git answer was not deleted, it was renamed to ``sGitState``
-- the declaration row's track/untrack gate genuinely wants "does git
hold this file", and it is the only consumer that did.

Kills (confirmed, not assumed): restoring ``_fsGitBadge`` as the
sGithub source fails the never-pushed test with 'synced'; dropping the
``listComparedPaths`` check fails the never-compared test the same
way.
"""

import pytest

from vaibify.gui import badgeState


S_FILE = "Step01/output.json"


def _fdictGit(dictFileStates=None, bIsRepo=True):
    """A porcelain snapshot; empty states means every file is clean."""
    return {
        "bIsRepo": bIsRepo,
        "sHeadSha": "abc",
        "sBranch": "main",
        "iAhead": 0,
        "iBehind": 0,
        "dictFileStates": dictFileStates or {},
        "sRefreshedAt": "2026-08-25T12:00:00Z",
        "sReason": "",
    }


def _fdictGithubStatus(
    listCompared=(S_FILE,), listDiverged=(), bVerified=True,
):
    """A syncStatus.json github entry, as a real verify writes one."""
    return {
        "sService": "github",
        "sLastVerified": "2026-08-25T12:00:00Z" if bVerified else None,
        "iTotalFiles": len(listCompared),
        "iMatching": len(listCompared) - len(listDiverged),
        "listComparedPaths": list(listCompared),
        "listDiverged": list(listDiverged),
    }


def _fsBadgeFor(dictGithubStatus, dictGit=None, sPath=S_FILE):
    dictResult = badgeState.fdictBadgeStateFromHashes(
        [sPath], dictGit or _fdictGit(), {},
        {sPath: "a" * 40}, set(),
        dictGithubStatus=dictGithubStatus,
    )
    return dictResult[sPath]


# ---------------------------------------------------------------------
# The four states.
# ---------------------------------------------------------------------


def test_a_verified_match_lights_the_badge():
    """The researcher's case: 19 of 19 matching, badge must light."""
    assert _fsBadgeFor(_fdictGithubStatus()) ["sGithub"] == (
        badgeState.S_BADGE_SYNCED
    )


def test_a_verified_difference_reads_drifted():
    dictStatus = _fdictGithubStatus(listDiverged=[
        {"sPath": S_FILE, "sExpected": "aaa", "sActual": "bbb"},
    ])
    assert _fsBadgeFor(dictStatus)["sGithub"] == (
        badgeState.S_BADGE_DRIFTED
    )


def test_a_file_the_mirror_does_not_have_reads_none():
    """A 404 from the mirror records sActual empty, not a hash."""
    dictStatus = _fdictGithubStatus(listDiverged=[
        {"sPath": S_FILE, "sExpected": "aaa", "sActual": None},
    ])
    assert _fsBadgeFor(dictStatus)["sGithub"] == badgeState.S_BADGE_NONE


def test_no_verify_at_all_reads_unknown():
    assert _fsBadgeFor(None)["sGithub"] == badgeState.S_BADGE_UNKNOWN
    assert _fsBadgeFor({})["sGithub"] == badgeState.S_BADGE_UNKNOWN
    assert _fsBadgeFor(
        _fdictGithubStatus(bVerified=False),
    )["sGithub"] == badgeState.S_BADGE_UNKNOWN


def test_a_path_the_verify_never_compared_reads_unknown():
    """The distinction listComparedPaths exists to make.

    Without it, "absent from listDiverged" reads as "matched" for a
    file the verify never looked at -- which is how a badge comes to
    claim agreement it never established.
    """
    dictStatus = _fdictGithubStatus(listCompared=["Other/file.json"])
    assert _fsBadgeFor(dictStatus)["sGithub"] == (
        badgeState.S_BADGE_UNKNOWN
    )


def test_a_legacy_cache_without_the_compared_list_reads_unknown():
    """Self-correcting rather than guessing.

    A cache written before listComparedPaths existed cannot support
    any per-file claim, so it makes none until the next verify.
    """
    dictStatus = _fdictGithubStatus()
    del dictStatus["listComparedPaths"]
    assert _fsBadgeFor(dictStatus)["sGithub"] == (
        badgeState.S_BADGE_UNKNOWN
    )


# ---------------------------------------------------------------------
# The defect, stated directly.
# ---------------------------------------------------------------------


def test_a_committed_but_never_pushed_file_does_not_claim_sync():
    """The overstatement the old badge made, as its own test.

    Clean porcelain and no GitHub verify. Under _fsGitBadge this
    rendered 'synced' -- "in sync with remote" for a remote that had
    never received the file.
    """
    dictBadges = _fsBadgeFor(None, dictGit=_fdictGit())
    assert dictBadges["sGithub"] != badgeState.S_BADGE_SYNCED, (
        "a file git calls clean is being reported as in sync with "
        "GitHub, which has never seen it"
    )
    assert dictBadges["sGithub"] == badgeState.S_BADGE_UNKNOWN


def test_local_git_truth_survives_under_its_own_key():
    """sGitState keeps what sGithub used to carry.

    The declaration row's track/untrack gate reads it, and that gate
    genuinely asks a local question. Losing it would silently hide
    'Remove from repo' for every tracked file.
    """
    dictBadges = _fsBadgeFor(
        _fdictGithubStatus(), dictGit=_fdictGit({S_FILE: "dirty"}),
    )
    assert dictBadges["sGitState"] == badgeState.S_BADGE_DIRTY
    # ...and it did not leak back onto the remote column.
    assert dictBadges["sGithub"] == badgeState.S_BADGE_SYNCED


def test_the_two_keys_can_disagree_in_both_directions():
    """The whole point: they answer different questions.

    Locally dirty while matching the published copy is ordinary (edit
    saved, not yet committed, mirror still holds the verified bytes).
    Locally clean while diverged is equally ordinary (committed, not
    pushed). Neither may be inferred from the other.
    """
    dictDirtyButMatching = _fsBadgeFor(
        _fdictGithubStatus(), dictGit=_fdictGit({S_FILE: "dirty"}),
    )
    assert dictDirtyButMatching["sGitState"] == badgeState.S_BADGE_DIRTY
    assert dictDirtyButMatching["sGithub"] == badgeState.S_BADGE_SYNCED

    dictCleanButDiverged = _fsBadgeFor(
        _fdictGithubStatus(listDiverged=[
            {"sPath": S_FILE, "sExpected": "aaa", "sActual": "bbb"},
        ]),
        dictGit=_fdictGit(),
    )
    assert dictCleanButDiverged["sGitState"] == (
        badgeState.S_BADGE_SYNCED
    )
    assert dictCleanButDiverged["sGithub"] == (
        badgeState.S_BADGE_DRIFTED
    )


def test_the_verify_records_the_paths_it_compared():
    """The badge's evidence has to actually be written down.

    fdictVerifyRemoteService is the only producer of the field the
    badge reads; a change that stopped recording it would make every
    badge unknown forever, with these unit tests still green.
    """
    import inspect
    from vaibify.reproducibility import scheduledReverify

    sSource = inspect.getsource(
        scheduledReverify.fdictVerifyRemoteService,
    )
    assert '"listComparedPaths"' in sSource, (
        "the verify no longer records which paths it compared, so no "
        "per-file badge can distinguish matched from never-checked"
    )
