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
import tempfile

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


def testEveryCuratedDocResolvesInsideThePackage():
    """The docs staged into the image must be reachable from a wheel.

    Five of the six were named at the repository's top-level ``docs/``,
    which no distribution contains, so a wheel-built image shipped one
    document while the bundled ``vaibify-doc-map`` skill told the agent
    all six were at ``/usr/share/vaibify/docs/``. The image did not
    merely lack docs; it misdirected the agent, and an image built from
    a checkout differed materially from one built from a release.
    """
    from vaibify.cli.commandBuild import T_STAGED_DOCS

    listOutside = [
        sSource for sSource, _sDest in T_STAGED_DOCS
        if not sSource.startswith("vaibify/")
    ]
    assert listOutside == [], (
        f"these staged docs live outside the package and cannot ship "
        f"in a distribution: {listOutside}"
    )
    listMissing = [
        sSource for sSource, _sDest in T_STAGED_DOCS
        if not (_PATH_REPO / sSource).is_file()
    ]
    assert listMissing == [], (
        f"staged doc sources that do not resolve: {listMissing}"
    )


def testCuratedDocsRemainSymlinksOntoTheSphinxSources():
    """The package copies must stay links, never second real files.

    ``vaibify/docs/`` holds the container's copy of five documents that
    Sphinx owns under ``docs/``. They are symlinks so there is exactly
    one file to edit; both builders dereference them into real files in
    the distribution. Replacing one with a real file reintroduces the
    shadowing trap: two copies with nothing forcing them to agree, and
    the container silently serving the stale one.
    """
    pathPackageDocs = _PATH_REPO / "vaibify" / "docs"
    listBroken = [
        pathDoc.name for pathDoc in sorted(pathPackageDocs.iterdir())
        if pathDoc.is_symlink() and not pathDoc.is_file()
    ]
    assert listBroken == [], (
        f"dangling symlinks in vaibify/docs/: {listBroken}"
    )
    listShadowed = [
        pathDoc.name for pathDoc in sorted(pathPackageDocs.iterdir())
        if not pathDoc.is_symlink()
        and (_PATH_REPO / "docs" / pathDoc.name).is_file()
    ]
    assert listShadowed == [], (
        f"these are real files in vaibify/docs/ while docs/ also has "
        f"a copy; make them symlinks so there is one source: "
        f"{listShadowed}"
    )


def testShellCompletionsShipInsideThePackage():
    """Tab completion must find its scripts in an installation.

    ``_fsCompletionsDirectory`` reads ``<package>/completions`` while
    the scripts sat at the repository root, so the lookup resolved to a
    path present in no installation *and no checkout*. Completion had
    never worked, and first-run setup recorded a permanent marker
    saying it had.
    """
    from vaibify.install.shellSetup import (
        _fsCompletionPathForShell, _fsCompletionsDirectory,
    )
    pathCompletions = pathlib.Path(_fsCompletionsDirectory())
    assert pathCompletions.parent.name == "vaibify", (
        f"completions resolved to '{pathCompletions}', outside the "
        f"package; no wheel will contain it"
    )
    for sShellName in ("bash", "zsh"):
        assert _fsCompletionPathForShell(sShellName), (
            f"no completion script resolves for {sShellName}"
        )


def testFirstRunMarkerIsWithheldWhenCompletionsAreMissing():
    """A broken install must not be recorded as finished setup.

    The marker is written once and checked forever, so writing it after
    a step that silently did nothing makes the failure permanent for
    that machine.
    """
    from unittest.mock import patch

    from vaibify.install import shellSetup

    with tempfile.TemporaryDirectory() as sScratch:
        sMarker = str(pathlib.Path(sScratch) / ".setup_done")
        with patch.object(shellSetup, "_MARKER_DIR", sScratch), \
                patch.object(shellSetup, "_MARKER_PATH", sMarker), \
                patch.object(shellSetup, "fnConfigureCompletions"), \
                patch.object(shellSetup, "fnConfigureHelperCommands"), \
                patch.object(shellSetup, "fnLinkColimaSocket"), \
                patch.object(
                    shellSetup, "fbCompletionsArePresent",
                    return_value=False,
                ):
            shellSetup.fnRunFirstTimeSetup()
        assert not pathlib.Path(sMarker).exists(), (
            "setup wrote its completion marker despite the "
            "completions being absent, so it will never retry"
        )


