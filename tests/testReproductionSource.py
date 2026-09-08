"""Staging a published project: the commit, the rules, the redaction.

Every test here drives the real ``git`` on PATH against real
repositories, and the remote tests clone over a real loopback HTTP
server, because the property under test is what the staged BYTES are
-- a stubbed clone would let every one of them pass against a command
that never ran. Each falsification test names the mutation it was
proven to fail against on a ``Kills:`` line.
"""

import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import time

import pytest

from tests.reproductionSourceFixtures import (
    LoopbackGitServer,
    S_FIXTURE_ARCHITECTURE,
    S_FIXTURE_IMAGE_DIGEST,
    S_FIXTURE_OUTPUT,
    S_FIXTURE_SCRIPT,
    S_FIXTURE_WORKFLOW_PATH,
    fdictBuildEnvelope,
    fdictBuildWorkflow,
    fnCommitEverything,
    fnPublishToBare,
    fnPushToBare,
    fnWriteJson,
    fnWriteManifest,
    fnWriteText,
    fsBuildPublishedProject,
    fsRunGit,
)
from vaibify.reproducibility import reproductionSource
from vaibify.reproducibility.gitHardening import (
    LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
    LIST_GIT_HARDENING_CONFIG,
)
from vaibify.reproducibility.reproductionSource import (
    ReproductionSourceRefusedError,
    fbaExportStagedSnapshot,
    fcontextHoldStagedSource,
    fdictClassifySource,
    fdictDescribeStagedSource,
    fdictStageSource,
    flistSweepAbandonedStaging,
    fsRequiredPlatformFromArchitecture,
)


@pytest.fixture
def sAdmittedRoot(tmp_path, monkeypatch):
    """Admit the test's temporary directory as the one local-clone root."""
    sRoot = os.path.realpath(str(tmp_path))
    monkeypatch.setattr(
        reproductionSource, "flistAdmittedLocalCloneRoots", lambda: [sRoot],
    )
    return sRoot


@pytest.fixture
def sPublishedRepo(sAdmittedRoot):
    """A committed, reproduction-ready project under the admitted root."""
    sRepoPath = os.path.join(sAdmittedRoot, "publishedProject")
    fsBuildPublishedProject(sRepoPath)
    return sRepoPath


def _flistStagingTokens():
    """Return the staging directories currently on disk."""
    sRoot = reproductionSource._fsStagingRoot()
    return sorted(os.listdir(sRoot)) if os.path.isdir(sRoot) else []


def _fdictTarMembers(baArchive):
    """Return ``{member name: bytes}`` for every regular file in a tar."""
    dictMembers = {}
    with tarfile.open(fileobj=io.BytesIO(baArchive)) as fileTar:
        for infoMember in fileTar:
            if infoMember.isreg():
                dictMembers[infoMember.name] = fileTar.extractfile(
                    infoMember,
                ).read()
    return dictMembers


# ---------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------


@pytest.mark.parametrize("sInput", [
    "ext::sh -c id",
    "file:///anywhere/at/all",
    "git://host.example/project.git",
    "http://host.example/project.git",
    "https://user:token@host.example/project.git",
    "https://token@host.example/project.git",
    "ssh://user:password@host.example/project.git",
    "",
    "not a source",
])
def test_every_refused_shape_names_the_accepted_ones(sInput, sAdmittedRoot):
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictClassifySource(sInput)
    assert "Accepted:" in str(excinfo.value)


def test_accepted_url_shapes_classify_as_git_urls(sAdmittedRoot):
    for sInput in (
        "https://host.example/group/project.git",
        "ssh://git@host.example:2222/group/project.git",
        "git@host.example:group/project.git",
    ):
        assert fdictClassifySource(sInput) == {
            "sKind": "git-url", "sSource": sInput,
        }


def test_a_local_path_outside_the_admitted_roots_is_refused(
    sAdmittedRoot, tmp_path_factory,
):
    sElsewhere = str(tmp_path_factory.mktemp("elsewhere"))
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictClassifySource(sElsewhere)
    assert "outside the directories" in str(excinfo.value)


