"""Level 2 requires the published copies to be PUBLIC, and to be about this remote.

The paper's Level 2 table lists "GitHub repository is public" and
"Zenodo archive is public" beside the two "files match" rows. Two
authorities in ``levelGates`` answer them --
``fdictDescribePublicAccess`` and ``fdictDescribePublishedCopy`` -- and
the scalar gate, the gaps dict, the blockers and the header read those
two and nothing else.

Both answer "unknown" when the record cannot answer: none yet, stale,
written before the verify asked what a reader is served, or about a
remote other than the one the project publishes to now. That last case
was a real gap: the record stored only the commit for GitHub and only
"some DOI" for Zenodo, so a renamed repository, a switched branch or a
newly declared record kept Level 2 on evidence about something else.

Every cache here is built by ``syncStatusFixtures``; every verdict the
gate is asked about is one a real verify writes.
"""

import json
import os

import pytest

from tests.syncStatusFixtures import (
    DICT_DEFAULT_REMOTE_IDENTITY,
    fdictBuildCachedVerify,
    fsRecentVerifyIso,
)
from vaibify.reproducibility import levelGates, scheduledReverify


S_STEP_OUTPUT = "Step/out.csv"
S_BINDING_KEY = scheduledReverify.S_HOST_GITHUB_BINDING_KEY


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    sRepo = str(tmp_path / "project")
    os.makedirs(os.path.join(sRepo, "Step"))
    with open(os.path.join(sRepo, "Step", "out.csv"), "w") as fileOut:
        fileOut.write("a,b\n")
    return sRepo


def _fdictWorkflow(sRepo, dictGithub=None, dictZenodo=None):
    """A workflow whose AI answers are complete, so only the remotes decide."""
    return {
        "sProjectRepoPath": sRepo,
        "dictRemotes": {
            "github": dict(dictGithub if dictGithub is not None
                           else {"sOwner": "someone", "sRepo": "something"}),
            "zenodo": dict(dictZenodo if dictZenodo is not None
                           else {"sDoi": "10.5281/zenodo.1"}),
        },
        "listSteps": [{
            "sName": "Step", "sDirectory": "Step",
            "saOutputDataFiles": ["out.csv"], "saPlotFiles": [],
        }],
        "dictAiProvenance": {
            "listDeclaredModels": [{
                "sVendor": "vendor", "sModelId": "model",
                "sUseStartDate": "2026-01-01",
                "sUseEndDate": "2026-01-02", "bOpenWeights": False,
            }],
            "dictPersonalLayer": {"sStatus": "none"},
        },
    }


def _fnBind(dictWorkflow, sOwner, sRepo, sBranch=""):
    """Record, as the hub does, where this project's push landed."""
    dictWorkflow[S_BINDING_KEY] = {dictWorkflow["sProjectRepoPath"]: {
        "sOwner": sOwner, "sRepo": sRepo, "sBranch": sBranch,
    }}


def _fdictCache(sService, **dictOverrides):
    """A fresh, matching record, as a successful verify writes one."""
    dictFields = {
        "sService": sService, "listComparedPaths": [S_STEP_OUTPUT],
        "sLastVerified": fsRecentVerifyIso(),
    }
    if sService == "zenodo":
        dictFields.update(
            sZenodoDoi="10.5281/zenodo.1", sEndpointVerified="sandbox")
    dictFields.update(dictOverrides)
    return fdictBuildCachedVerify(**dictFields)


def _fnWriteCaches(sRepo, dictGithub=None, dictZenodo=None):
    """Write both services' records; a None argument means a green one."""
    dictAll = {
        "github": dictGithub if dictGithub is not None
        else _fdictCache("github"),
        "zenodo": dictZenodo if dictZenodo is not None
        else _fdictCache("zenodo"),
    }
    os.makedirs(os.path.join(sRepo, ".vaibify"), exist_ok=True)
    with open(os.path.join(sRepo, ".vaibify", "syncStatus.json"), "w") as fileOut:
        json.dump(dictAll, fileOut)
    levelGates.fnClearLevelBlockerCache()


@pytest.fixture
def fixtureOnlyTheRemotesDecide(monkeypatch):
    """Pass the conjuncts no remote decides, so a remote alone can refuse."""
    monkeypatch.setattr(levelGates, "fbAtLeastLevel1", lambda *a: True)
    monkeypatch.setattr(
        levelGates, "fbWorkflowAiDeclarationAttested", lambda *a: True)