def testConcurrentBuildsGetPrivateStagingDirectories():
    """Two builds of one project must not share a context directory.

    The GUI starts builds in worker threads with no serialization, so
    two dashboard clicks — or a dashboard build and a CLI build — run
    at once. A per-project staging path made them share one directory
    whose refresh begins with ``rmtree``, so one build could delete the
    context out from under another that was still archiving it.
    """
    from vaibify.cli import commandBuild
    from vaibify.cli.configLoader import fsDockerDir
    from vaibify.config.projectConfig import ProjectConfig

    configProject = ProjectConfig(sProjectName="raceCheck")
    with tempfile.TemporaryDirectory() as sScratch:
        commandBuild._S_BUILD_STAGING_DIRECTORY = sScratch
        sFirst = commandBuild.fsStageBuildContext(
            configProject, fsDockerDir(),
        )
        sSecond = commandBuild.fsStageBuildContext(
            configProject, fsDockerDir(),
        )
        assert sFirst != sSecond, (
            "two concurrent builds of the same project were handed the "
            "same staging directory"
        )
        # The second staging must not have disturbed the first.
        assert (pathlib.Path(sFirst) / "Dockerfile").is_file()
        assert (pathlib.Path(sSecond) / "Dockerfile").is_file()
        commandBuild.fnDiscardBuildContext(sSecond)
        assert (pathlib.Path(sFirst) / "Dockerfile").is_file(), (
            "discarding one build's context destroyed another's"
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


def _fsCaptureDiscoveryFindCommand(sSearchRoot):
    """Return the exact find expression Project discovery runs."""
    from vaibify.gui.workflowManager import _flistDiscoverCandidatePaths

    listCaptured = []

    class _ConnectionRecording:
        def ftResultExecuteCommand(self, sContainerId, sCommand):
            listCaptured.append(sCommand)
            return (0, "")

    _flistDiscoverCandidatePaths(
        _ConnectionRecording(), "cid", sSearchRoot,
    )
    return listCaptured[0]


def testInitScaffoldsAProjectThatDiscoveryActuallyFinds():
    """``vaibify init --template`` must produce a discoverable Project.

    The template's project.json used to land at the repository root
    while discovery scanned only ``.vaibify/projects`` and the legacy
    ``.vaibify/workflows``. Every scaffolded project was therefore
    invisible to the dashboard and to ``vaibify run``, and init exited
    0 saying it had succeeded.

    This runs init for real and then runs discovery's own find
    expression over the result, so the two halves are checked against
    each other rather than against a restatement of either.
    """
    from unittest.mock import patch

    from click.testing import CliRunner

    from vaibify.cli.commandInit import init

    with tempfile.TemporaryDirectory() as sTempDir:
        pathRoot = pathlib.Path(sTempDir)
        pathRepo = pathRoot / "scaffold"
        pathRepo.mkdir()
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=str(pathRepo)) as sCwd:
            # The global project registry lives at ~/.vaibify and its
            # path is resolved at import, so it cannot be redirected by
            # an environment variable: without this the test rewrites
            # the developer's own registry entry for any project that
            # happens to share the template's name.
            with patch("vaibify.cli.commandInit.fnAddProject"):
                resultInit = runner.invoke(
                    init, ["--template", "workflow"],
                    catch_exceptions=False,
                )
            assert resultInit.exit_code == 0, resultInit.output
            # Search from the repo's parent, which is what /workspace is
            # to a project repo cloned directly inside it.
            sCommand = _fsCaptureDiscoveryFindCommand(
                str(pathlib.Path(sCwd).parent),
            )
            resultFind = subprocess.run(
                sCommand, shell=True, capture_output=True, text=True,
            )
            listFound = [
                sLine for sLine in resultFind.stdout.splitlines()
                if sLine.strip()
            ]
            listWritten = sorted(
                pathItem.name
                for pathItem in pathlib.Path(sCwd).iterdir()
            )
            assert listFound, (
                "discovery found no Project under the scaffolded "
                f"tree; init wrote {listWritten}"
            )
            assert all(
                pathlib.Path(sPath).is_file() for sPath in listFound
            )
