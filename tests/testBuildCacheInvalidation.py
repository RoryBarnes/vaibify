"""Build-cache invalidation on ARG-hash drift (F-B-14)."""

from types import SimpleNamespace
from unittest.mock import patch

from vaibify.cli import commandBuild
from vaibify.cli.commandBuild import (
    fbBuildArgsChangedSinceLastBuild,
    fnRecordBuildArgHash,
    fsBuildArgHash,
)


def _fConfigArgs(
    sPython="3.11",
    sBase="ubuntu:24.04",
    sPackageManager="pip",
    bLatex=False,
):
    """Return a stub config exposing only the ARG-affecting fields."""
    return SimpleNamespace(
        sProjectName="cacheproj",
        sPythonVersion=sPython,
        sBaseImage=sBase,
        sPackageManager=sPackageManager,
        features=SimpleNamespace(bLatex=bLatex, bGpu=False),
    )


def test_hash_changes_when_python_version_changes():
    """Two configs differing in PYTHON_VERSION produce different hashes."""
    sHashOne = fsBuildArgHash(_fConfigArgs(sPython="3.11"))
    sHashTwo = fsBuildArgHash(_fConfigArgs(sPython="3.12"))
    assert sHashOne != sHashTwo


def test_hash_stable_for_same_inputs():
    """The hash is reproducible across calls."""
    sHashOne = fsBuildArgHash(_fConfigArgs())
    sHashTwo = fsBuildArgHash(_fConfigArgs())
    assert sHashOne == sHashTwo


def test_hash_changes_when_latex_flips():
    """Toggling INSTALL_LATEX flips the hash."""
    sHashOff = fsBuildArgHash(_fConfigArgs(bLatex=False))
    sHashOn = fsBuildArgHash(_fConfigArgs(bLatex=True))
    assert sHashOff != sHashOn


def test_first_build_does_not_force_no_cache(tmp_path):
    """When no hash file exists yet, no --no-cache is forced."""
    with patch.object(
        commandBuild, "_S_BUILD_HASH_DIRECTORY", str(tmp_path),
    ):
        config = _fConfigArgs()
        assert fbBuildArgsChangedSinceLastBuild(config) is False


def test_unchanged_hash_does_not_force_no_cache(tmp_path):
    """A matching saved hash does not force --no-cache."""
    with patch.object(
        commandBuild, "_S_BUILD_HASH_DIRECTORY", str(tmp_path),
    ):
        config = _fConfigArgs()
        fnRecordBuildArgHash(config)
        assert fbBuildArgsChangedSinceLastBuild(config) is False


def test_changed_hash_forces_no_cache(tmp_path):
    """A different saved hash signals that --no-cache must be applied."""
    with patch.object(
        commandBuild, "_S_BUILD_HASH_DIRECTORY", str(tmp_path),
    ):
        configOld = _fConfigArgs(sPython="3.11")
        fnRecordBuildArgHash(configOld)
        configNew = _fConfigArgs(sPython="3.12")
        assert fbBuildArgsChangedSinceLastBuild(configNew) is True


def test_record_creates_directory_if_missing(tmp_path):
    """fnRecordBuildArgHash creates the cache directory."""
    sCacheDir = str(tmp_path / "cache" / "nested")
    with patch.object(
        commandBuild, "_S_BUILD_HASH_DIRECTORY", sCacheDir,
    ):
        config = _fConfigArgs()
        fnRecordBuildArgHash(config)
        from pathlib import Path
        assert Path(sCacheDir).is_dir()


def test_build_from_config_forces_no_cache_on_drift(tmp_path):
    """fnBuildFromConfig prepends --no-cache when ARG-hash drifts."""
    listSeenNoCache = []

    def fakeBuild(config, sDockerDir, bNoCache=False):
        listSeenNoCache.append(bNoCache)

    config = _fConfigArgs(sPython="3.11")
    with patch.object(
        commandBuild, "_S_BUILD_HASH_DIRECTORY", str(tmp_path),
    ):
        # Seed a different prior hash.
        fnRecordBuildArgHash(_fConfigArgs(sPython="3.12"))
        with patch(
            "vaibify.cli.commandBuild.fnPrepareBuildContext",
        ), patch(
            "vaibify.cli.commandBuild.fnPruneDanglingImages",
        ), patch(
            "vaibify.docker.imageBuilder.fnBuildImage", side_effect=fakeBuild,
        ):
            commandBuild.fnBuildFromConfig(config, "/dk", bNoCache=False)
    assert listSeenNoCache == [True]


def test_build_from_config_does_not_force_when_first_build(tmp_path):
    """Without a prior hash file the build runs with bNoCache=False."""
    listSeenNoCache = []

    def fakeBuild(config, sDockerDir, bNoCache=False):
        listSeenNoCache.append(bNoCache)

    config = _fConfigArgs()
    with patch.object(
        commandBuild, "_S_BUILD_HASH_DIRECTORY", str(tmp_path),
    ):
        with patch(
            "vaibify.cli.commandBuild.fnPrepareBuildContext",
        ), patch(
            "vaibify.cli.commandBuild.fnPruneDanglingImages",
        ), patch(
            "vaibify.docker.imageBuilder.fnBuildImage", side_effect=fakeBuild,
        ):
            commandBuild.fnBuildFromConfig(config, "/dk", bNoCache=False)
    assert listSeenNoCache == [False]