def _flistCriteria(dictWorkflow, sRepo):
    return [d["sCriterion"]
            for d in levelGates.flistLevel2Blockers(dictWorkflow, sRepo)]


# ── the scalar gate ──────────────────────────────────────────────────


def test_a_matching_public_project_reaches_level_two(
    fixtureProjectRepo, fixtureOnlyTheRemotesDecide,
):
    """The positive control every refusal below is measured against."""
    _fnWriteCaches(fixtureProjectRepo)
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo)
    assert levelGates._fbComputeLevel2(dictWorkflow, fixtureProjectRepo) is True


@pytest.mark.falsification
def test_a_matching_private_repository_is_below_level_two(
    fixtureProjectRepo, fixtureOnlyTheRemotesDecide,
):
    """Every file matches, fresh, about this remote -- and no reader can see it.

    This is the reported shape: the verify hashed with the researcher's
    own token, so a private repository's files matched and Level 2 was
    reached on a copy nobody else could retrieve.

    Kills: removing the fbGithubRepositoryIsPublic conjunct from
    _fbComputeLevel2.
    """
    _fnWriteCaches(fixtureProjectRepo, dictGithub=_fdictCache(
        "github", sPublicAccess="not-public"))
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo)
    assert levelGates.fbWorkflowFullySyncedWithGithub(
        dictWorkflow, fixtureProjectRepo) is True
    assert levelGates._fbComputeLevel2(dictWorkflow, fixtureProjectRepo) is False


@pytest.mark.falsification
def test_a_matching_archive_a_reader_cannot_open_is_below_level_two(
    fixtureProjectRepo, fixtureOnlyTheRemotesDecide,
):
    """The gate refuses a not-public archive whatever wrote the record.

    The verify withholds the comparison of an archive a reader cannot
    open, so it never writes "matched" beside "not public" -- but the
    gate must not depend on the writer for that.

    Kills: removing the fbZenodoArchiveIsPublic conjunct from
    _fbComputeLevel2.
    """
    _fnWriteCaches(fixtureProjectRepo, dictZenodo=_fdictCache(
        "zenodo", sPublicAccess="not-public"))
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo)
    assert levelGates.fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, fixtureProjectRepo) is True
    assert levelGates._fbComputeLevel2(dictWorkflow, fixtureProjectRepo) is False


@pytest.mark.falsification
def test_the_public_predicate_checks_freshness(fixtureProjectRepo):
    """A day-old "public" says nothing about today.

    Kills: dropping the staleness clause from _fsDescribeEvidenceGap.
    """
    _fnWriteCaches(fixtureProjectRepo, dictGithub=_fdictCache(
        "github", sLastVerified=fsRecentVerifyIso(fHoursAgo=48.0)))
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo)
    assert levelGates.fbGithubRepositoryIsPublic(
        dictWorkflow, fixtureProjectRepo) is False
    dictAccess = levelGates.fdictDescribePublicAccess(
        dictWorkflow, fixtureProjectRepo, "github")
    assert dictAccess["sState"] == "unknown"
    assert "out of date" in dictAccess["sReason"]


@pytest.mark.falsification
def test_a_record_that_never_asked_about_readers_is_unknown(fixtureProjectRepo):
    """Absent is not public, whatever else the record proves.

    The record is fresh, matching and about this remote; it just never
    asked what a reader is served. A kindness that read the absence as
    public, so that nobody's level dropped on upgrading, would put every
    private repository verified before the question existed back at
    Level 2.

    Kills: fdictDescribePublicAccess reading an absent verdict as public.
    """
    _fnWriteCaches(fixtureProjectRepo, dictGithub=_fdictCache(
        "github", sPublicAccess=None))
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo)
    dictAccess = levelGates.fdictDescribePublicAccess(
        dictWorkflow, fixtureProjectRepo, "github")
    assert dictAccess["sState"] == "unknown"
    assert "before vaibify asked" in dictAccess["sReason"]
    assert levelGates.fbGithubRepositoryIsPublic(
        dictWorkflow, fixtureProjectRepo) is False