def test_the_platform_is_derived_from_the_recorded_architecture():
    assert fsRequiredPlatformFromArchitecture("amd64") == "linux/amd64"
    assert fsRequiredPlatformFromArchitecture("linux/arm64") == "linux/arm64"
    assert fsRequiredPlatformFromArchitecture("") == ""


# ---------------------------------------------------------------------
# 1b. A reproduction is of a commit
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_an_untracked_file_refuses_the_clone_naming_the_path(sPublishedRepo):
    """Kills: ``--untracked-files=all`` -> ``--untracked-files=no``."""
    fnWriteText(sPublishedRepo, "scratchNotes.txt", "not committed\n")
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    assert "scratchNotes.txt" in str(excinfo.value)
    assert "not a clean clone" in str(excinfo.value)
    assert _flistStagingTokens() == []


@pytest.mark.falsification
def test_an_ignored_file_refuses_the_clone_too(sPublishedRepo):
    """Kills: dropping ``--ignored`` from the status query."""
    fnWriteText(sPublishedRepo, ".gitignore", "*.cache\n")
    fnCommitEverything(sPublishedRepo, "ignore caches")
    fnWriteText(sPublishedRepo, "results.cache", "stale\n")
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    assert "results.cache" in str(excinfo.value)


@pytest.mark.falsification
def test_a_clean_clone_stages_the_checked_out_commit(sPublishedRepo):
    """The staged commit is the source's HEAD, not its default branch.

    Kills: resolving ``main`` instead of ``HEAD`` in the local
    materializer.
    """
    sFirstCommit = fsRunGit(["rev-parse", "HEAD"], sPublishedRepo)
    fnWriteText(sPublishedRepo, S_FIXTURE_OUTPUT, "2\n")
    fnWriteManifest(sPublishedRepo, [S_FIXTURE_SCRIPT, S_FIXTURE_OUTPUT])
    fnCommitEverything(sPublishedRepo, "second result")
    fsRunGit(["checkout", "--quiet", "--detach", sFirstCommit], sPublishedRepo)
    dictStaged = fdictStageSource(sPublishedRepo)
    assert dictStaged["sResolvedCommit"] == sFirstCommit
    dictMembers = _fdictTarMembers(fbaExportStagedSnapshot(dictStaged["sToken"]))
    assert dictMembers[f"publishedProject/{S_FIXTURE_OUTPUT}"] == b"1\n"


def test_a_subdirectory_of_a_clone_is_refused_naming_the_root(sPublishedRepo):
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(os.path.join(sPublishedRepo, "MakeNumbers"))
    assert "point at the root" in str(excinfo.value)


@pytest.mark.falsification
def test_the_export_carries_the_staged_commit_after_the_remote_moves(
    sPublishedRepo, tmp_path, monkeypatch,
):
    """A branch that advances after staging changes nothing that runs.

    Kills: re-cloning the remote inside ``fbaExportStagedSnapshot``.
    """
    monkeypatch.setattr(
        reproductionSource, "T_ACCEPTED_URL_SCHEMES", ("https", "ssh", "http"),
    )
    sBarePath = os.path.join(str(tmp_path), "served.git")
    fnPublishToBare(sPublishedRepo, sBarePath)
    with LoopbackGitServer(sBarePath) as server:
        dictStaged = fdictStageSource(server.sUrl)
        assert dictStaged["sKind"] == "git-url"
        assert dictStaged["sRemoteUrl"] == server.sUrl
        fnWriteText(sPublishedRepo, S_FIXTURE_OUTPUT, "moved\n")
        fnWriteManifest(sPublishedRepo, [S_FIXTURE_SCRIPT, S_FIXTURE_OUTPUT])
        fnCommitEverything(sPublishedRepo, "the branch moved")
        fnPushToBare(sPublishedRepo, sBarePath)
        dictMembers = _fdictTarMembers(
            fbaExportStagedSnapshot(dictStaged["sToken"]),
        )
    sRepositoryName = dictStaged["sRepositoryName"]
    assert dictMembers[f"{sRepositoryName}/{S_FIXTURE_OUTPUT}"] == b"1\n"
    assert any(
        sName.startswith(f"{sRepositoryName}/.git/") for sName in dictMembers
    ), "the export must carry the repository's history"


