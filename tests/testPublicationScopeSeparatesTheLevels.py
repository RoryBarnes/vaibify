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


def _fdictStatus(listCompared, listDivergedPaths=(), sVerified="2026-08-26T00:00:00Z"):
    """A syncStatus.json github entry as a real verify writes one."""
    return {
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


def test_a_pre_split_cache_still_answers_level_two_on_its_own_terms():
    """A cache with no scope field is not thereby useless.

    Everything a pre-split verify compared WAS Level 2 material --
    the envelope was not in the comparison set at all then -- so its
    aggregate is exactly the Level 2 answer rather than an
    approximation. Demanding a re-verify for a claim the old cache
    can support would have demoted every existing project on upgrade,
    which is a cost with nothing bought.
    """
    dictLegacy = {
        "sService": "github", "sLastVerified": "2026-08-26T00:00:00Z",
        "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
    }
    assert levelGates._fbCachedSyncStatusFullMatch(dictLegacy) is True


def test_a_pre_split_cache_with_a_divergence_still_fails():
    """The fallback reads the old shape; it does not wave it through."""
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


def test_an_envelope_never_compared_does_not_pass(monkeypatch):
    """A cache from a hub whose verify predated the widening.

    listComparedPaths is non-empty, so a length check would call this
    proven; the file that matters is simply absent from it. Detected
    by asking whether every envelope file ON DISK appears in the
    compared set.
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