def test_a_cache_written_before_this_change_reads_unknown_on_both_questions(
    fixtureProjectRepo,
):
    """A2: the real pre-upgrade shape has neither field, and both read unknown."""
    _fnWriteCaches(
        fixtureProjectRepo,
        dictGithub=_fdictCache("github", sPublicAccess=None,
                               bRemoteIdentity=False),
        dictZenodo=_fdictCache("zenodo", sPublicAccess=None,
                               bRemoteIdentity=False),
    )
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo)
    for sService in ("github", "zenodo"):
        for fdictDescribe in (levelGates.fdictDescribePublicAccess,
                              levelGates.fdictDescribePublishedCopy):
            assert fdictDescribe(
                dictWorkflow, fixtureProjectRepo, sService,
            )["sState"] == "unknown"
    assert levelGates.fbWorkflowFullySyncedWithGithub(
        dictWorkflow, fixtureProjectRepo) is False
    assert set(_flistCriteria(dictWorkflow, fixtureProjectRepo)) >= {
        "github-verify-stale", "zenodo-verify-stale"}


# ── which remote the evidence is about ───────────────────────────────


@pytest.mark.falsification
def test_a_repository_change_invalidates_the_evidence(fixtureProjectRepo):
    """Recorded about A, pushed now to B, same commit: no claim about B.

    Kills: dropping the recorded-versus-binding comparison from
    _fsDescribeUncoveredGithubRemote.
    """
    _fnWriteCaches(fixtureProjectRepo, dictGithub=_fdictCache(
        "github", sCommittedShaVerified="c0ffee"))
    dictWorkflow = _fdictWorkflow(
        fixtureProjectRepo, dictGithub={"sCommittedSha": "c0ffee"})
    _fnBind(dictWorkflow, "someone", "something")
    assert levelGates.fbWorkflowFullySyncedWithGithub(
        dictWorkflow, fixtureProjectRepo) is True
    _fnBind(dictWorkflow, "someone", "elsewhere")
    assert levelGates.fbWorkflowFullySyncedWithGithub(
        dictWorkflow, fixtureProjectRepo) is False
    dictCopy = levelGates.fdictDescribePublishedCopy(
        dictWorkflow, fixtureProjectRepo, "github")
    assert dictCopy["sState"] == "unknown"
    assert "someone/something" in dictCopy["sReason"]
    # Published copies shows this sentence where the counts would be.
    assert dictCopy["sReason"].startswith("Not compared: ")


@pytest.mark.falsification
def test_removing_a_declared_branch_invalidates_the_evidence(fixtureProjectRepo):
    """A record about ``release``; the declaration stops naming a branch.

    The next verify reads the default branch, so a record about
    ``release`` answers nothing about it. An omitted branch read as
    "any branch" kept that record matched and public (found in review,
    2026-10-31).

    Kills: _fsEffectiveGithubBranch dropping the verify's default.
    """
    dictRelease = dict(DICT_DEFAULT_REMOTE_IDENTITY["github"], sBranch="release")
    _fnWriteCaches(fixtureProjectRepo, dictGithub=_fdictCache(
        "github", dictRemoteIdentity=dictRelease))
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo, dictGithub={
        "sOwner": "someone", "sRepo": "something", "sBranch": "release"})
    assert levelGates.fbWorkflowFullySyncedWithGithub(
        dictWorkflow, fixtureProjectRepo) is True
    del dictWorkflow["dictRemotes"]["github"]["sBranch"]
    assert levelGates.fbWorkflowFullySyncedWithGithub(
        dictWorkflow, fixtureProjectRepo) is False
    for fdictDescribe in (levelGates.fdictDescribePublicAccess,
                          levelGates.fdictDescribePublishedCopy):
        dictState = fdictDescribe(dictWorkflow, fixtureProjectRepo, "github")
        assert dictState["sState"] == "unknown", dictState
        assert "branch release" in dictState["sReason"]


def test_a_binding_naming_no_branch_still_reads_the_default(fixtureProjectRepo):
    """The binding path resolves an absent branch the same way."""
    dictRelease = dict(DICT_DEFAULT_REMOTE_IDENTITY["github"], sBranch="release")
    _fnWriteCaches(fixtureProjectRepo, dictGithub=_fdictCache(
        "github", dictRemoteIdentity=dictRelease))
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo, dictGithub={})
    _fnBind(dictWorkflow, "someone", "something")
    assert levelGates.fdictDescribePublishedCopy(
        dictWorkflow, fixtureProjectRepo, "github")["sState"] == "unknown"
    dictWorkflow["dictRemotes"]["github"]["sBranch"] = "release"
    assert levelGates.fdictDescribePublishedCopy(
        dictWorkflow, fixtureProjectRepo, "github")["sState"] == "matched"