# ---------------------------------------------------------------------
# 1c. Workflow selection
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_two_workflows_are_refused_until_one_is_named(sPublishedRepo):
    """Kills: ``if len(listWorkflows) > 1`` -> ``if False``."""
    fnWriteJson(
        sPublishedRepo, ".vaibify/projects/second.json",
        fdictBuildWorkflow("Second"),
    )
    fnCommitEverything(sPublishedRepo, "a second workflow")
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    assert "Demo" in str(excinfo.value) and "Second" in str(excinfo.value)
    dictStaged = fdictStageSource(sPublishedRepo, sWorkflowName="Second")
    assert dictStaged["sWorkflowPath"] == ".vaibify/projects/second.json"


# ---------------------------------------------------------------------
# 1d. Six rules, six fixtures, each violating exactly one
# ---------------------------------------------------------------------


def _fnAssertOnlyRuleFired(excinfo, iRule):
    """The refusal names rule ``iRule`` and no other, and left nothing."""
    sMessage = str(excinfo.value)
    assert f"rule {iRule} " in sMessage, sMessage
    for iOther in range(1, 7):
        if iOther != iRule:
            assert f"rule {iOther} " not in sMessage, sMessage
    assert _flistStagingTokens() == []


@pytest.mark.falsification
def test_rule_1_an_invalid_project_file_refuses(sPublishedRepo):
    """Kills: dropping the validation-failure raise in the strict loader."""
    dictWorkflow = fdictBuildWorkflow()
    del dictWorkflow["sPlotDirectory"]
    fnWriteJson(sPublishedRepo, S_FIXTURE_WORKFLOW_PATH, dictWorkflow)
    fnCommitEverything(sPublishedRepo, "invalid workflow")
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    _fnAssertOnlyRuleFired(excinfo, 1)
    assert "sPlotDirectory" in str(excinfo.value)


@pytest.mark.falsification
def test_rule_2_a_tag_instead_of_a_digest_refuses(sPublishedRepo):
    """Kills: swallowing the pinned-reference refusal."""
    dictEnvelope = fdictBuildEnvelope()
    dictEnvelope["dictContainer"]["sImageDigest"] = "registry.example/demo:latest"
    fnWriteJson(sPublishedRepo, ".vaibify/environment.json", dictEnvelope)
    fnCommitEverything(sPublishedRepo, "a tag, not a digest")
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    _fnAssertOnlyRuleFired(excinfo, 2)
    assert "tag" in str(excinfo.value)


@pytest.mark.falsification
def test_rule_2_a_missing_architecture_refuses(sPublishedRepo):
    """Kills: dropping the architecture refusal from the envelope reader."""
    dictEnvelope = fdictBuildEnvelope()
    del dictEnvelope["dictContainer"]["sArchitecture"]
    fnWriteJson(sPublishedRepo, ".vaibify/environment.json", dictEnvelope)
    fnCommitEverything(sPublishedRepo, "no architecture recorded")
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    _fnAssertOnlyRuleFired(excinfo, 2)
    assert "no image architecture" in str(excinfo.value)


@pytest.mark.falsification
def test_rule_1_a_project_from_a_newer_vaibify_refuses_by_name(
    sPublishedRepo,
):
    """Kills: letting the migration's ValueError escape untranslated."""
    dictWorkflow = fdictBuildWorkflow()
    dictWorkflow["iWorkflowSchemaVersion"] = 10 ** 6
    fnWriteJson(sPublishedRepo, S_FIXTURE_WORKFLOW_PATH, dictWorkflow)
    fnCommitEverything(sPublishedRepo, "from the future")
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    _fnAssertOnlyRuleFired(excinfo, 1)
    assert "newer" in str(excinfo.value)


