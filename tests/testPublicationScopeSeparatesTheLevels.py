"""Level 2 publishes data; Level 3 publishes the envelope.

The remote verify compares a file on disk against the copy a remote
serves. Until 2026-08-26 it compared only the manifest set -- step
outputs, inputs, scripts, standards, the AI declaration -- so the
reproducibility envelope was pinned by Level 3 and compared by
nothing. A pushed ``reproduce.sh`` that had drifted from the local one
meant a third party would run something the researcher never ran, and
every surface reported Level 3 attained.

The obvious repair -- widen the comparison and let the existing gate
read it -- was WRONG, and the researcher said so: Level 2 is about
publishing the generating data, and making it depend on artifacts
whose whole purpose is Level 3 couples two rungs the ladder keeps
independent. A stale ``requirements.lock`` would have reported that
the data was not published, which is a false statement about a real
thing.

So one pass compares the union and each gate reads the paths it owns.
The test that matters most is
``test_a_diverged_envelope_leaves_level_two_alone``: it is the whole
reason the split exists, and it is the assertion that fails first if
anyone later "simplifies" the scope-aware gate back to the aggregate
counts.

Kills (confirmed, not assumed): reading iMatching/iTotalFiles in
``_fbCachedSyncStatusFullMatch`` again fails the L2-independence test;
dropping the envelope from ``flistCollectComparisonPaths`` fails the
coverage test; removing the subset check in
``fbEnvelopeMatchesGithubMirror`` fails the legacy-cache test.
"""

import pytest

from vaibify.reproducibility import levelGates, publicationScope


S_DATA = "MakeData/output.json"
S_SCRIPT = "MakeData/makeData.py"
S_PROJECT = ".vaibify/projects/project.json"
S_ENVELOPE = "reproduce.sh"
S_MARKER = ".vaibify/test_markers/project/MakeData.json"


class _FakeRepoFiles:
    """A repo whose files are whatever the test says exist."""

    def __init__(self, setPresent=(), listProjects=("project.json",)):
        self.sRootPath = "/repo"
        self._setPresent = set(setPresent)
        self._listProjects = list(listProjects)

    def fbIsFile(self, sRelPath):
        return sRelPath in self._setPresent

    def flistListJsonFilenames(self, sRelDir):
        if sRelDir == publicationScope.S_PROJECTS_DIRECTORY:
            return list(self._listProjects)
        return []


def _fdictStatus(listCompared, listDivergedPaths=(),
                 sVerified="2026-08-26T00:00:00Z", bScopeCurrent=True):
    """A syncStatus.json github entry as a real verify writes one.

    ``bScopeCurrent`` defaults True because that is what a real verify
    writes; passing False models a cache written under an EARLIER
    definition of the published set, which is a different thing from a
    stale or diverged one and is the case the gate used to wave
    through.
    """
    dictStatus = {
        "sService": "github",
        "sLastVerified": sVerified,
        "iTotalFiles": len(listCompared),
        "iMatching": len(listCompared) - len(listDivergedPaths),
        "listComparedPaths": list(listCompared),
        "listDiverged": [
            {"sPath": s, "sExpected": "aaa", "sActual": "bbb"}
            for s in listDivergedPaths
        ],
    }
    if bScopeCurrent:
        dictStatus["iScopeVersion"] = (
            publicationScope.I_PUBLICATION_SCOPE_VERSION
        )
    return dictStatus


# ---------------------------------------------------------------------
# The partition.
# ---------------------------------------------------------------------


def test_the_envelope_and_the_data_land_on_different_levels():
    listCompared = [S_DATA, S_SCRIPT, S_PROJECT, S_ENVELOPE,
                    "requirements.lock", "MANIFEST.sha256"]
    setLevel2 = publicationScope.fsetSelectLevel2Paths(listCompared)
    setLevel3 = publicationScope.fsetSelectLevel3Paths(listCompared)
    assert setLevel2 == {S_DATA, S_SCRIPT, S_PROJECT}
    assert setLevel3 == {S_ENVELOPE, "requirements.lock",
                         "MANIFEST.sha256"}
    assert not (setLevel2 & setLevel3), "a path cannot own two levels"


