"""Paths that may hold a secret never reach a council participant.

The council snapshot is read by AI agents running third-party models, so
the reviewed exclusion policy must drop every conventional credential
store. These tests drive the policy through its consumers: the
predicate itself, the whole-directory capture, the git-tracked capture
(real git, the real typed-read programs), and the size probe that runs
inside the container and must weigh exactly what the capture keeps.

Each reviewed entry has its OWN falsification test, because the
registry records one source mutation per test.
"""

import shutil

import pytest

from vaibify.docker import dockerConnection
from vaibify.gui import agentCouncilContext
from tests.testAgentCouncilContext import (
    S_ROOT_COMPONENT,
    _FakeCouncilConnection,
    _fbaBuildArchive,
    _fdictBuildObservationAnswer,
    _fdictCapture,
)
from tests.testCouncilSnapshotScope import (
    LocalRepoConnection,
    S_REPO_ROOT as S_TRACKED_REPO_ROOT,
    _fdictCaptureTracked,
    _fdictObserve,
    _fnGit,
    _fnWrite,
)

S_SECRET_TEXT = "SECRET_VALUE_THAT_MUST_NOT_SHIP\n"
S_NESTING = "deep/sub/"

LIST_SECRET_BEARING_PATHS = [
    ".env.local", ".env.production", ".env.example", ".aws/credentials",
    ".npmrc", ".pypirc", ".docker/config.json", ".config/gh/hosts.yml",
    ".mcp.json", "CLAUDE.local.md", "AGENTS.override.md",
]

DICT_OMISSION_PATH_FOR_SECRET_PATH = {
    ".config/gh/hosts.yml": ".config/gh",
    ".aws/credentials": ".aws",
    ".docker/config.json": ".docker",
}

LIST_LOOKALIKE_PATHS = [
    ".environment", ".envrc", ".envs/settings", ".env_backup",
    ".config/other/hosts.yml", ".config", "config/gh/hosts.yml",
    ".configs/gh/hosts.yml", ".config/ghost/hosts.yml", ".awsome",
    ".npmrc.md", ".docker-compose.yml", "mcp.json", "CLAUDE.local.txt",
    "src/AGENTS.overrides.md",
]


def _fsOmissionPathFor(sPath):
    sNesting = S_NESTING if sPath.startswith(S_NESTING) else ""
    sSuffix = sPath[len(sNesting):]
    return sNesting + DICT_OMISSION_PATH_FOR_SECRET_PATH.get(
        sSuffix, sSuffix)


def _fnAssertExcludedAtEveryDepth(sPath):
    for sNesting in ("", S_NESTING):
        tExclusion = agentCouncilContext.ftFindExcludedComponent(
            sNesting + sPath)
        assert tExclusion is not None, (
            f"{sNesting + sPath} would be shown to a council participant")
        assert tExclusion[0] == _fsOmissionPathFor(sNesting + sPath)
        assert tExclusion[1]


@pytest.mark.falsification
def testDotenvVariantsAreExcluded():
    """Kills: removing the dotenv-variant prefix from the policy."""
    for sPath in (".env.local", ".env.production", ".env.example"):
        _fnAssertExcludedAtEveryDepth(sPath)


@pytest.mark.falsification
def testTheAwsCredentialDirectoryIsExcluded():
    """Kills: removing the cloud credential directory from the policy."""
    _fnAssertExcludedAtEveryDepth(".aws/credentials")


@pytest.mark.falsification
def testTheNpmConfigurationIsExcluded():
    """Kills: removing the package registry config from the policy."""
    _fnAssertExcludedAtEveryDepth(".npmrc")


@pytest.mark.falsification
def testThePythonIndexConfigurationIsExcluded():
    """Kills: removing the package index config from the policy."""
    _fnAssertExcludedAtEveryDepth(".pypirc")


@pytest.mark.falsification
def testTheDockerConfigurationDirectoryIsExcluded():
    """Kills: removing the container registry login from the policy."""
    _fnAssertExcludedAtEveryDepth(".docker/config.json")


@pytest.mark.falsification
def testTheGitHubCliLoginIsExcludedAsAComponentSequence():
    """Kills: removing the .config/gh sequence from the policy."""
    _fnAssertExcludedAtEveryDepth(".config/gh/hosts.yml")


@pytest.mark.falsification
def testTheModelContextProtocolConfigurationIsExcluded():
    """Kills: removing the tool configuration file from the policy."""
    _fnAssertExcludedAtEveryDepth(".mcp.json")


@pytest.mark.falsification
def testThePersonalClaudeInstructionsAreExcluded():
    """Kills: removing the personal instruction file from the policy."""
    _fnAssertExcludedAtEveryDepth("CLAUDE.local.md")


@pytest.mark.falsification
def testThePersonalAgentsOverrideIsExcluded():
    """Kills: removing the personal override file from the policy."""
    _fnAssertExcludedAtEveryDepth("AGENTS.override.md")


@pytest.mark.falsification
def testALookalikeNameIsNotExcluded():
    """Kills: widening the dotenv prefix or the .config/gh sequence.

    A prefix without its trailing dot would drop `.environment`, and a
    sequence matched on a partial component would drop `.config/ghost`.
    """
    for sPath in LIST_LOOKALIKE_PATHS:
        assert agentCouncilContext.ftFindExcludedComponent(sPath) is None, (
            f"{sPath} is wrongly excluded")


