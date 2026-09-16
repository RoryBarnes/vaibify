"""Running the Level 3 verification must not cost you Level 2.

Measured on a real project, 2026-09-16. After a regeneration five files
diverged from both GitHub and Zenodo. Four were envelope files, which
gate Level 3 and correctly say nothing about Level 2. The fifth was
``.vaibify/l3_attestation.json`` -- and it was the ONLY Level 2
blocker, so the project sat at Level 1.

That file lives in ``TUPLE_COMPARED_NOT_REQUIRED_PATHS``, whose own
docstring says it is "compared against every remote, and required by NO
criterion... keeping it out of ``TUPLE_LEVEL3_ENVELOPE_PATHS`` is what
stops that badge from gating a level." The opposite was true.
``fsetSelectLevel2Paths`` defines Level 2 as the COMPLEMENT of the
envelope within the compared set, so a path kept out of the envelope
tuple lands in Level 2 -- the one scope the complement sweeps
everything into.

The loop that made it fatal: running a Level 3 verification is what
writes the attestation, so attaining Level 3 dropped the project below
Level 2, and the remedy for that (push, then publish a new immutable
Zenodo version) was undone by the next verification. No button exited
it.

The general rule this file defends is the one in
``testAdvancingALevelNeverLowersOne.py``, met here in its sharpest
form: a file a level writes must not be a file a lower level compares.
"""

import pytest

from vaibify.reproducibility import levelGates, publicationScope


_S_ATTESTATION = ".vaibify/l3_attestation.json"
_S_PROVENANCE_STAMP = ".vaibify/ai_provenance.json"


def _fdictSyncStatus(listCompared, listDivergedPaths):
    """Build a cache in the shape a completed remote verify writes."""
    return {
        "sService": "github",
        "sLastVerified": "2026-09-16T03:50:28Z",
        "iScopeVersion": publicationScope.I_PUBLICATION_SCOPE_VERSION,
        "iTotalFiles": len(listCompared),
        "iMatching": len(listCompared) - len(listDivergedPaths),
        "listComparedPaths": list(listCompared),
        "listDiverged": [
            {"sPath": sPath, "sExpected": "a" * 64, "sActual": "b" * 64}
            for sPath in listDivergedPaths
        ],
    }


# The set a real verify compared on the project this was found on,
# reduced to one representative of each category.
_LIST_COMPARED = [
    "Data/result.csv",                 # published data: Level 2
    "MANIFEST.sha256",                 # envelope: Level 3
    "requirements.lock",               # envelope: Level 3
    ".vaibify/environment.json",       # envelope: Level 3
    _S_ATTESTATION,                    # compared, required by nothing
    _S_PROVENANCE_STAMP,               # compared, an L4 concern
]


@pytest.mark.falsification
def test_a_diverged_rebuild_attestation_does_not_block_level_two():
    """The file a Level 3 run writes is not a Level 2 criterion.

    Asserted through the GATE and the BLOCKER list together, because
    the two are required to fail on the same set and they derive the
    selection from the same helper. A test on the helper alone would
    pass against a blocker list that still named the file.

    Kills: dropping ``TUPLE_COMPARED_NOT_REQUIRED_PATHS`` from the
    Level 2 selection -- the shipped state, in which attaining Level 3
    dropped a project to Level 1 with no way out.
    """
    dictStatus = _fdictSyncStatus(_LIST_COMPARED, [_S_ATTESTATION])
    setLevel2 = publicationScope.fsetSelectLevel2Paths(_LIST_COMPARED)
    assert _S_ATTESTATION not in setLevel2, sorted(setLevel2)
    assert "Data/result.csv" in setLevel2, (
        "the published data left Level 2 scope, so this test is no "
        f"longer about the attestation: {sorted(setLevel2)}"
    )
    assert levelGates._fbCachedSyncStatusFullMatch(dictStatus), (
        "a diverged rebuild attestation refused Level 2, which is the "
        "loop: the Level 3 run writes that file"
    )
    assert levelGates._flistUnclaimedSyncBlockers(
        dictStatus, set(), "not-in-github-mirror", "push",
    ) == [], "the blocker list named a file the gate does not refuse for"


@pytest.mark.falsification
def test_diverged_published_data_still_blocks_level_two():
    """The exclusion is narrow: real Level 2 divergence still refuses.

    The opposite failure is the worse one -- a Level 2 claim that
    passes over unpublished data -- so it gets its own assertion
    rather than riding on the test above.

    Kills: excluding every compared path from Level 2, or widening
    the exclusion tuple to swallow published outputs.
    """
    dictStatus = _fdictSyncStatus(_LIST_COMPARED, ["Data/result.csv"])
    assert not levelGates._fbCachedSyncStatusFullMatch(dictStatus), (
        "Level 2 passed over a published data file that does not "
        "match the remote"
    )
    listBlockers = levelGates._flistUnclaimedSyncBlockers(
        dictStatus, set(), "not-in-github-mirror", "push",
    )
    assert listBlockers, "the refusal named nothing the researcher can act on"


def test_a_diverged_envelope_file_is_level_three_business_only():
    """Unchanged behaviour, asserted because the same edit could break it."""
    dictStatus = _fdictSyncStatus(_LIST_COMPARED, ["requirements.lock"])
    assert levelGates._fbCachedSyncStatusFullMatch(dictStatus)
    assert levelGates._flistUnclaimedSyncBlockers(
        dictStatus, set(), "not-in-github-mirror", "push",
    ) == []


@pytest.mark.falsification
def test_a_diverged_ai_provenance_stamp_gates_nothing():
    """The stamp is an L4 concern: shown truthfully, required by nothing.

    Ruled 2026-09-16. The stamp is machine-written by the hub, so the
    attestation's failure shape applies to it verbatim: swept into
    Level 2 by the complement rule, the file vaibify itself rewrites
    would hold the researcher's data-publication claim hostage. And it
    must not gate Level 3 either -- there is no Level 4 rung to hang
    it on, and hanging it one rung down would invent a requirement
    the ruling refused.

    Kills: dropping ``S_AI_PROVENANCE_REPO_PATH`` from
    ``TUPLE_COMPARED_NOT_REQUIRED_PATHS`` -- the complement rule then
    lands the stamp in Level 2, the second instance of the exact bug
    this file was written for.
    """
    assert _S_PROVENANCE_STAMP == publicationScope.S_AI_PROVENANCE_REPO_PATH
    dictStatus = _fdictSyncStatus(_LIST_COMPARED, [_S_PROVENANCE_STAMP])
    setLevel2 = publicationScope.fsetSelectLevel2Paths(_LIST_COMPARED)
    assert _S_PROVENANCE_STAMP not in setLevel2, sorted(setLevel2)
    assert _S_PROVENANCE_STAMP not in (
        publicationScope.TUPLE_LEVEL3_ENVELOPE_PATHS
    ), "the stamp entered the envelope tuple; that is a ladder change"
    assert levelGates._fbCachedSyncStatusFullMatch(dictStatus), (
        "a diverged provenance stamp refused Level 2; the hub "
        "rewrites this file itself"
    )
    assert levelGates._flistUnclaimedSyncBlockers(
        dictStatus, set(), "not-in-github-mirror", "push",
    ) == [], "the blocker list named a file no criterion requires"