def test_the_verify_and_the_gate_share_one_default_branch():
    """The record a verify writes for an omitted branch is the gate's default."""
    dictStatus = {}
    scheduledReverify._fnAttachServiceIdentityFields(
        dictStatus, "github", {"sOwner": "someone", "sRepo": "something"})
    assert dictStatus["dictRemoteIdentity"]["sBranch"] == (
        levelGates._fsEffectiveGithubBranch({}, {}))


def test_the_binding_compares_owner_and_repository_without_case(
    fixtureProjectRepo,
):
    """GitHub names are case-insensitive, as the verify's own rule is."""
    _fnWriteCaches(fixtureProjectRepo)
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo, dictGithub={})
    _fnBind(dictWorkflow, "Someone", "SOMETHING", "main")
    assert levelGates.fbWorkflowFullySyncedWithGithub(
        dictWorkflow, fixtureProjectRepo) is True


def test_a_branch_the_binding_names_must_be_the_branch_checked(
    fixtureProjectRepo,
):
    _fnWriteCaches(fixtureProjectRepo)
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo, dictGithub={})
    _fnBind(dictWorkflow, "someone", "something", "release")
    assert levelGates.fbWorkflowFullySyncedWithGithub(
        dictWorkflow, fixtureProjectRepo) is False


def test_a_project_that_never_pushed_from_this_hub_is_unknown(
    fixtureProjectRepo,
):
    """A binding map with no entry for this repository binds nothing."""
    _fnWriteCaches(fixtureProjectRepo)
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo)
    dictWorkflow[S_BINDING_KEY] = {}
    dictAccess = levelGates.fdictDescribePublicAccess(
        dictWorkflow, fixtureProjectRepo, "github")
    assert dictAccess == {
        "sState": "unknown", "sReason": scheduledReverify.S_GITHUB_NOT_BOUND}


@pytest.mark.falsification
def test_a_declared_remote_that_disagrees_with_the_binding_is_unknown(
    fixtureProjectRepo,
):
    """Record and binding both say A; the declaration names B.

    The next verify would refuse this declaration, so the record cannot
    answer for the project as it now stands, and the reason given is the
    verify's own sentence.

    Kills: dropping the declared-versus-binding comparison from
    _fsDescribeUncoveredGithubRemote.
    """
    _fnWriteCaches(fixtureProjectRepo)
    dictWorkflow = _fdictWorkflow(
        fixtureProjectRepo,
        dictGithub={"sOwner": "someone", "sRepo": "elsewhere"})
    _fnBind(dictWorkflow, "someone", "something")
    dictAccess = levelGates.fdictDescribePublicAccess(
        dictWorkflow, fixtureProjectRepo, "github")
    assert dictAccess["sState"] == "unknown"
    assert dictAccess["sReason"] == scheduledReverify._fsDescribeBindingMismatch(
        dictWorkflow[S_BINDING_KEY][fixtureProjectRepo])
    assert levelGates.fbWorkflowFullySyncedWithGithub(
        dictWorkflow, fixtureProjectRepo) is False


@pytest.mark.falsification
def test_a_declared_branch_that_disagrees_with_the_binding_is_unknown(
    fixtureProjectRepo,
):
    """Same owner and repository, a different declared branch.

    Kills: _fbGithubRemotesAgree ignoring the branch.
    """
    _fnWriteCaches(fixtureProjectRepo)
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo, dictGithub={
        "sOwner": "someone", "sRepo": "something", "sBranch": "draft"})
    _fnBind(dictWorkflow, "someone", "something", "main")
    assert levelGates.fdictDescribePublicAccess(
        dictWorkflow, fixtureProjectRepo, "github")["sState"] == "unknown"
    assert levelGates.fbWorkflowFullySyncedWithGithub(
        dictWorkflow, fixtureProjectRepo) is False


def test_a_workflow_without_a_binding_is_compared_with_its_declaration(
    fixtureProjectRepo,
):
    """A library caller has no hub to ask, so the declaration is the remote."""
    _fnWriteCaches(fixtureProjectRepo)
    dictWorkflow = _fdictWorkflow(
        fixtureProjectRepo,
        dictGithub={"sOwner": "someone", "sRepo": "elsewhere"})
    assert S_BINDING_KEY not in dictWorkflow
    assert levelGates.fdictDescribePublishedCopy(
        dictWorkflow, fixtureProjectRepo, "github")["sState"] == "unknown"