@pytest.mark.falsification
def test_rule_3_a_malformed_manifest_refuses(sPublishedRepo):
    """Kills: answering an empty entry list for a manifest that fails to parse."""
    fnWriteText(sPublishedRepo, "MANIFEST.sha256", "this is not a manifest line\n")
    fnCommitEverything(sPublishedRepo, "broken manifest")
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    _fnAssertOnlyRuleFired(excinfo, 3)


@pytest.mark.falsification
def test_rule_4_a_manifest_entry_that_differs_refuses(sPublishedRepo):
    """Kills: ``listMismatches = flistVerifyManifestEntries(...)`` -> ``[]``."""
    fnWriteText(sPublishedRepo, S_FIXTURE_OUTPUT, "tampered\n")
    fnCommitEverything(sPublishedRepo, "output changed, manifest not")
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    _fnAssertOnlyRuleFired(excinfo, 4)
    assert S_FIXTURE_OUTPUT in str(excinfo.value)


@pytest.mark.falsification
def test_rule_5_a_declared_file_the_manifest_omits_refuses(sPublishedRepo):
    """Kills: ``listMissing = flistDeclaredButMissingFromManifest(...)`` -> ``[]``."""
    fnWriteManifest(sPublishedRepo, [S_FIXTURE_OUTPUT])
    fnCommitEverything(sPublishedRepo, "manifest omits the script")
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    _fnAssertOnlyRuleFired(excinfo, 5)
    assert S_FIXTURE_SCRIPT in str(excinfo.value)


@pytest.mark.falsification
def test_rule_6_a_deposit_for_another_build_refuses(sPublishedRepo):
    """Kills: ``listReasons = imageArchive.flistDescribeArchiveMismatch(...)`` -> ``[]``."""
    dictRecord = {
        "sVersionDoi": "10.5281/zenodo.1234567",
        "sConceptDoi": "10.5281/zenodo.1234566",
        "sTarballSha256": "sha256:" + "b" * 64,
        "iTarballBytes": 12345,
        "sProvenance": "original",
        "sImageDigest": S_FIXTURE_IMAGE_DIGEST,
        "sArchitecture": "arm64",
    }
    fnWriteJson(
        sPublishedRepo, ".vaibify/environment.json",
        fdictBuildEnvelope(dictArchiveRecord=dictRecord),
    )
    fnCommitEverything(sPublishedRepo, "deposit of the other build")
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    _fnAssertOnlyRuleFired(excinfo, 6)
    assert "arm64" in str(excinfo.value)


def test_a_matching_deposit_is_recorded_with_its_version_doi(sPublishedRepo):
    dictRecord = {
        "sVersionDoi": "10.5281/zenodo.1234567",
        "sConceptDoi": "10.5281/zenodo.1234566",
        "sTarballSha256": "sha256:" + "b" * 64,
        "iTarballBytes": 12345,
        "sProvenance": "original",
        "sImageDigest": S_FIXTURE_IMAGE_DIGEST,
        "sArchitecture": S_FIXTURE_ARCHITECTURE,
    }
    fnWriteJson(
        sPublishedRepo, ".vaibify/environment.json",
        fdictBuildEnvelope(dictArchiveRecord=dictRecord),
    )
    fnCommitEverything(sPublishedRepo, "deposit on record")
    dictStaged = fdictStageSource(sPublishedRepo)
    assert dictStaged["bDepositOnRecord"] is True
    assert dictStaged["sDepositVersionDoi"] == "10.5281/zenodo.1234567"
    assert dictStaged["sRequiredPlatform"] == "linux/amd64"


def test_a_valid_project_stages_with_no_deposit_on_record(sPublishedRepo):
    dictStaged = fdictStageSource(sPublishedRepo)
    assert dictStaged["bDepositOnRecord"] is False
    assert dictStaged["sPinnedImageReference"] == S_FIXTURE_IMAGE_DIGEST
    assert dictStaged["iManifestEntries"] == 2
    assert dictStaged["sWorkflowName"] == "Demo"
    assert dictStaged["sManifestDigest"].startswith("sha256:")


