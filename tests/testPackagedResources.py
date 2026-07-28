"""The wheel must contain the trees vaibify cannot run without.

``vaibify init`` copies ``vaibify/templates/`` and ``vaibify build``
sends ``vaibify/containerImage/`` to the Docker daemon. Both lived at
the repository root until 2026-07-27 and were reached with
``parents[2]``, which is the repository root only in a checkout. No
wheel ever contained them, and the release workflow could not see that
because it tested a distribution with ``import vaibify``.

These tests pin the things that had to be simultaneously true for that
bug to exist: the trees live inside the package, the packaging
metadata ships them, and no generated build artifact rides along.

What they do NOT prove is that a built wheel actually contains the
files -- that needs a built distribution, so it lives in
``tools/checkInstalledDistribution.py``, which the release workflow
runs against an installed sdist and an installed wheel. Read a green
run here as "the declaration is right", not as "the artifact is right".
"""

import pathlib
import subprocess

import pytest

from vaibify.resources import (
    S_CONTAINER_IMAGE_TREE,
    S_TEMPLATES_TREE,
    fpathContainerImageRoot,
    fpathTemplatesRoot,
)


_PATH_REPO = pathlib.Path(__file__).resolve().parent.parent

# Files ``fnPrepareBuildContext`` writes into a build context. They are
# per-project output, they are staged under ``~/.vaibify/build/``, and
# a copy inside the packaged tree would ship a stale image input that
# the build-input hash does not cover.
_T_GENERATED_CONTEXT_ARTIFACTS = (
    "director.py",
    "overleafSync.py",
    "latexConnector.py",
    "zenodoClient.py",
    "container.conf",
    "requirements.txt",
    "system-packages.txt",
    "pip-flags.txt",
    "binaries.env",
)


def testTemplatesResolveInsideThePackage():
    """The templates tree must be package data, not a sibling of it."""
    pathTemplates = fpathTemplatesRoot()
    assert pathTemplates.parent.name == "vaibify", (
        f"templates resolved to '{pathTemplates}', which is outside "
        f"the vaibify package; a wheel will not contain it"
    )
    listTemplates = [p.name for p in pathTemplates.iterdir() if p.is_dir()]
    assert listTemplates, "no project templates are shipped"


def testContainerContextResolvesInsideThePackage():
    """The Docker build context must be package data.

    The old lookup returned ``<parent-of-vaibify>/docker``, which in an
    installed environment is ``site-packages/docker`` -- the Docker
    SDK's own source directory. It exists, so an ``is_dir()`` check
    passed and the wrong tree would have been handed to the daemon.
    """
    pathContext = fpathContainerImageRoot()
    assert pathContext.parent.name == "vaibify", (
        f"build context resolved to '{pathContext}', outside the "
        f"vaibify package"
    )
    for sRequired in ("Dockerfile", "entrypoint.sh", "vaibifyDo.py"):
        assert (pathContext / sRequired).is_file(), (
            f"build context is missing {sRequired}"
        )


def testDockerDirDoesNotResolveOntoTheDockerSdk():
    """``fsDockerDir`` must never point at the installed docker package."""
    import docker

    from vaibify.cli.configLoader import fsDockerDir

    pathSdk = pathlib.Path(docker.__file__).resolve().parent
    pathContext = pathlib.Path(fsDockerDir()).resolve()
    assert pathContext != pathSdk, (
        "fsDockerDir() returned the Docker SDK's source directory; "
        "this is the wheel-install failure mode that an is_dir() "
        "check cannot catch"
    )


@pytest.mark.parametrize("sTreeName", [
    S_TEMPLATES_TREE, S_CONTAINER_IMAGE_TREE,
])
def testPackageDataDeclaresEachShippedTree(sTreeName):
    """pyproject must declare each tree, or no wheel will carry it.

    This asserts the *declaration*. The artifact-level check is
    ``tools/checkInstalledDistribution.py`` in the release workflow.
    """
    sDeclaration = _fsReadPackageDataSection()
    assert f"{sTreeName}/**/*" in sDeclaration, (
        f"'{sTreeName}' is not declared in "
        f"[tool.setuptools.package-data]; a built wheel will silently "
        f"omit it, exactly as every wheel before 2026-07-27 did"
    )


def _fsReadPackageDataSection():
    """Return the body of the package-data table from pyproject.toml.

    Parsed by hand rather than with tomllib, which arrived in 3.11
    while vaibify supports 3.9. The alternative was skipping this test
    on the two oldest interpreters, and a check that reports success
    for having run nothing is worse than no check.
    """
    listLines = (
        _PATH_REPO / "pyproject.toml"
    ).read_text().splitlines()
    iStart = listLines.index("[tool.setuptools.package-data]")
    listBody = []
    for sLine in listLines[iStart + 1:]:
        if sLine.startswith("["):
            break
        listBody.append(sLine)
    return "\n".join(listBody)


def testNoGeneratedBuildArtifactSitsInThePackagedContext():
    """A build's output must never be committed into the context.

    These nine files were untracked *and* unignored for months. The
    move into the package swept them along, and they reached a built
    wheel -- where a stale ``requirements.txt`` changes the image
    without changing the build-input hash that is supposed to describe
    it.
    """
    pathContext = fpathContainerImageRoot()
    listPresent = [
        sName for sName in _T_GENERATED_CONTEXT_ARTIFACTS
        if (pathContext / sName).exists()
    ]
    assert not listPresent, (
        f"generated build artifacts are sitting in the packaged "
        f"context and will ship in the wheel: {listPresent}"
    )


def testGeneratedContextArtifactsCannotBeCommitted():
    """Every generated artifact name must be gitignored.

    Being untracked is not protection: an ``git add -A`` or a
    directory move commits them. Only an ignore rule prevents it.
    """
    listUnignored = []
    for sName in _T_GENERATED_CONTEXT_ARTIFACTS:
        sRelative = f"vaibify/{S_CONTAINER_IMAGE_TREE}/{sName}"
        resultProcess = subprocess.run(
            ["git", "check-ignore", "-q", sRelative],
            cwd=str(_PATH_REPO), capture_output=True,
        )
        if resultProcess.returncode != 0:
            listUnignored.append(sRelative)
    assert not listUnignored, (
        f"these generated build artifacts are not gitignored: "
        f"{listUnignored}"
    )