def test_the_project_definition_is_level_two():
    """The researcher's ruling (2026-08-25), pinned.

    It describes how the published data was generated, which is what
    Level 2 publishes. A third party also needs it, but needing a file
    to reproduce does not make it envelope -- by that argument the
    data itself would be Level 3.
    """
    assert S_PROJECT in publicationScope.fsetSelectLevel2Paths(
        [S_PROJECT],
    )
    assert publicationScope.fsetSelectLevel3Paths([S_PROJECT]) == set()


@pytest.mark.parametrize("sPath", [S_MARKER, ".gitignore"])
def test_some_tracked_files_belong_to_neither_level(sPath):
    """Compared by nothing, and the badge says so rather than nagging.

    Test markers are rewritten by every local test run, so comparing
    them would report "you ran your tests again" as a publication
    defect.
    """
    assert publicationScope.fbPathIsCompared(sPath) is False
    assert publicationScope.fsetSelectLevel2Paths([sPath]) == set()
    assert publicationScope.fsetSelectLevel3Paths([sPath]) == set()


def test_the_comparison_set_covers_the_envelope_and_the_project():
    """The verify must actually LOOK at what the gates read.

    A partition over a set that never contained the envelope would
    leave the Level 3 criterion permanently unproven while every unit
    test of the partition still passed.
    """
    dictWorkflow = {
        "sProjectRepoPath": "/repo",
        "listSteps": [{
            "sName": "Make Data", "sDirectory": "MakeData",
            # Step-RELATIVE, the way a workflow really spells it;
            # the collector prefixes sDirectory itself.
            "saOutputDataFiles": ["output.json"], "saPlotFiles": [],
            "saDataCommands": ["python3 makeData.py"],
        }],
    }
    listPaths = publicationScope.flistCollectComparisonPaths(
        dictWorkflow, _FakeRepoFiles(),
    )
    assert S_DATA in listPaths
    assert S_PROJECT in listPaths
    for sEnvelope in publicationScope.TUPLE_LEVEL3_ENVELOPE_PATHS:
        assert sEnvelope in listPaths, sEnvelope
    assert S_MARKER not in listPaths
    assert ".gitignore" not in listPaths


# ---------------------------------------------------------------------
# Independence — the property the split exists to protect.
# ---------------------------------------------------------------------


def test_a_diverged_envelope_leaves_level_two_alone():
    """THE test. Level 2 must not fail over a Level 3 artifact.

    Widening the comparison without making the gate scope-aware would
    silently make Level 2 harder, which is precisely what was ruled
    out. This is the assertion that fails first if the gate is ever
    "simplified" back to iMatching/iTotalFiles.
    """
    dictStatus = _fdictStatus(
        [S_DATA, S_SCRIPT, S_ENVELOPE], [S_ENVELOPE],
    )
    assert levelGates._fbCachedSyncStatusFullMatch(dictStatus) is True, (
        "Level 2 failed because a reproducibility-envelope file "
        "diverged — the two levels are coupled again"
    )


def test_a_diverged_data_file_still_fails_level_two():
    """The other half: the gate did not simply stop working."""
    dictStatus = _fdictStatus(
        [S_DATA, S_SCRIPT, S_ENVELOPE], [S_DATA],
    )
    assert levelGates._fbCachedSyncStatusFullMatch(dictStatus) is False


def test_a_pre_split_cache_cannot_answer_the_current_question():
    """This test asserted the OPPOSITE until 2026-08-26, and was wrong.

    The reasoning was: everything a pre-split verify compared WAS
    Level 2 material, so its aggregate is exactly the Level 2 answer,
    and demanding a re-verify would demote every project on upgrade
    for nothing. The first clause is true and the conclusion does not
    follow. The scope also GREW -- project.json joined Level 2 -- and
    an old cache is silent about the added files in precisely the way
    it is silent about files that matched. So the "complete" reading
    was complete only for a question no longer being asked.

    The cost is one Verify-now per project, once. The thing bought is
    that the row stops asserting a comparison nobody performed.
    """
    dictLegacy = {
        "sService": "github", "sLastVerified": "2026-08-26T00:00:00Z",
        "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
    }
    assert levelGates._fbCachedSyncStatusFullMatch(dictLegacy) is False


