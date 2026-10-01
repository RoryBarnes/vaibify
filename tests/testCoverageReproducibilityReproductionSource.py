"""Refusals and redactions on the "reproduce a published project" input side.

Real git runs against real local repositories for every origin
question; the only replacement is ``subprocess.run`` in the two tests
about a git that is missing or hangs. ``$HOME`` and the staging root
are redirected under ``tmp_path`` so the admitted-clone root is a
directory the test owns.
"""

import json
import os
import subprocess

import pytest

from vaibify.reproducibility import imageArchive, reproductionSource
from vaibify.reproducibility.reproductionSource import (
    ReproductionSourceRefusedError,
)


_REAL_RUN = subprocess.run


def fsRunGit(listArguments, sCwd):
    """Run git for real inside a fixture and return its stdout."""
    return _REAL_RUN(
        ["git", "-c", "user.email=fixture@example.invalid",
         "-c", "user.name=fixture", "-c", "commit.gpgsign=false",
         *listArguments],
        cwd=sCwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def pathHome(monkeypatch, tmp_path):
    """A private home that is the one admitted local-clone root."""
    pathPrivateHome = tmp_path / "home"
    pathPrivateHome.mkdir()
    monkeypatch.setenv("HOME", str(pathPrivateHome))
    return pathPrivateHome


def fsInitRepository(pathRepo, sOrigin=None):
    """Create a git repository with one commit and an optional origin."""
    pathRepo.mkdir(parents=True)
    fsRunGit(["init", "-q"], str(pathRepo))
    fsRunGit(["commit", "-q", "--allow-empty", "-m", "initial"], str(pathRepo))
    if sOrigin is not None:
        fsRunGit(["remote", "add", "origin", sOrigin], str(pathRepo))
    return str(pathRepo)


# ── recorded remotes never carry userinfo ────────────────────────


@pytest.mark.parametrize("sUrl, sExpected", [
    ("git@host.example:owner/repo.git", "host.example:owner/repo.git"),
    ("https://user:secretValue@host.example:8443/o/r.git",
     "https://host.example:8443/o/r.git"),
    ("relative/path/only", "relative/path/only"),
])
def testUserinfoIsStrippedFromEveryRemoteShape(sUrl, sExpected):
    """An scp-like, a URL with a port, and a bare path are all handled."""
    assert reproductionSource._fsStripUserinfo(sUrl) == sExpected


def testAnHttpsOriginIsReportedWithoutItsCredentials(pathHome):
    """A clone whose origin embeds a password reports the host and path."""
    sClone = fsInitRepository(
        pathHome / "cloneAlpha",
        "https://user:secretValue@host.example/owner/repo.git",
    )
    assert reproductionSource._fsAdmittedOriginUrl(sClone) == (
        "https://host.example/owner/repo.git"
    )


def testAnScpLikeOriginIsReportedWithoutItsUser(pathHome):
    """``git@host:path`` is reported as ``host:path``."""
    sClone = fsInitRepository(
        pathHome / "cloneAlpha", "git@host.example:owner/repo.git",
    )
    assert reproductionSource._fsAdmittedOriginUrl(sClone) == (
        "host.example:owner/repo.git"
    )


def testALocalOriginUnderHomeIsAdmittedButNeverReported(pathHome):
    """A host path is not a remote: admitted, and reported as empty."""
    sUpstream = fsInitRepository(pathHome / "upstreamAlpha")
    sClone = fsInitRepository(pathHome / "cloneAlpha", sUpstream)
    assert reproductionSource._fsAdmittedOriginUrl(sClone) == ""


def testALocalOriginOutsideHomeIsRefused(pathHome, tmp_path):
    """An origin path outside the admitted root is refused, not read."""
    sOutside = fsInitRepository(tmp_path / "outsideHome")
    sClone = fsInitRepository(pathHome / "cloneAlpha", sOutside)
    with pytest.raises(
        ReproductionSourceRefusedError,
        match="the clone's origin at .* lies outside the directories",
    ):
        reproductionSource._fsAdmittedOriginUrl(sClone)


def testTheAdmittedCloneRootIsTheResolvedHome(pathHome):
    """Exactly one root, the real path of the home directory."""
    assert reproductionSource.flistAdmittedLocalCloneRoots() == [
        os.path.realpath(str(pathHome)),
    ]


# ── git itself missing or hanging ────────────────────────────────


@pytest.mark.parametrize("errorRaised, iExpectedCode", [
    (FileNotFoundError("git: not found"), 127),
    (subprocess.TimeoutExpired(["git"], 30.0), 124),
])
def testAGitThatCannotRunIsASyntheticFailureNotAnException(
    monkeypatch, errorRaised, iExpectedCode,
):
    """Missing git is 127 and a hung git is 124, both as answers."""
    def fnRaise(*args, **kwargs):
        raise errorRaised

    monkeypatch.setattr(reproductionSource.subprocess, "run", fnRaise)
    processGit = reproductionSource._fprocessRunGit(["rev-parse", "HEAD"])
    assert processGit.returncode == iExpectedCode
    assert processGit.args == ["git", "rev-parse", "HEAD"]
    assert processGit.stdout == ""


# ── ssh batch mode and repository names ──────────────────────────


def testBatchModeIsTheFirstSshOption():
    """The enforced option precedes an inherited ``BatchMode=no``."""
    assert reproductionSource._fsBatchModeSshCommand(
        "ssh -o BatchMode=no -i keyFile",
    ) == "ssh -o BatchMode=yes -o BatchMode=no -i keyFile"


@pytest.mark.parametrize("sInherited", ["ssh -i 'unterminated", "", None])
def testAnUnparseableOrEmptySshCommandIsReplaced(sInherited):
    """A command that is not shell words is not trusted."""
    assert reproductionSource._fsBatchModeSshCommand(sInherited) == (
        "ssh -o BatchMode=yes"
    )


@pytest.mark.parametrize("sSource, sExpected", [
    ("https://host.example/owner/repo.git/", "repo"),
    ("git@host.example:owner/My Repo.git", "My-Repo"),
    ("https://host.example/owner/.git", "project"),
])
def testTheStagedDirectoryNameIsASafeBareName(sSource, sExpected):
    """The ``.git`` suffix is dropped and unsafe characters replaced."""
    assert reproductionSource._fsRepositoryNameFromSource(sSource) == (
        sExpected
    )


# ── dirty trees ──────────────────────────────────────────────────


def testDirtyPathsSeparateVaibifyBookkeepingFromTheResearchers():
    """Each half is named with its own remedy."""
    sDescription = reproductionSource._fsDescribeDirtyPaths([
        " M analysis.py", "?? .vaibify/syncStatus.json",
    ])
    sTheirs, sMine = sDescription.split("\n\n")
    assert sTheirs.startswith("Your unpublished work:")
    assert "analysis.py" in sTheirs
    assert sMine.startswith("Files vaibify itself wrote")
    assert "syncStatus.json" in sMine
    assert "analysis.py" not in sMine


def testOnlyBookkeepingDirtIsNotCalledTheResearchersWork():
    """A tree dirty only with vaibify's files names only that half."""
    sDescription = reproductionSource._fsDescribeDirtyPaths(
        ["?? .vaibify/syncStatus.json"],
    )
    assert "Your unpublished work" not in sDescription
    assert sDescription.startswith("Files vaibify itself wrote")


# ── workflow discovery and selection ─────────────────────────────


def testDiscoveryReadsDeclaredNamesAndFallsBackToTheStem(tmp_path):
    """Non-JSON files are skipped; an unreadable workflow uses its stem."""
    pathProjects = tmp_path / ".vaibify" / "projects"
    pathProjects.mkdir(parents=True)
    (pathProjects / "alpha.json").write_text(
        json.dumps({"sWorkflowName": "Alpha Analysis"}), "utf-8",
    )
    (pathProjects / "beta.json").write_text("{broken", "utf-8")
    (pathProjects / "README.md").write_text("not a workflow", "utf-8")
    assert reproductionSource._flistDiscoverWorkflowFiles(str(tmp_path)) == [
        {"sPath": ".vaibify/projects/alpha.json", "sName": "Alpha Analysis"},
        {"sPath": ".vaibify/projects/beta.json", "sName": "beta"},
    ]


def testSelectingFromNothingIsRefusedByPlace():
    """No workflows names where the search looked."""
    with pytest.raises(ValueError, match="no vaibify workflow found in the"):
        reproductionSource.fdictSelectWorkflowEntry(
            [], "", "the staged snapshot",
        )


def testANameMatchingTwoWorkflowsIsRefused():
    """A name shared by two workflows asks for the full path."""
    listWorkflows = [
        {"sPath": ".vaibify/projects/a.json", "sName": "Shared"},
        {"sPath": ".vaibify/workflows/b.json", "sName": "Shared"},
    ]
    with pytest.raises(ValueError, match="matches more than one workflow"):
        reproductionSource.fdictSelectWorkflowEntry(
            listWorkflows, "Shared", "the staged snapshot",
        )
    assert reproductionSource.fdictSelectWorkflowEntry(
        listWorkflows, ".vaibify/workflows/b.json", "the staged snapshot",
    ) == listWorkflows[1]


# ── strict loading, manifest, deposit facts ──────────────────────


@pytest.mark.parametrize("sContent, sMatch", [
    ("{not json", "could not be read as JSON"),
    ("[1, 2, 3]", "is not a JSON object"),
])
def testAProjectFileThatIsNotAnObjectIsRefusedUnderRuleOne(
    tmp_path, sContent, sMatch,
):
    """Rule 1 names the file and the way it failed."""
    (tmp_path / "project.json").write_text(sContent, "utf-8")
    with pytest.raises(
        ReproductionSourceRefusedError, match=sMatch,
    ) as infoError:
        reproductionSource._fdictLoadWorkflowStrictly(
            str(tmp_path), "project.json",
        )
    assert str(infoError.value).startswith("rule 1 (project file loads)")


def testAMissingManifestIsRefusedUnderRuleThree(tmp_path):
    """No MANIFEST.sha256 refuses by rule and by file."""
    with pytest.raises(
        ReproductionSourceRefusedError,
        match=r"rule 3 \(manifest parses\): MANIFEST\.sha256 is missing",
    ):
        reproductionSource._flistParseManifestOrRefuse(str(tmp_path))


def fdictEnvelopeWithRecord(sArchitecture):
    """Return an envelope whose deposit covers its pinned image."""
    sDigest = "registry.example/projectAlpha@sha256:" + "a" * 64
    return {"dictContainer": {
        "sImageDigest": sDigest, "sArchitecture": sArchitecture,
        imageArchive.S_IMAGE_ARCHIVE_KEY: {
            "sVersionDoi": "10.5281/zenodo.77",
            "sConceptDoi": "10.5281/zenodo.76",
            "sTarballSha256": "sha256:" + "b" * 64,
            "iTarballBytes": 1024,
            "sProvenance": imageArchive.S_PROVENANCE_ORIGINAL,
            "sImageDigest": sDigest, "sArchitecture": "amd64",
        },
    }}


def testADepositCoveringTheEnvelopeIsRecorded():
    """A covering deposit is on record under its version DOI."""
    assert reproductionSource._fdictDepositFacts(
        fdictEnvelopeWithRecord("amd64"),
    ) == {"bDepositOnRecord": True, "sDepositVersionDoi": "10.5281/zenodo.77"}


def testADepositThatCannotBeComparedIsRefusedUnderRuleSix():
    """An envelope with no architecture cannot be checked: refused."""
    with pytest.raises(
        ReproductionSourceRefusedError,
        match=r"rule 6 \(deposit covers the envelope\).*no architecture",
    ):
        reproductionSource._fdictDepositFacts(fdictEnvelopeWithRecord(""))


# ── staging tokens ───────────────────────────────────────────────


def testAnUnknownStagingTokenIsRefusedByName(monkeypatch, tmp_path):
    """A bare token with no record is refused, naming the token."""
    monkeypatch.setattr(
        reproductionSource, "_S_REPRODUCTIONS_DIRECTORY", str(tmp_path),
    )
    with pytest.raises(
        ReproductionSourceRefusedError,
        match="no staged snapshot is recorded under token 'tokenAlpha'",
    ):
        reproductionSource._fdictReadSourceRecord("tokenAlpha")


def testATokenThatIsAPathIsRefusedBeforeAnyRead(monkeypatch, tmp_path):
    """A token with a separator never becomes a path."""
    monkeypatch.setattr(
        reproductionSource, "_S_REPRODUCTIONS_DIRECTORY", str(tmp_path),
    )
    with pytest.raises(ReproductionSourceRefusedError, match="not a bare"):
        reproductionSource._fdictReadSourceRecord("../escape")


def testRemovingAnAbsentFileIsQuiet(tmp_path):
    """Cleanup tolerates a file that is already gone."""
    reproductionSource._fnRemoveQuietly(str(tmp_path / "gone.tar"))
    (tmp_path / "present.tar").write_bytes(b"x")
    reproductionSource._fnRemoveQuietly(str(tmp_path / "present.tar"))
    assert list(tmp_path.iterdir()) == []