@pytest.mark.falsification
def test_a_newly_declared_record_invalidates_the_evidence(fixtureProjectRepo):
    """{1} recorded, {1, 2} declared: record 2 was never asked about.

    Kills: dropping the record-set comparison from
    _fsDescribeUncoveredZenodoArchive.
    """
    _fnWriteCaches(fixtureProjectRepo, dictZenodo=_fdictCache(
        "zenodo", dictRemoteIdentity={
            "sEndpoint": "sandbox", "listRecordIds": ["1"]}))
    dictDeclared = {"sRecordId": "1", "sDoi": "10.5281/zenodo.1",
                    "sService": "sandbox"}
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo, dictZenodo=dictDeclared)
    assert levelGates.fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, fixtureProjectRepo) is True
    dictDeclared["listRecords"] = [{"sRecordId": "2"}]
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo, dictZenodo=dictDeclared)
    assert levelGates.fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, fixtureProjectRepo) is False
    dictAccess = levelGates.fdictDescribePublicAccess(
        dictWorkflow, fixtureProjectRepo, "zenodo")
    assert dictAccess["sState"] == "unknown"
    assert "1, 2 on sandbox" in dictAccess["sReason"]
    assert dictAccess["sReason"].startswith("Not compared: ")


def test_the_other_zenodo_instance_invalidates_the_evidence(fixtureProjectRepo):
    _fnWriteCaches(fixtureProjectRepo)
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo, dictZenodo={
        "sDoi": "10.5281/zenodo.1", "sService": "zenodo"})
    assert levelGates.fdictDescribePublicAccess(
        dictWorkflow, fixtureProjectRepo, "zenodo")["sState"] == "unknown"


def test_a_binding_change_busts_the_cached_blocker_list(fixtureProjectRepo):
    """The binding decides what a record covers, so it keys the cache."""
    _fnWriteCaches(fixtureProjectRepo)
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo, dictGithub={})
    _fnBind(dictWorkflow, "someone", "something")
    assert "github-verify-stale" not in _flistCriteria(
        dictWorkflow, fixtureProjectRepo)
    _fnBind(dictWorkflow, "someone", "elsewhere")
    assert "github-verify-stale" in _flistCriteria(
        dictWorkflow, fixtureProjectRepo)


# ── the blockers, the gaps and the header ────────────────────────────


def test_a_private_repository_names_its_remedy_in_the_blocker(
    fixtureProjectRepo,
):
    sReason = "a reason the verify recorded"
    dictCache = _fdictCache("github", sPublicAccess="not-public")
    dictCache["dictPublicAccess"]["sReason"] = sReason
    _fnWriteCaches(fixtureProjectRepo, dictGithub=dictCache)
    listBlockers = levelGates.flistLevel2Blockers(
        _fdictWorkflow(fixtureProjectRepo), fixtureProjectRepo)
    listNotPublic = [d for d in listBlockers
                     if d["sCriterion"] == "github-repository-not-public"]
    assert len(listNotPublic) == 1
    assert listNotPublic[0]["sScope"] == "workflow"
    assert listNotPublic[0]["sRemediationHint"] == sReason


def test_a_private_repository_with_a_diverged_file_is_charged_twice(
    fixtureProjectRepo,
):
    """Public and matching are separate requirements, as in the paper."""
    _fnWriteCaches(fixtureProjectRepo, dictGithub=_fdictCache(
        "github", sPublicAccess="not-public",
        listDivergedPaths=[S_STEP_OUTPUT]))
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo)
    listBlockers = levelGates.flistLevel2Blockers(dictWorkflow, fixtureProjectRepo)
    assert "github-repository-not-public" in [
        d["sCriterion"] for d in listBlockers]
    assert "not-in-github-mirror" in [d["sCriterion"] for d in listBlockers]
    assert levelGates.fdictDescribePublishedCopy(
        dictWorkflow, fixtureProjectRepo, "github")["sState"] == "differs"


def test_staleness_charges_a_service_once(fixtureProjectRepo):
    """A stale not-public record is unknown, never also not public."""
    _fnWriteCaches(fixtureProjectRepo, dictGithub=_fdictCache(
        "github", sPublicAccess="not-public",
        sLastVerified=fsRecentVerifyIso(fHoursAgo=48.0)))
    listCriteria = _flistCriteria(
        _fdictWorkflow(fixtureProjectRepo), fixtureProjectRepo)
    assert "github-verify-stale" in listCriteria
    assert "github-repository-not-public" not in listCriteria