# ---------------------------------------------------------------------
# Hardening: every git call, both lists, no prompt
# ---------------------------------------------------------------------


def _flistRecordGitLaunches(monkeypatch):
    """Record every subprocess launch's argv and env, then call through."""
    listLaunches = []
    fRealRun = subprocess.run
    classRealPopen = subprocess.Popen

    def fnRecord(listArguments, kwargs):
        # The fixtures run git too; only the product's launches count.
        if "user.name=fixture" not in listArguments:
            listLaunches.append((list(listArguments), kwargs.get("env")))

    def fprocessRecordingRun(listArguments, *args, **kwargs):
        fnRecord(list(listArguments), kwargs)
        return fRealRun(listArguments, *args, **kwargs)

    class _RecordingPopen(classRealPopen):
        def __init__(self, listArguments, *args, **kwargs):
            fnRecord(list(listArguments), kwargs)
            super().__init__(listArguments, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fprocessRecordingRun)
    monkeypatch.setattr(subprocess, "Popen", _RecordingPopen)
    return listLaunches


def _fbCarriesEveryPair(listArguments, listConfig):
    """True when every ``-c key=value`` pair of a list appears in argv."""
    listPairs = list(zip(listConfig[::2], listConfig[1::2]))
    listSeen = list(zip(listArguments, listArguments[1:]))
    return all(tPair in listSeen for tPair in listPairs)


@pytest.mark.falsification
def test_every_git_query_carries_both_hardening_lists(
    sPublishedRepo, monkeypatch,
):
    """Kills: dropping ``*LIST_GIT_HARDENING_CONFIG`` from the query runner."""
    listLaunches = _flistRecordGitLaunches(monkeypatch)
    fdictStageSource(sPublishedRepo)
    listQueries = [
        tLaunch for tLaunch in listLaunches if "clone" not in tLaunch[0]
    ]
    assert listQueries, "the stage must query git"
    for listArguments, _dictEnvironment in listQueries:
        assert listArguments[0] == "git"
        assert _fbCarriesEveryPair(listArguments, LIST_GIT_HARDENING_CONFIG)
        assert _fbCarriesEveryPair(
            listArguments, LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
        )


@pytest.mark.falsification
def test_the_clone_carries_both_hardening_lists(sPublishedRepo, monkeypatch):
    """Kills: dropping ``*LIST_GIT_CREDENTIAL_ISOLATION_CONFIG`` from the clone."""
    listLaunches = _flistRecordGitLaunches(monkeypatch)
    fdictStageSource(sPublishedRepo)
    listClones = [tLaunch for tLaunch in listLaunches if "clone" in tLaunch[0]]
    assert len(listClones) == 1
    listArguments, _dictEnvironment = listClones[0]
    assert _fbCarriesEveryPair(listArguments, LIST_GIT_HARDENING_CONFIG)
    assert _fbCarriesEveryPair(
        listArguments, LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
    )


def test_only_the_local_clone_reopens_the_file_transport_after_the_list(
    sPublishedRepo, monkeypatch, tmp_path,
):
    """The override follows the hardening list, and a URL clone has none."""
    listLaunches = _flistRecordGitLaunches(monkeypatch)
    fdictStageSource(sPublishedRepo)
    listArguments = next(
        tLaunch[0] for tLaunch in listLaunches if "clone" in tLaunch[0]
    )
    iNever = listArguments.index("protocol.file.allow=never")
    iAlways = listArguments.index("protocol.file.allow=always")
    assert iNever < iAlways
    listLaunches.clear()
    monkeypatch.setattr(
        reproductionSource, "T_ACCEPTED_URL_SCHEMES", ("https", "ssh", "http"),
    )
    sBarePath = os.path.join(str(tmp_path), "served.git")
    fnPublishToBare(sPublishedRepo, sBarePath)
    with LoopbackGitServer(sBarePath) as server:
        fdictStageSource(server.sUrl)
    listUrlClone = next(
        tLaunch[0] for tLaunch in listLaunches if "clone" in tLaunch[0]
    )
    assert "protocol.file.allow=always" not in listUrlClone
    assert "protocol.file.allow=never" in listUrlClone


