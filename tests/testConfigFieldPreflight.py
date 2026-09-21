"""The vaibify.yml fields a build turns into container identity are checked.

``containerUser`` becomes a ``useradd`` argument, ``pythonVersion`` is
pasted into ``apt-get install python<version>``, and ``workspaceRoot``
becomes every mount and every path the dashboard resolves. All three
were validated nowhere, so a wrong value failed the build minutes in
with apt's or useradd's words rather than the field's name.
"""

from types import SimpleNamespace

import pytest

from vaibify.cli.configFieldPreflight import (
    S_PREFLIGHT_NAME,
    flistDescribeInvalidFields,
    fpreflightConfigurationFields,
)
from vaibify.cli.preflightResult import S_LEVEL_FAIL


def _fconfigWith(**kwargs):
    dictFields = {
        "sContainerUser": "researcher",
        "sPythonVersion": "3.12",
        "sWorkspaceRoot": "/workspace",
    }
    dictFields.update(kwargs)
    return SimpleNamespace(**dictFields)


def test_a_config_the_wizards_write_is_silent():
    assert fpreflightConfigurationFields(_fconfigWith()) is None


@pytest.mark.falsification
def test_a_python_version_apt_cannot_install_is_refused():
    """The Dockerfile installs `python${PYTHON_VERSION}`, so the value
    is an apt package name: major.minor, never a patch version.

    Kills: accepting any pythonVersion, under which '3.12.1' becomes
    `apt-get install python3.12.1` and fails after the base image.
    """
    for sVersion in ("3.12.1", "python3.12", "3", "3.x", "", "2.7"):
        preflightFields = fpreflightConfigurationFields(
            _fconfigWith(sPythonVersion=sVersion),
        )
        assert preflightFields is not None, sVersion
        assert preflightFields.sLevel == S_LEVEL_FAIL
        assert preflightFields.sName == S_PREFLIGHT_NAME
        assert "pythonVersion" in preflightFields.sMessage
    for sVersion in ("3.9", "3.12", "3.14"):
        assert fpreflightConfigurationFields(
            _fconfigWith(sPythonVersion=sVersion),
        ) is None, sVersion


@pytest.mark.falsification
def test_root_is_refused_as_the_container_user():
    """The image drops privileges to this user; naming root would undo
    the unprivileged-user protection the entrypoint rests on.

    Kills: accepting any containerUser, root included.
    """
    preflightFields = fpreflightConfigurationFields(
        _fconfigWith(sContainerUser="root"),
    )
    assert preflightFields.sLevel == S_LEVEL_FAIL
    assert "root" in preflightFields.sMessage


def test_a_container_user_useradd_would_refuse_is_refused():
    for sUser in ("Researcher", "9lives", "my user", "a" * 33, ""):
        assert fpreflightConfigurationFields(
            _fconfigWith(sContainerUser=sUser),
        ) is not None, sUser
    for sUser in ("researcher", "_svc", "r2-d2"):
        assert fpreflightConfigurationFields(
            _fconfigWith(sContainerUser=sUser),
        ) is None, sUser


def test_a_workspace_root_that_is_not_an_absolute_path_is_refused():
    for sRoot in ("workspace", "./workspace", "/"):
        assert fpreflightConfigurationFields(
            _fconfigWith(sWorkspaceRoot=sRoot),
        ) is not None, sRoot
    assert fpreflightConfigurationFields(
        _fconfigWith(sWorkspaceRoot="/srv/work"),
    ) is None


def test_every_broken_field_is_named_at_once():
    """A researcher fixing one field must not discover the next only
    on the following build."""
    listComplaints = flistDescribeInvalidFields(_fconfigWith(
        sContainerUser="root", sPythonVersion="3.12.1",
        sWorkspaceRoot="workspace",
    ))
    assert len(listComplaints) == 3


def test_a_field_the_config_does_not_carry_is_not_graded():
    """Each is a dataclass field with a working default, so absence
    means the default applies; grading it would refuse every config
    built from a partial stub."""
    assert flistDescribeInvalidFields(SimpleNamespace()) == []
    assert flistDescribeInvalidFields(
        SimpleNamespace(sPythonVersion="3.12"),
    ) == []


def test_both_wizards_refuse_the_fields_at_save_rather_than_at_build():
    """A wizard that writes a value the build then refuses has only
    moved the discovery to the end of an hour.

    This drives the two REFUSAL helpers. That the create ROUTE calls
    its one is a separate claim, asserted through the route itself in
    testCreationWizardRoutes: a test that calls the helper directly
    survives the route dropping the call, which the falsification
    harness proved when this test carried that claim (2026-09-21).
    """
    from fastapi import HTTPException
    from vaibify.gui import routeContext
    from vaibify.install import setupServer

    requestBroken = SimpleNamespace(
        sProjectName="demo", sPackageManager="pip",
        sContainerUser="root", sPythonVersion="3.12",
        sWorkspaceRoot="/workspace",
    )
    with pytest.raises(HTTPException) as excinfo:
        routeContext.fnRefuseUnusableContainerFields(requestBroken)
    assert excinfo.value.status_code == 400
    assert "root" in excinfo.value.detail["sMessage"]

    listErrors = setupServer._flistCollectErrors(requestBroken)
    assert any("root" in sError for sError in listErrors)

    routeContext.fnRefuseUnusableContainerFields(SimpleNamespace(
        sContainerUser="researcher", sPythonVersion="3.12",
        sWorkspaceRoot="/workspace",
    ))