def test_a_pre_split_cache_with_a_divergence_also_fails():
    """Two independent reasons now; it must not pass on either."""
    dictLegacy = {
        "sService": "github", "sLastVerified": "2026-08-26T00:00:00Z",
        "iTotalFiles": 3, "iMatching": 2,
        "listDiverged": [{"sPath": S_DATA, "sActual": "bbb"}],
    }
    assert levelGates._fbCachedSyncStatusFullMatch(dictLegacy) is False


def test_the_legacy_fallback_does_not_reach_level_three():
    """The envelope claim gets no such benefit of the doubt.

    A pre-split verify never looked at the envelope, so there is no
    old-cache reading that supports "the published envelope matches".
    Level 3 stays blocked until a real comparison has been made --
    which is the whole point of the new criterion.
    """
    dictLegacy = {
        "sService": "github", "sLastVerified": "2026-08-26T00:00:00Z",
        "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
    }
    from vaibify.reproducibility import scheduledReverify
    import unittest.mock as mock
    with mock.patch.object(
        scheduledReverify, "fdictReadCachedSyncStatus",
        lambda filesRepo, sService: dictLegacy,
    ):
        assert levelGates.fbEnvelopeMatchesGithubMirror(
            _FakeRepoFiles({S_ENVELOPE}),
        ) is False


def test_a_verify_that_compared_nothing_is_not_a_full_match():
    """The vacuity floor the old iTotal == 0 guard supplied."""
    assert levelGates._fbCachedSyncStatusFullMatch(
        _fdictStatus([]),
    ) is False


# ---------------------------------------------------------------------
# The Level 3 criterion.
# ---------------------------------------------------------------------


def _fbEnvelopeMatches(monkeypatch, dictStatus, setPresent):
    from vaibify.reproducibility import scheduledReverify
    monkeypatch.setattr(
        scheduledReverify, "fdictReadCachedSyncStatus",
        lambda filesRepo, sService: dictStatus,
    )
    return levelGates.fbEnvelopeMatchesGithubMirror(
        _FakeRepoFiles(setPresent),
    )


def test_a_diverged_envelope_blocks_level_three(monkeypatch):
    """The gap this whole change closes."""
    assert _fbEnvelopeMatches(
        monkeypatch,
        _fdictStatus([S_DATA, S_ENVELOPE], [S_ENVELOPE]),
        {S_ENVELOPE},
    ) is False


def test_a_matching_envelope_passes_level_three(monkeypatch):
    assert _fbEnvelopeMatches(
        monkeypatch,
        _fdictStatus([S_DATA, S_ENVELOPE], [S_DATA]),
        {S_ENVELOPE},
    ) is True


@pytest.mark.falsification
def test_an_envelope_never_compared_does_not_pass(monkeypatch):
    """A cache from a hub whose verify predated the widening.

    listComparedPaths is non-empty, so a length check would call this
    proven; the file that matters is simply absent from it. Detected
    by asking whether every envelope file ON DISK appears in the
    compared set.

    Kills: drop the `set(listOnDisk).issubset(setCompared)` check in
    fbEnvelopeMatchesGithubMirror, which then reports a match over an
    envelope no verify ever looked at.
    """
    assert _fbEnvelopeMatches(
        monkeypatch,
        _fdictStatus([S_DATA, S_SCRIPT]),
        {S_ENVELOPE},
    ) is False


def test_a_project_with_no_envelope_is_not_double_reported(monkeypatch):
    """Absence is already reported by the artifact criteria.

    A second blocker saying the same thing would send the researcher
    hunting a sync problem they do not have.
    """
    assert _fbEnvelopeMatches(
        monkeypatch, _fdictStatus([S_DATA]), set(),
    ) is True


def test_an_unverified_project_does_not_claim_a_matching_envelope(
    monkeypatch,
):
    """Unproven blocks, symmetric with the Level 2 gate."""
    assert _fbEnvelopeMatches(
        monkeypatch,
        _fdictStatus([S_DATA, S_ENVELOPE], sVerified=None),
        {S_ENVELOPE},
    ) is False


def test_the_criterion_is_registered_with_a_usable_hint():
    """A blocker nobody can act on is a worse blocker than none."""
    sHint = levelGates._DICT_L3_REMEDIATION_HINTS[
        "envelope-not-in-github-mirror"
    ]
    assert "Verify now" in sHint, sHint
    assert "GitHub" in sHint, sHint