@pytest.mark.falsification
def test_no_git_call_can_prompt_for_a_credential(sPublishedRepo, monkeypatch):
    """Kills: ``GIT_TERMINAL_PROMPT`` set to ``"1"``."""
    listLaunches = _flistRecordGitLaunches(monkeypatch)
    fdictStageSource(sPublishedRepo)
    assert listLaunches
    for _listArguments, dictEnvironment in listLaunches:
        assert dictEnvironment is not None
        assert dictEnvironment.get("GIT_TERMINAL_PROMPT") == "0"


@pytest.mark.falsification
def test_ssh_cannot_prompt_either(sPublishedRepo, monkeypatch):
    """Batch mode wins even over an inherited ``BatchMode=no``.

    OpenSSH keeps the FIRST value of an option, so the enforced one
    must precede the researcher's. The effective setting is read back
    through ``ssh -G`` when ssh is on PATH; the ordering is asserted
    structurally either way, so no host makes this test vacuous.

    Kills: appending ``BatchMode=yes`` after the inherited options.
    """
    monkeypatch.setenv(
        "GIT_SSH_COMMAND", "ssh -o BatchMode=no -i /somewhere/key",
    )
    listLaunches = _flistRecordGitLaunches(monkeypatch)
    fdictStageSource(sPublishedRepo)
    assert listLaunches
    # A snapshot: the ``ssh -G`` probe below is itself a recorded launch.
    for _listArguments, dictEnvironment in list(listLaunches):
        listWords = shlex.split(dictEnvironment["GIT_SSH_COMMAND"])
        assert listWords[0] == "ssh"
        assert "-i" in listWords and "/somewhere/key" in listWords
        assert listWords.index("BatchMode=yes") < listWords.index(
            "BatchMode=no",
        )
        if shutil.which("ssh"):
            processSsh = subprocess.run(
                listWords + ["-G", "host.invalid"],
                capture_output=True, text=True,
            )
            assert "batchmode yes" in processSsh.stdout.lower(), (
                processSsh.stdout + processSsh.stderr
            )


# ---------------------------------------------------------------------
# 1f. Redaction
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_description_strips_userinfo_from_the_origin(sPublishedRepo):
    """Kills: recording the origin without ``_fsStripUserinfo``."""
    fsRunGit(
        ["remote", "add", "origin",
         "https://someone:hunter2token@host.example/group/project.git"],
        sPublishedRepo,
    )
    dictStaged = fdictStageSource(sPublishedRepo)
    sDescription = json.dumps(fdictDescribeStagedSource(dictStaged["sToken"]))
    assert "hunter2token" not in sDescription
    assert "someone" not in sDescription
    assert "https://host.example/group/project.git" in sDescription


@pytest.mark.falsification
def test_the_description_never_carries_a_host_path(sPublishedRepo):
    """Kills: adding the staging directory to the source record."""
    dictStaged = fdictStageSource(sPublishedRepo)
    sDescription = json.dumps(fdictDescribeStagedSource(dictStaged["sToken"]))
    assert sPublishedRepo not in sDescription
    assert reproductionSource._fsStagingRoot() not in sDescription
    assert os.path.expanduser("~") not in sDescription
    for sValue in fdictDescribeStagedSource(dictStaged["sToken"]).values():
        if isinstance(sValue, str):
            assert not sValue.startswith("/"), sValue


def test_a_local_origin_outside_the_roots_is_refused(
    sPublishedRepo, tmp_path_factory,
):
    sElsewhere = str(tmp_path_factory.mktemp("elsewhereOrigin"))
    fsRunGit(["remote", "add", "origin", sElsewhere], sPublishedRepo)
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    assert "origin" in str(excinfo.value)