def _fdictWithheldZenodoCache():
    """The record a verify writes for an archive a reader cannot open."""
    dictCache = fdictBuildCachedVerify(
        sService="zenodo", listComparedPaths=[],
        sLastVerified=fsRecentVerifyIso(), sZenodoDoi="10.5281/zenodo.1",
        sEndpointVerified="sandbox", sPublicAccess="not-public",
        bComparisonWithheld=True, dictArchivedAttestation=None,
    )
    dictCache["dictPublicAccess"]["sReason"] = (
        "Zenodo record 2 restricts its files.")
    return dictCache


def test_a_withheld_comparison_is_charged_once_and_reads_not_compared(
    fixtureProjectRepo,
):
    _fnWriteCaches(fixtureProjectRepo, dictZenodo=_fdictWithheldZenodoCache())
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo)
    listZenodo = [c for c in _flistCriteria(dictWorkflow, fixtureProjectRepo)
                  if "zenodo" in c]
    assert listZenodo == ["zenodo-archive-not-public"]
    dictCopy = levelGates.fdictDescribePublishedCopy(
        dictWorkflow, fixtureProjectRepo, "zenodo")
    assert dictCopy == {
        "sState": "unknown",
        "sReason": "Not compared: Zenodo record 2 restricts its files.",
    }


def test_a_step_s_zenodo_cell_is_unknown_while_the_comparison_is_withheld(
    fixtureProjectRepo,
):
    _fnWriteCaches(fixtureProjectRepo, dictZenodo=_fdictWithheldZenodoCache())
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo)
    dictContext = levelGates._fdictStepProjectionContext(
        dictWorkflow, [],
        levelGates.flistLevel2Blockers(dictWorkflow, fixtureProjectRepo), [],
    )
    listRequirements = levelGates._flistStepLevel2Requirements(
        dictWorkflow["listSteps"][0], set(), dictContext)
    assert ("zenodo-deposit", None) in listRequirements
    assert ("github-mirror", True) in listRequirements


def test_every_false_gap_has_a_state_and_a_reason_beside_it(fixtureProjectRepo):
    _fnWriteCaches(fixtureProjectRepo, dictGithub=_fdictCache(
        "github", sPublicAccess="not-public"),
        dictZenodo=_fdictWithheldZenodoCache())
    dictGaps = levelGates.fdictLevel2Gaps(
        _fdictWorkflow(fixtureProjectRepo), fixtureProjectRepo)
    assert dictGaps["bGithubRepositoryPublic"] is False
    assert dictGaps["bZenodoArchivePublic"] is False
    assert dictGaps["bZenodoFullySynced"] is False
    assert dictGaps["dictPublicAccessStates"]["github"]["sState"] == "not-public"
    assert dictGaps["dictPublicAccessStates"]["zenodo"]["sState"] == "not-public"
    assert dictGaps["dictPublishedCopyStates"]["github"]["sState"] == "matched"
    assert dictGaps["dictPublishedCopyStates"]["zenodo"]["sState"] == "unknown"
    for dictState in (list(dictGaps["dictPublicAccessStates"].values())
                      + list(dictGaps["dictPublishedCopyStates"].values())):
        assert dictState["sReason"]


@pytest.mark.parametrize("dictOverrides", [
    {}, {"listDivergedPaths": [S_STEP_OUTPUT]},
    {"sLastVerified": fsRecentVerifyIso(fHoursAgo=48.0)},
    {"bRemoteIdentity": False},
    {"dictRemoteIdentity": dict(
        DICT_DEFAULT_REMOTE_IDENTITY["github"], sRepo="elsewhere")},
    {"sCommittedShaVerified": "an-older-commit"},
])
def test_the_sync_predicate_is_true_exactly_when_the_copy_matched(
    fixtureProjectRepo, dictOverrides,
):
    _fnWriteCaches(fixtureProjectRepo, dictGithub=_fdictCache(
        "github", **dictOverrides))
    dictWorkflow = _fdictWorkflow(fixtureProjectRepo)
    assert levelGates.fbWorkflowFullySyncedWithGithub(
        dictWorkflow, fixtureProjectRepo,
    ) is (levelGates.fdictDescribePublishedCopy(
        dictWorkflow, fixtureProjectRepo, "github")["sState"] == "matched")