def testTheTemplateExceptionIsSaidInTheReason():
    sReason = agentCouncilContext.ftFindExcludedComponent(".env.example")[1]
    assert "template" in sReason


def _fbaBuildSecretArchive():
    listSpecs = [{"sName": f"{S_ROOT_COMPONENT}/source.py",
                  "baContent": b"tracked\n"}]
    listSpecs += [
        {"sName": f"{S_ROOT_COMPONENT}/{sPath}",
         "baContent": S_SECRET_TEXT.encode()}
        for sPath in LIST_SECRET_BEARING_PATHS]
    return _fbaBuildArchive(listSpecs)


@pytest.mark.falsification
def testTheWholeDirectoryCaptureOmitsAndRecordsEverySecretPath(tmp_path):
    """Kills: dropping an entry for the whole-directory capture.

    Every secret path is also reported ignored, the state in which a
    project's own secrets usually sit, so only the policy keeps them out.
    """
    connection = _FakeCouncilConnection(
        _fbaBuildSecretArchive(), listObservationAnswers=[
            _fdictBuildObservationAnswer(listRecords=[(
                "file", agentCouncilContext._fsComputeGitBlobIdentity(
                    b"tracked\n"), "source.py")],
                listIgnoredPaths=LIST_SECRET_BEARING_PATHS)])
    dictManifest = _fdictCapture(connection, tmp_path)
    assert [dictEntry["sPath"] for dictEntry
            in dictManifest["listIncludedEntries"]] == ["source.py"]
    setOmitted = {dictRow["sPath"]
                  for dictRow in dictManifest["listOmissions"]}
    assert setOmitted == {
        _fsOmissionPathFor(sPath) for sPath in LIST_SECRET_BEARING_PATHS}
    baSealed = (tmp_path / "campaign-one" / "snapshot"
                / "snapshot.tar").read_bytes()
    assert S_SECRET_TEXT.encode() not in baSealed


@pytest.fixture
def pathSecretRepo(tmp_path):
    pathRepo = tmp_path / "scopeRepo"
    pathRepo.mkdir()
    _fnGit(pathRepo, "init", "-q")
    _fnGit(pathRepo, "config", "user.email", "t@example.org")
    _fnGit(pathRepo, "config", "user.name", "tester")
    _fnWrite(pathRepo, "source.py", "print(1)\n")
    for sPath in LIST_SECRET_BEARING_PATHS:
        _fnWrite(pathRepo, sPath, S_SECRET_TEXT)
    _fnGit(pathRepo, "add", "-A")
    _fnGit(pathRepo, "commit", "-qm", "tracks secrets by mistake")
    return pathRepo


@pytest.mark.falsification
@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def testTheGitTrackedScopeOmitsEverySecretPathEvenWhenCommitted(
        pathSecretRepo, tmp_path):
    """Kills: dropping an entry for the git-tracked capture."""
    dictIdentity = _fdictObserve(LocalRepoConnection(pathSecretRepo))
    assert sorted(dictIdentity["dictEligible"]) == ["source.py"]
    setPolicyOmitted = {
        sPath for sPath, listRow
        in dictIdentity["dictTrackedOmissions"].items()
        if listRow[0] == "policyExcluded"}
    assert setPolicyOmitted == set(LIST_SECRET_BEARING_PATHS)
    connection = LocalRepoConnection(pathSecretRepo)
    _fdictCaptureTracked(connection, tmp_path)
    assert connection.container.listRequestedPaths == [
        f"{S_TRACKED_REPO_ROOT}/source.py"]
    baSealed = (tmp_path / "councils" / "campaign-scope" / "snapshot"
                / "snapshot.tar").read_bytes()
    assert S_SECRET_TEXT.encode() not in baSealed


@pytest.mark.falsification
def testTheSizeProbeWeighsExactlyWhatTheCaptureKeeps(tmp_path):
    """Kills: the probe pruning fewer paths than the capture excludes.

    The probe runs the real program text. A pruning gap makes the
    pre-flight report a repository heavier than the one captured.
    """
    listLookalikeFiles = [
        sPath for sPath in LIST_LOOKALIKE_PATHS if sPath != ".config"]
    for sPath in LIST_SECRET_BEARING_PATHS:
        _fnWrite(tmp_path, sPath, S_SECRET_TEXT)
        _fnWrite(tmp_path, S_NESTING + sPath, S_SECRET_TEXT)
    for sPath in listLookalikeFiles + ["kept.txt"]:
        _fnWrite(tmp_path, sPath, "x\n")
    dictWeight = LocalRepoConnection(tmp_path)._fdictRunProgram(
        dockerConnection.S_TYPED_READ_REPOSITORY_WEIGHT)
    assert dictWeight["iFileCount"] == len(listLookalikeFiles) + 1


def testTheProbeMirrorsMatchThePolicy():
    assert set(
        dockerConnection._TUPLE_REPOSITORY_WEIGHT_PRUNED_COMPONENTS,
    ) == set(agentCouncilContext.DICT_EXCLUDED_COMPONENT_REASONS)
    assert set(
        dockerConnection._TUPLE_REPOSITORY_WEIGHT_PRUNED_PREFIXES,
    ) == set(agentCouncilContext.DICT_EXCLUDED_COMPONENT_PREFIX_REASONS)
    assert set(
        dockerConnection._TUPLE_REPOSITORY_WEIGHT_PRUNED_SEQUENCES,
    ) == set(agentCouncilContext.DICT_EXCLUDED_COMPONENT_SEQUENCE_REASONS)