# ---------------------------------------------------------------------
# Size ceiling and the TTL sweep
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_clone_over_the_ceiling_is_refused_and_removed(
    sPublishedRepo, monkeypatch,
):
    """Kills: ``if iBytes <= I_STAGING_SIZE_CEILING_BYTES`` -> ``if True``."""
    fnWriteText(sPublishedRepo, "MakeNumbers/large.bin", "x" * 200_000)
    fnCommitEverything(sPublishedRepo, "a large file")
    monkeypatch.setattr(reproductionSource, "I_STAGING_SIZE_CEILING_BYTES", 4096)
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictStageSource(sPublishedRepo)
    assert "exceeded" in str(excinfo.value)
    assert _flistStagingTokens() == []


@pytest.mark.falsification
def test_the_sweep_skips_a_held_snapshot_whatever_its_age(sPublishedRepo):
    """Kills: sweeping a directory whose lock could not be taken."""
    dictHeld = fdictStageSource(sPublishedRepo)
    dictAbandoned = fdictStageSource(sPublishedRepo)
    fOld = time.time() - 10 * reproductionSource.F_STAGING_TTL_SECONDS
    for sToken in (dictHeld["sToken"], dictAbandoned["sToken"]):
        os.utime(reproductionSource._fsStagingDirectory(sToken), (fOld, fOld))
    with fcontextHoldStagedSource(dictHeld["sToken"]):
        listSwept = flistSweepAbandonedStaging()
    assert listSwept == [dictAbandoned["sToken"]]
    assert _flistStagingTokens() == [dictHeld["sToken"]]


def test_the_sweep_leaves_a_young_snapshot_alone(sPublishedRepo):
    dictStaged = fdictStageSource(sPublishedRepo)
    assert flistSweepAbandonedStaging() == []
    assert _flistStagingTokens() == [dictStaged["sToken"]]


# ---------------------------------------------------------------------
# The redaction boundary: a credential and a host path (review, 2026-09-07)
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_credential_query_parameter_is_refused(sAdmittedRoot):
    """A token rides in the query as readily as in the userinfo.

    Kills: dropping the query-parameter refusal from the URL admission.
    """
    for sName in ("access_token", "token"):
        sUrl = f"https://host.example/group/project.git?{sName}=TOPSECRET"
        with pytest.raises(ReproductionSourceRefusedError) as excinfo:
            fdictClassifySource(sUrl)
        assert sName in str(excinfo.value)
        assert "TOPSECRET" not in str(excinfo.value)
    assert fdictClassifySource(
        "https://host.example/group/project.git?ref=main",
    )["sKind"] == "git-url"


@pytest.mark.falsification
def test_the_recorded_remote_scrubs_a_query_credential(sAdmittedRoot):
    """The second line, for a URL that reached the recorder another way.

    Kills: returning the composed URL without the redactor's scrub.
    """
    assert "TOPSECRET" not in reproductionSource._fsStripUserinfo(
        "https://host.example/p.git?access_token=TOPSECRET",
    )


@pytest.mark.falsification
def test_the_staged_clone_carries_no_host_path_in_its_git_config(
    sPublishedRepo,
):
    """``git clone`` records the source it was given; the export carries it.

    Kills: not rewriting the staged clone's origin before the record.
    """
    dictStaged = fdictStageSource(sPublishedRepo)
    sConfigPath = os.path.join(
        reproductionSource.fsStagedClonePath(dictStaged["sToken"]),
        ".git", "config",
    )
    with open(sConfigPath, "r", encoding="utf-8") as fileConfig:
        sConfig = fileConfig.read()
    assert sPublishedRepo not in sConfig, sConfig
    dictMembers = _fdictTarMembers(
        fbaExportStagedSnapshot(dictStaged["sToken"]),
    )
    for sName, baContent in dictMembers.items():
        assert sPublishedRepo.encode("utf-8") not in baContent, sName