# ---------------------------------------------------------------------
# The gate runs under the POLL adapter, which answers a fixed set.
# ---------------------------------------------------------------------
#
# Every test above this line drives `_FakeRepoFiles`, whose `fbIsFile`
# answers any path a caller asks about. The adapter the file-status
# poll really passes -- `SnapshotRepoFiles` -- answers exactly the
# paths one container exec sampled and raises `KeyError` for the rest,
# deliberately, because guessing would make a gate silently wrong.
#
# So the permissive fake agreed with itself while the shipped gate
# raised on `requirements.txt` and 500'd the whole poll: every badge
# and every level cell on the researcher's dashboard went blank. The
# two tests below close that gap from both ends -- one pins the set
# relationship at the source, the other drives the real adapter over a
# real tree so a future envelope addition fails here rather than in a
# browser.


class _FakeExecConnection:
    """Run the snapshot's embedded script in a host shell, for real."""

    def ftRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        import subprocess
        from types import SimpleNamespace

        resultProcess = subprocess.run(
            ["bash", "-c", sCommand], capture_output=True, text=True,
        )
        return SimpleNamespace(
            iExitCode=resultProcess.returncode,
            sStdout=resultProcess.stdout,
            sStderr=resultProcess.stderr,
        )


def test_the_poll_snapshot_samples_every_envelope_path():
    """Pin the relationship, not the spelling.

    The envelope tuple and the snapshot's sampled set are edited in
    different modules for different reasons, and nothing about either
    edit looks wrong on its own. This is the assertion that makes the
    second edit compulsory.
    """
    from vaibify.reproducibility.repoFiles import (
        TUPLE_SNAPSHOT_CONTENT_PATHS,
    )

    setUnsampled = (
        set(publicationScope.TUPLE_LEVEL3_ENVELOPE_PATHS)
        - set(TUPLE_SNAPSHOT_CONTENT_PATHS)
    )
    assert not setUnsampled, (
        "these envelope paths are probed by the Level 3 gate but not "
        "sampled by the poll snapshot, so fbIsFile raises KeyError and "
        "the file-status poll answers 500 -- blanking every badge and "
        f"level cell on the dashboard: {sorted(setUnsampled)}"
    )


def test_the_level_three_gate_survives_the_real_poll_adapter(tmp_path):
    """Drive the gate through the adapter the poll actually passes.

    Not a unit stub: the embedded snapshot script runs over a real
    tree, and the gate then probes it exactly as the poll route does.
    Before the sampled set was widened this raised ``KeyError: path
    not in poll snapshot: 'requirements.txt'``.
    """
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles

    (tmp_path / ".vaibify").mkdir()
    (tmp_path / "reproduce.sh").write_text("#!/bin/sh\necho run\n")
    (tmp_path / "requirements.txt").write_text("numpy==1.26.4\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")

    filesSnapshot = SnapshotRepoFiles.ffilesFetch(
        _FakeExecConnection(), "cid", str(tmp_path),
    )

    # Existence is answered for every envelope path, present or not.
    listOnDisk = levelGates._flistEnvelopePathsOnDisk(filesSnapshot)
    assert set(listOnDisk) == {
        "reproduce.sh", "requirements.txt", "pyproject.toml",
    }, listOnDisk

    # And the whole criterion evaluates rather than raising. No verify
    # has ever run against this tree, so the honest answer is False.
    assert levelGates.fbEnvelopeMatchesGithubMirror(filesSnapshot) is False


def test_the_poll_snapshot_answers_existence_without_carrying_bodies(
    tmp_path,
):
    """The dependency declarations cost a stat, not a transfer.

    They are sampled for existence only. Asserting this keeps a later
    reader from "fixing" the skip list and quietly putting a research
    repo's largest files on every poll.
    """
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles

    (tmp_path / ".vaibify").mkdir()
    (tmp_path / "requirements.txt").write_text("numpy==1.26.4\n")

    filesSnapshot = SnapshotRepoFiles.ffilesFetch(
        _FakeExecConnection(), "cid", str(tmp_path),
    )
    assert filesSnapshot.fbIsFile("requirements.txt") is True
    with pytest.raises(FileNotFoundError):
        filesSnapshot.fsReadText("requirements.txt")


