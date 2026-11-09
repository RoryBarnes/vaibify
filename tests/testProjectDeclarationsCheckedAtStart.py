"""The container's start names a project declaration the image does not hold.

The build installs each project's declaration as it stood on the
project's remote, so an edit made since works in nobody's pulled image
and in no Level 3 rerun. The start asks the image's own packages whether
they satisfy the working copy's file, and the remedy depends on where
the edit lives: not committed, committed but not pushed, or pushed but
not built.

These source the REAL ``entrypoint.sh`` and run the REAL checker it
ships, against real git repositories in each state. ``python3`` on PATH
is this suite's own interpreter, which has ``packaging``, so the
installed set is this interpreter's: ``packaging>=9999`` is a
requirement nothing satisfies and ``packaging>=1`` one that is met.
"""

import os
import subprocess
import sys

import pytest

_S_ENTRYPOINT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "vaibify", "containerImage",
    "entrypoint.sh",
))
_S_CHECKER = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "vaibify", "containerImage",
    "checkDeclaredRequirements.py",
))


def _fnGit(sDirectory, *listArguments):
    subprocess.run(
        ["git", "-c", "user.email=t@example.org", "-c", "user.name=t",
         "-C", sDirectory, *listArguments],
        check=True, capture_output=True, text=True,
    )


@pytest.fixture
def pathWorkspace(tmp_path):
    """Yield a workspace holding ``study``, cloned from a remote, published."""
    pathRemote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(pathRemote)],
                   check=True)
    pathWorkspace = tmp_path / "workspace"
    pathRepo = pathWorkspace / "study"
    pathWorkspace.mkdir()
    subprocess.run(["git", "clone", "-q", str(pathRemote), str(pathRepo)],
                   check=True, capture_output=True)
    _fnGit(str(pathRepo), "checkout", "-q", "-b", "main")
    (pathRepo / ".vaibify").mkdir()
    (pathRepo / ".vaibify" / "keep").write_text("")
    (pathRepo / "requirements.txt").write_text("packaging>=1\n")
    _fnGit(str(pathRepo), "add", "-A")
    _fnGit(str(pathRepo), "commit", "-q", "-m", "declare")
    _fnGit(str(pathRepo), "push", "-q", "-u", "origin", "main")
    return pathWorkspace


def _fnCommit(pathRepo, sText, bPush):
    (pathRepo / "requirements.txt").write_text(sText)
    _fnGit(str(pathRepo), "commit", "-q", "-am", "edit")
    if bPush:
        _fnGit(str(pathRepo), "push", "-q")


def _flistWarnings(tmp_path, pathWorkspace):
    pathBin = tmp_path / "bin"
    pathBin.mkdir(exist_ok=True)
    if not (pathBin / "python3").exists():
        os.symlink(sys.executable, pathBin / "python3")
    sScript = (
        "set +e\n"
        f"source {_S_ENTRYPOINT}\n"
        f"WORKSPACE={pathWorkspace}\n"
        f"S_DECLARATION_CHECKER={_S_CHECKER}\n"
        f"PATH={pathBin}:$PATH\n"
        "fnCheckProjectDeclarations\n"
        'printf "WARNING-LINE:%s\\n" "${saStartupWarnings[@]}"\n'
    )
    sOutput = subprocess.run(
        ["bash", "-c", sScript], capture_output=True, text=True, timeout=120,
    ).stdout
    return [sLine[len("WARNING-LINE:"):] for sLine in sOutput.splitlines()
            if sLine.startswith("WARNING-LINE:") and sLine != "WARNING-LINE:"]


def _fsInstalledPackaging():
    import importlib.metadata
    return importlib.metadata.version("packaging")


def testASatisfiedDeclarationSaysNothing(tmp_path, pathWorkspace):
    assert _flistWarnings(tmp_path, pathWorkspace) == []


def testAPublishedEditTheImagePredatesSaysRebuild(tmp_path, pathWorkspace):
    _fnCommit(pathWorkspace / "study", "packaging>=9999\n", bPush=True)
    assert _flistWarnings(tmp_path, pathWorkspace) == [
        "study: project-requirements-unbuilt: requirements.txt declares what "
        f"the image does not hold: packaging>=9999 ({_fsInstalledPackaging()} "
        "installed); Rebuild, which reads requirements.txt from the remote"
    ]


@pytest.mark.falsification
def testACommitNotYetPushedSaysPushFirst(tmp_path, pathWorkspace):
    """Kills: telling an unpushed edit to Rebuild.

    The build reads the remote, so a Rebuild alone would install the old
    declaration again. Mutation: the unpushed case answers "published".
    """
    _fnCommit(pathWorkspace / "study", "packaging>=9999\n", bPush=False)
    listWarnings = _flistWarnings(tmp_path, pathWorkspace)
    assert len(listWarnings) == 1
    assert listWarnings[0].startswith("study: project-requirements-unpushed: ")
    assert listWarnings[0].endswith(
        "push the commit that changed requirements.txt, then Rebuild")


@pytest.mark.parametrize("bTracked", [True, False])
def testAnEditNotYetCommittedSaysCommitAndPush(tmp_path, pathWorkspace, bTracked):
    pathRepo = pathWorkspace / "study"
    if not bTracked:
        _fnGit(str(pathRepo), "rm", "-q", "--cached", "requirements.txt")
        _fnGit(str(pathRepo), "commit", "-q", "-m", "untrack")
    (pathRepo / "requirements.txt").write_text("packaging[extra]>=9999\n")
    listWarnings = _flistWarnings(tmp_path, pathWorkspace)
    assert len(listWarnings) == 1
    assert listWarnings[0].startswith("study: project-requirements-uncommitted: ")
    assert listWarnings[0].endswith(
        "commit and push requirements.txt, then Rebuild")


def testTheFileTheBuildReadsIsTheOneChecked(tmp_path, pathWorkspace):
    """pyproject.toml comes first in the lock's order, so it is the one read."""
    if sys.version_info < (3, 11):
        pytest.skip("reading TOML needs Python 3.11, as it does in the image")
    pathRepo = pathWorkspace / "study"
    (pathRepo / "pyproject.toml").write_text(
        '[project]\nname = "study"\ndependencies = ["packaging>=9999"]\n')
    listWarnings = _flistWarnings(tmp_path, pathWorkspace)
    assert len(listWarnings) == 1
    assert "pyproject.toml declares what the image does not hold" in listWarnings[0]


def testAnImageWithoutTheCheckerSaysNothing(tmp_path, pathWorkspace):
    _fnCommit(pathWorkspace / "study", "packaging>=9999\n", bPush=True)
    sScript = (
        "set +e\n"
        f"source {_S_ENTRYPOINT}\n"
        f"WORKSPACE={pathWorkspace}\n"
        f"S_DECLARATION_CHECKER={tmp_path}/absent.py\n"
        "fnCheckProjectDeclarations\n"
        'printf "WARNING-LINE:%s\\n" "${saStartupWarnings[@]}"\n'
    )
    sOutput = subprocess.run(["bash", "-c", sScript], capture_output=True,
                             text=True, timeout=60).stdout
    assert "project-requirements" not in sOutput