@pytest.mark.falsification
def test_a_staged_url_clone_keeps_only_the_redacted_remote(
    sPublishedRepo, tmp_path, monkeypatch,
):
    """The staged config names the redacted remote, never the typed URL.

    The URL carries a USERNAME, which is what makes the assertion
    discriminating: without it the recorded remote and the cloned URL
    are the same string, and a test that compares them passes against
    a clone whose origin was never rewritten (this test was written
    that way first, and the mutation survived it).

    Kills: reading the origin instead of rewriting it.
    """
    monkeypatch.setattr(
        reproductionSource, "T_ACCEPTED_URL_SCHEMES", ("https", "ssh", "http"),
    )
    sBarePath = os.path.join(str(tmp_path), "served.git")
    fnPublishToBare(sPublishedRepo, sBarePath)
    with LoopbackGitServer(sBarePath) as server:
        sUrlWithUser = server.sUrl.replace("http://", "http://someone@", 1)
        dictStaged = fdictStageSource(sUrlWithUser)
    sConfigPath = os.path.join(
        reproductionSource.fsStagedClonePath(dictStaged["sToken"]),
        ".git", "config",
    )
    with open(sConfigPath, "r", encoding="utf-8") as fileConfig:
        sConfig = fileConfig.read()
    assert dictStaged["sRemoteUrl"] in sConfig
    assert "someone@" not in sConfig, sConfig
    assert "someone@" not in dictStaged["sRemoteUrl"]
    assert sBarePath not in sConfig


@pytest.mark.falsification
def test_an_export_over_the_archive_bound_is_refused(sPublishedRepo):
    """The staging ceiling bounds the DISK; this bounds the hub's memory.

    Kills: ignoring ``iMaxBytes`` in the export.
    """
    dictStaged = fdictStageSource(sPublishedRepo)
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fbaExportStagedSnapshot(dictStaged["sToken"], 512)
    assert "512" in str(excinfo.value)
    assert fbaExportStagedSnapshot(dictStaged["sToken"], 50_000_000)


def test_the_export_spool_is_removed_on_every_path(sPublishedRepo):
    dictStaged = fdictStageSource(sPublishedRepo)
    sSpool = os.path.join(
        reproductionSource._fsStagingDirectory(dictStaged["sToken"]),
        reproductionSource._S_EXPORT_SPOOL_NAME,
    )
    fbaExportStagedSnapshot(dictStaged["sToken"])
    assert not os.path.exists(sSpool)
    with pytest.raises(ReproductionSourceRefusedError):
        fbaExportStagedSnapshot(dictStaged["sToken"], 512)
    assert not os.path.exists(sSpool)


# ---------------------------------------------------------------------
# The redaction boundary, second pass (review, 2026-09-07)
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_percent_encoded_credential_parameter_is_refused(sAdmittedRoot):
    """A server decodes the name, so the comparison must too.

    Kills: comparing the raw parameter name instead of the decoded one.
    """
    with pytest.raises(ReproductionSourceRefusedError) as excinfo:
        fdictClassifySource(
            "https://host.example/p.git?access%5Ftoken=TOPSECRET",
        )
    assert "access_token" in str(excinfo.value)
    assert "TOPSECRET" not in str(excinfo.value)
    assert "TOPSECRET" not in reproductionSource._fsStripUserinfo(
        "https://host.example/p.git?access%5Ftoken=TOPSECRET",
    )


@pytest.mark.falsification
def test_no_refusal_ever_echoes_the_credential_it_refuses(sAdmittedRoot):
    """The message names the secret it is about, and messages travel.

    Kills: echoing the raw URL in the userinfo refusal.
    """
    for sUrl in (
        "https://someone:TOPSECRET@host.example/p.git",
        "ssh://someone:TOPSECRET@host.example/p.git",
        "https://host.example/p.git?token=TOPSECRET",
        "https://host.example/p.git?ACCESS_TOKEN=TOPSECRET",
    ):
        with pytest.raises(ReproductionSourceRefusedError) as excinfo:
            fdictClassifySource(sUrl)
        assert "TOPSECRET" not in str(excinfo.value), sUrl