# ---------------------------------------------------------------------
# The split has to reach the SCREEN, not only the gate.
# ---------------------------------------------------------------------
#
# The first cut made the gates scope-aware and stopped there. The
# Published-copies row beside them kept reporting the aggregate, so a
# researcher with a drifted reproduce.sh saw the Level 2 GitHub row go
# orange and list reproduce.sh among the files -- the row making
# exactly the statement the gate had just been taught not to make.
# A split visible only to the backend is not a split the researcher
# has.


def test_the_level_two_counts_leave_the_envelope_out():
    dictStatus = _fdictStatus(
        [S_DATA, S_SCRIPT, S_PROJECT, S_ENVELOPE, "requirements.lock"],
        listDivergedPaths=[S_ENVELOPE],
    )
    dictCounts = publicationScope.fdictCountAtLevel2(dictStatus)
    assert dictCounts["iTotalFiles"] == 3, dictCounts
    assert dictCounts["iMatching"] == 3, dictCounts
    assert dictCounts["iDivergedCount"] == 0, (
        "a diverged reproduce.sh is being counted against the "
        f"researcher's DATA: {dictCounts}"
    )


def test_a_diverged_data_file_still_counts_at_level_two():
    """The complement of the test above; without it, always-zero passes."""
    dictStatus = _fdictStatus(
        [S_DATA, S_SCRIPT, S_ENVELOPE],
        listDivergedPaths=[S_DATA, S_ENVELOPE],
    )
    dictCounts = publicationScope.fdictCountAtLevel2(dictStatus)
    assert dictCounts["iTotalFiles"] == 2, dictCounts
    assert dictCounts["iDivergedCount"] == 1, dictCounts
    assert dictCounts["iMatching"] == 1, dictCounts


def test_a_pre_split_cache_keeps_its_counts():
    """No project loses a verified row by upgrading the hub.

    A cache written before the split carries no listComparedPaths, and
    its compared set was entirely Level 2 material, so its aggregate
    IS the Level 2 answer.
    """
    dictStatus = _fdictStatus([S_DATA, S_SCRIPT], listDivergedPaths=[S_DATA])
    del dictStatus["listComparedPaths"]
    dictCounts = publicationScope.fdictCountAtLevel2(dictStatus)
    assert dictCounts == {
        "iTotalFiles": 2, "iMatching": 1, "iDivergedCount": 1,
    }, dictCounts


@pytest.mark.falsification
def test_the_route_summary_reports_the_level_two_counts():
    """Assert the projection ARRIVES, with a value the aggregate can't give.

    The counts differ from the aggregate only because the envelope was
    subtracted, so this fails if the route is ever simplified back to
    reading iTotalFiles/iMatching straight off the cache -- the shape
    that looks correct and says the wrong thing.

    Kills: replace the publicationScope.fdictCountAtLevel2 call in
    _fdictProjectSyncSummary with the cache's raw aggregate counts.
    """
    from vaibify.gui.routes.pipelineRoutes import _fdictProjectSyncSummary

    dictStatus = _fdictStatus(
        [S_DATA, S_SCRIPT, S_ENVELOPE, "MANIFEST.sha256"],
        listDivergedPaths=[S_ENVELOPE, "MANIFEST.sha256"],
    )
    dictSummary = _fdictProjectSyncSummary(dictStatus)
    assert dictSummary["iTotalFiles"] == 2, dictSummary
    assert dictSummary["iDivergedCount"] == 0, (
        "the Level 2 row is reporting envelope divergences as reasons "
        f"the data is unpublished: {dictSummary}"
    )
    assert dictSummary["sLastVerified"], dictSummary


def test_the_envelope_paths_present_are_offered_to_the_client():
    """The Level 3 row's file list, from the one authority on the set."""
    filesRepo = _FakeRepoFiles(setPresent={S_ENVELOPE, S_DATA})
    listPresent = publicationScope.flistSelectEnvelopePathsPresent(
        filesRepo,
    )
    assert listPresent == [S_ENVELOPE], listPresent


