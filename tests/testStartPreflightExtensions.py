"""Pre-flight extension tests for `vaibify start` (F-S-06, F-S-07)."""

from types import SimpleNamespace
from unittest.mock import patch


def _fConfigForPreflight(listBindMounts=None, sProjectName="proj"):
    """Return a minimal config namespace with only bind-mount fields set."""
    return SimpleNamespace(
        sProjectName=sProjectName,
        sWorkspaceRoot="/workspace",
        listPorts=[],
        listBindMounts=listBindMounts or [],
    )


# -----------------------------------------------------------------------
# F-S-06 — bind-mount path format
# -----------------------------------------------------------------------


def test_bind_mount_path_with_space_emits_warn(tmp_path):
    """A bind-mount host path containing a space emits a warn-level result."""
    from vaibify.cli.commandStart import _flistpreflightBindMountFormats
    sPathSpace = "/Users/example/My Stuff/data"
    config = _fConfigForPreflight(listBindMounts=[
        {"host": sPathSpace, "container": "/data"},
    ])
    listResults = _flistpreflightBindMountFormats(config)
    assert len(listResults) == 1
    assert listResults[0].sLevel == "warn"
    assert sPathSpace in listResults[0].sMessage


def test_bind_mount_path_clean_yields_no_result():
    """A well-behaved host path produces no warn entry."""
    from vaibify.cli.commandStart import _flistpreflightBindMountFormats
    config = _fConfigForPreflight(listBindMounts=[
        {"host": "/Users/example/data", "container": "/data"},
    ])
    listResults = _flistpreflightBindMountFormats(config)
    assert listResults == []


def test_bind_mount_path_with_unicode_emits_warn():
    """A non-ASCII host path emits a warn-level result."""
    from vaibify.cli.commandStart import _flistpreflightBindMountFormats
    sPathUnicode = "/Users/example/d\u00e9j\u00e0/data"
    config = _fConfigForPreflight(listBindMounts=[
        {"host": sPathUnicode, "container": "/data"},
    ])
    listResults = _flistpreflightBindMountFormats(config)
    assert len(listResults) == 1
    assert listResults[0].sLevel == "warn"


# -----------------------------------------------------------------------
# F-S-07 — Colima file-sharing roots
# -----------------------------------------------------------------------


def test_bind_mount_outside_colima_shared_roots_fails():
    """A bind-mount source outside Colima's shared roots emits a fail."""
    from vaibify.cli.commandStart import _flistpreflightColimaSharedRoots
    config = _fConfigForPreflight(listBindMounts=[
        {"host": "/opt/foo", "container": "/data"},
    ])
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.cli.commandStart._flistColimaSharedRoots",
        return_value=["/Users", "/private/tmp"],
    ):
        listResults = _flistpreflightColimaSharedRoots(config)
    assert len(listResults) == 1
    assert listResults[0].sLevel == "fail"
    assert "Colima isn't sharing" in listResults[0].sMessage
    assert "colima start --mount" in listResults[0].sRemediation


def test_bind_mount_inside_colima_shared_roots_passes(tmp_path):
    """A bind-mount source under a shared root yields no fail."""
    from vaibify.cli.commandStart import _flistpreflightColimaSharedRoots
    sShared = str(tmp_path)
    sChild = f"{sShared}/sub"
    config = _fConfigForPreflight(listBindMounts=[
        {"host": sChild, "container": "/data"},
    ])
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.cli.commandStart._flistColimaSharedRoots",
        return_value=[sShared],
    ):
        listResults = _flistpreflightColimaSharedRoots(config)
    assert listResults == []


def test_colima_share_check_skipped_when_colima_inactive():
    """No result is emitted when Colima is not the active context."""
    from vaibify.cli.commandStart import _flistpreflightColimaSharedRoots
    config = _fConfigForPreflight(listBindMounts=[
        {"host": "/opt/foo", "container": "/data"},
    ])
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=False,
    ):
        listResults = _flistpreflightColimaSharedRoots(config)
    assert listResults == []