# ---------------------------------------------------------------------
# A mark the researcher cannot clear must not look like a to-do.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_never_compared_file_gets_its_own_badge_not_an_orange_todo():
    """Orange says "nobody has looked yet"; these will never be looked at.

    Test markers are rewritten by every local test run and .gitignore
    governs the repository rather than describing the work, so no
    verify compares either -- and `unknown` would leave the researcher
    an instruction ("run a verify") that changes nothing when followed.

    Kills: drop the fbPathIsCompared branch in _fsVerifiedRemoteBadge,
    which returns these files to an orange to-do nothing can clear.
    """
    from vaibify.gui import badgeState

    dictStatus = _fdictStatus([S_DATA, S_SCRIPT])
    for sPath in (S_MARKER, ".gitignore"):
        sBadge = badgeState._fsVerifiedRemoteBadge(sPath, dictStatus)
        assert sBadge == badgeState.S_BADGE_NOT_COMPARED, (
            f"{sPath} renders as {sBadge!r}, which asks the researcher "
            "to run a verify that will skip this file forever"
        )


def test_a_compared_file_still_reaches_the_ordinary_states():
    """The complement: the new branch must not swallow real answers."""
    from vaibify.gui import badgeState

    dictStatus = _fdictStatus(
        [S_DATA, S_SCRIPT], listDivergedPaths=[S_DATA],
    )
    assert badgeState._fsVerifiedRemoteBadge(
        S_SCRIPT, dictStatus) == badgeState.S_BADGE_SYNCED
    assert badgeState._fsVerifiedRemoteBadge(
        S_DATA, dictStatus) == badgeState.S_BADGE_DRIFTED
    assert badgeState._fsVerifiedRemoteBadge(
        "Never/Verified.json", dictStatus) == badgeState.S_BADGE_UNKNOWN


def test_the_envelope_criterion_reaches_the_proof_tab_payload(tmp_path):
    """A blocker with no row is an unexplained dash to the researcher.

    The PROOF tab binds its L3 rows to `dictL3ReadinessGaps` keys, so
    a criterion absent from that payload can block Level 3 with
    nothing on screen naming it.
    """
    from vaibify.reproducibility.repoFiles import HostRepoFiles

    dictWorkflow = {"sProjectRepoPath": "/repo", "listSteps": []}
    dictGaps = levelGates.fdictL3ReadinessGaps(
        dictWorkflow, HostRepoFiles(str(tmp_path)),
    )
    assert "bEnvelopeInGithubMirror" in dictGaps, sorted(dictGaps)


def test_publishing_is_not_a_precondition_for_attesting_locally():
    """Readiness asks about the LOCAL envelope; publication is separate.

    Folding the mirror check into the readiness all() would stop a
    researcher attesting a complete local envelope until they had
    pushed it — a different rung's requirement reaching down, which is
    the coupling this whole split exists to prevent.
    """
    dictFlags = levelGates._fdictCollectL3ReadinessFlags(
        {"sProjectRepoPath": "/repo", "listSteps": []},
        _FakeRepoFiles(setPresent={S_ENVELOPE}),
        True,
    )
    assert "bEnvelopeInGithubMirror" not in dictFlags, sorted(dictFlags)


# ---------------------------------------------------------------------
# Absence of evidence is not agreement.
# ---------------------------------------------------------------------
#
# The gate can only ask "did anything that WAS compared diverge?" A
# file the verify never looked at is missing from listDiverged in
# exactly the way a file that matched is missing. So when the Level 2
# scope grew, every cached verify kept reporting a full match while
# newly-covered files sat uncompared beside it. The scope version is
# what makes the two cases distinguishable.


def test_a_cache_from_an_older_scope_cannot_carry_level_two():
    """The defect, stated directly.

    Every field says "complete": nothing diverged, everything compared
    matched. The only thing wrong is that the set it compared was
    defined before the current one, so files now in scope were never
    looked at -- and the gate has no other way to know.
    """
    dictStatus = _fdictStatus([S_DATA, S_SCRIPT], bScopeCurrent=False)
    assert levelGates._fbCachedSyncStatusFullMatch(dictStatus) is False


def test_a_current_scope_cache_with_no_divergence_does_carry_it():
    """The complement: the check must not refuse everything."""
    dictStatus = _fdictStatus([S_DATA, S_SCRIPT, S_PROJECT])
    assert levelGates._fbCachedSyncStatusFullMatch(dictStatus) is True


def test_a_current_scope_cache_still_fails_on_a_real_divergence():
    dictStatus = _fdictStatus(
        [S_DATA, S_SCRIPT], listDivergedPaths=[S_DATA],
    )
    assert levelGates._fbCachedSyncStatusFullMatch(dictStatus) is False


def test_an_envelope_divergence_still_leaves_level_two_alone():
    """The independence property, re-asserted under scope versioning.

    This is the assertion the whole split exists for, and the scope
    check must not quietly take it away by refusing every cache that
    contains an envelope divergence.
    """
    dictStatus = _fdictStatus(
        [S_DATA, S_SCRIPT, S_ENVELOPE],
        listDivergedPaths=[S_ENVELOPE],
    )
    assert levelGates._fbCachedSyncStatusFullMatch(dictStatus) is True


def test_a_never_verified_service_is_not_scope_current():
    """The empty status must not present as evidence of anything."""
    from vaibify.reproducibility import scheduledReverify

    dictEmpty = scheduledReverify._fdictEmptyServiceStatus("github")
    assert not publicationScope.fbCachedScopeIsCurrent(dictEmpty)


def test_the_row_reports_scope_staleness_rather_than_a_pass():
    """The screen must not disagree with the gate.

    An orange "verify again" is the honest rendering: the researcher
    has published nothing wrong, they have no evidence yet about the
    newly-covered files. Red would accuse them of something untrue.
    """
    from vaibify.gui.routes.pipelineRoutes import _fdictProjectSyncSummary

    dictSummary = _fdictProjectSyncSummary(
        _fdictStatus([S_DATA], bScopeCurrent=False),
    )
    assert dictSummary["bScopeStale"] is True, dictSummary

    dictCurrent = _fdictProjectSyncSummary(
        _fdictStatus([S_DATA]),
    )
    assert dictCurrent["bScopeStale"] is False, dictCurrent


# ---------------------------------------------------------------------
# A refusal must name itself.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_stale_scope_emits_a_blocker_rather_than_a_silent_refusal(
    tmp_path,
):
    """The gate refused on scope while the blocker list said nothing.

    _flistGithubLevel2Blockers consulted the clock and the divergence
    list and never the scope, so a cache verified under an earlier
    definition blocked Level 2 with no reason attached -- the same
    unexplained-dash failure the Level 3 envelope row was given a
    criterion to avoid. It reuses `github-verify-stale` because the
    criterion is named for the VERIFICATION being stale, not the
    clock, and its existing remediation ("re-verify") is already the
    correct instruction.

    Kills: drop the fbCachedScopeIsCurrent check from
    _fbSyncCacheStale, which returns Level 2 to refusing silently.
    """
    import json
    import os
    from tests.syncStatusFixtures import fdictBuildCachedVerify

    sRepo = str(tmp_path)
    os.makedirs(os.path.join(sRepo, ".vaibify"), exist_ok=True)
    dictWorkflow = {
        "sProjectRepoPath": sRepo,
        "listSteps": [{
            "sName": "A", "sDirectory": "A", "bNoInputData": True,
            "dictVerification": {sKey: "passed" for sKey in (
                "sUser", "sUnitTest", "sIntegrity",
                "sQualitative", "sQuantitative",
            )},
        }],
        "dictRemotes": {"github": {
            "sOwner": "u", "sRepo": "r", "sBranch": "main",
            "sCommittedSha": "abc123",
        }},
    }
    with open(
        os.path.join(sRepo, ".vaibify", "syncStatus.json"), "w",
    ) as fileStatus:
        json.dump({"github": fdictBuildCachedVerify(
            sLastVerified="2026-08-26T00:00:00Z",
            bScopeCurrent=False,
            sCommittedShaVerified="abc123",
        )}, fileStatus)

    levelGates.fnClearLevelBlockerCache()
    listCriteria = [
        dictEntry.get("sCriterion")
        for dictEntry in levelGates.flistLevel2Blockers(dictWorkflow, sRepo)
    ]
    assert "github-verify-stale" in listCriteria, (
        "Level 2 is refused on scope with nothing in the blocker list "
        f"naming GitHub, so the researcher gets no reason: {listCriteria}"
    )
