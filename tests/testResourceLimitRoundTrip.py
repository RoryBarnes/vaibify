"""Config-writing routes must never persist a value that cannot be read back.

Both guards under test here failed the same way: the value passed the
API boundary, was rendered into ``vaibify.yml``, and only became a
problem on the *next* load — at which point the settings page, start,
and build all raise without naming a field, and the container cannot
start until a human hand-edits the YAML. So every test drives the full
chain: request -> guard -> file on disk -> reload.
"""

import os

import pytest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.testclient import TestClient

from vaibify.config.projectConfig import fconfigLoadFromFile
from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
from vaibify.gui.setupServer import (
    fappCreateSetupApplication,
    fnWriteConfigToDirectory,
)


S_PROJECT_NAME = "limits-project"


def _fsWriteLoadableConfig(pathDirectory):
    """Write a minimal vaibify.yml that fconfigLoadFromFile accepts."""
    sConfigPath = os.path.join(str(pathDirectory), "vaibify.yml")
    os.makedirs(str(pathDirectory), exist_ok=True)
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write(f"projectName: {S_PROJECT_NAME}\n")
    fconfigLoadFromFile(sConfigPath)
    return sConfigPath


@pytest.fixture
def fixtureSettingsClient(tmp_path):
    """Client plus config path for a registry project rooted in tmp_path."""
    from fastapi import FastAPI
    sConfigPath = _fsWriteLoadableConfig(tmp_path / S_PROJECT_NAME)
    app = FastAPI()
    fnRegisterRegistryRoutes(app, {"require": lambda: None, "docker": None})
    with patch(
        "vaibify.config.registryManager.fdictGetProject",
        return_value={
            "sName": S_PROJECT_NAME,
            "sDirectory": str(tmp_path / S_PROJECT_NAME),
            "sConfigPath": sConfigPath,
        },
    ):
        yield TestClient(app), sConfigPath


def _fnAssertConfigStillLoads(sConfigPath):
    """Fail loudly if vaibify.yml stopped being loadable."""
    configLoaded = fconfigLoadFromFile(sConfigPath)
    assert configLoaded.sProjectName == S_PROJECT_NAME


@pytest.mark.falsification
def test_non_finite_memory_limit_never_reaches_the_yaml(
    fixtureSettingsClient,
):
    """A NaN or infinite cap is refused, and the refusal says why.

    Pydantic coerces the strings "nan" and "inf" to floats that compare
    False against every ordering test, so the pre-fix guard let them
    through; ``%g`` then wrote them as bare words that PyYAML reads
    back as strings. The range check rejects them too — a NaN fails
    every comparison — so the finiteness check earns its place by
    naming the actual defect instead of quoting a range the value was
    never in.

    Kills: replacing the ``math.isfinite`` rejection in
    registryRoutes._fnRequireLimitWithinRange with a dead ``if False``
    answers "nan" with the range message, leaving the researcher to
    debug a bound their input never violated.
    """
    clientHttp, sConfigPath = fixtureSettingsClient
    for sValue in ("nan", "inf"):
        responseHttp = clientHttp.post(
            f"/api/containers/{S_PROJECT_NAME}/settings",
            json={"fMemoryLimitGigabytes": sValue},
        )
        assert responseHttp.status_code == 400
        assert "finite" in responseHttp.json()["detail"]
        with open(sConfigPath) as fileHandle:
            sContent = fileHandle.read()
        assert "memoryLimitGigabytes" not in sContent
        _fnAssertConfigStillLoads(sConfigPath)


@pytest.mark.falsification
def test_oversized_cpu_limit_never_reaches_the_yaml(
    fixtureSettingsClient,
):
    """A cap at or above 1e6 is refused before ``%g`` mangles it.

    ``f"{1000000:g}"`` is ``1e+06``, which PyYAML 1.1 resolves to a
    string rather than a number — the same brick as NaN, reachable by
    an ordinary fat-finger in the settings form.

    Kills: dropping the ``<= numberMaximum`` conjunct from
    registryRoutes._fnRequireLimitWithinRange admits 1000000, so the
    POST returns 200 and the reload raises.
    """
    clientHttp, sConfigPath = fixtureSettingsClient
    responseHttp = clientHttp.post(
        f"/api/containers/{S_PROJECT_NAME}/settings",
        json={"iCpuLimit": 1000000},
    )
    assert responseHttp.status_code == 400
    with open(sConfigPath) as fileHandle:
        assert "cpuLimit" not in fileHandle.read()
    _fnAssertConfigStillLoads(sConfigPath)


def test_valid_limits_are_written_and_reload_cleanly(
    fixtureSettingsClient,
):
    """Positive control: an in-range cap survives the round trip."""
    clientHttp, sConfigPath = fixtureSettingsClient
    responseHttp = clientHttp.post(
        f"/api/containers/{S_PROJECT_NAME}/settings",
        json={"iCpuLimit": 4, "fMemoryLimitGigabytes": 8.5},
    )
    assert responseHttp.status_code == 200
    configLoaded = fconfigLoadFromFile(sConfigPath)
    assert configLoaded.iCpuLimit == 4
    assert configLoaded.fMemoryLimitGigabytes == 8.5


# -----------------------------------------------------------------------
# /api/setup/save — the wizard writer
# -----------------------------------------------------------------------


@pytest.fixture
def fixtureSetupClient(tmp_path, monkeypatch):
    """Authenticated setup-wizard client whose home is tmp_path/home."""
    os.makedirs(str(tmp_path / "home"), exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app = fappCreateSetupApplication()
    return TestClient(
        app, headers={"x-session-token": app.state.sSessionToken},
    )


@pytest.mark.falsification
def test_setup_save_refuses_a_config_it_cannot_validate(
    fixtureSetupClient, tmp_path,
):
    """A string where a number belongs never reaches vaibify.yml.

    ``ProjectConfig`` is a plain dataclass with no coercion, so
    ``{"cpuLimit": "8"}`` would persist verbatim and only surface much
    later as a broken ``docker run`` argument.

    Kills: removing the ``fbValidateConfig`` rejection from
    setupServer.fnWriteConfigToDirectory returns 200 and leaves a
    vaibify.yml on disk carrying the string.
    """
    sProjectDirectory = str(tmp_path / "home" / "project")
    responseHttp = fixtureSetupClient.post(
        "/api/setup/save",
        json={
            "sProjectDirectory": sProjectDirectory,
            "dictConfig": {"projectName": "wizard", "cpuLimit": "8"},
        },
    )
    assert responseHttp.status_code == 400
    assert not os.path.exists(
        os.path.join(sProjectDirectory, "vaibify.yml")
    )


def test_setup_save_writes_a_valid_config(
    fixtureSetupClient, tmp_path,
):
    """Positive control: a valid wizard config is written and reloads."""
    sProjectDirectory = str(tmp_path / "home" / "valid")
    responseHttp = fixtureSetupClient.post(
        "/api/setup/save",
        json={
            "sProjectDirectory": sProjectDirectory,
            "dictConfig": {"projectName": "wizard", "cpuLimit": 8},
        },
    )
    assert responseHttp.status_code == 200
    configLoaded = fconfigLoadFromFile(
        os.path.join(sProjectDirectory, "vaibify.yml")
    )
    assert configLoaded.iCpuLimit == 8


def test_write_config_to_directory_rejects_invalid_dict(tmp_path):
    """The writer itself refuses, so no caller can bypass validation."""
    sProjectDirectory = str(tmp_path / "direct")
    with pytest.raises(HTTPException) as excinfo:
        fnWriteConfigToDirectory(sProjectDirectory, {"projectName": ""})
    assert excinfo.value.status_code == 400
    assert not os.path.exists(
        os.path.join(sProjectDirectory, "vaibify.yml")
    )


# -----------------------------------------------------------------------
# /api/setup/save — the host-directory jail
# -----------------------------------------------------------------------


@pytest.mark.falsification
def test_setup_save_rejects_a_sibling_of_the_home_directory(
    fixtureSetupClient, tmp_path,
):
    """``/…/homeBackup`` is not inside ``/…/home``.

    A bare prefix test admits every sibling whose name extends the
    home path, which is how a wizard save escapes the jail without a
    single ``..`` in the request.

    Kills: replacing the separator-anchored comparison in
    setupServer._fnValidateProjectDirectory with a bare
    ``startswith(sHome)`` admits the sibling and returns 200.
    """
    sProjectDirectory = str(tmp_path / "homeBackup")
    responseHttp = fixtureSetupClient.post(
        "/api/setup/save",
        json={
            "sProjectDirectory": sProjectDirectory,
            "dictConfig": {"projectName": "escape"},
        },
    )
    assert responseHttp.status_code == 403
    assert not os.path.exists(sProjectDirectory)


@pytest.mark.falsification
def test_setup_save_rejects_a_symlink_that_leaves_home(
    fixtureSetupClient, tmp_path,
):
    """A symlink under home that points outside it is still outside it.

    Kills: resolving the requested directory with ``os.path.abspath``
    instead of ``os.path.realpath`` in
    setupServer._fnValidateProjectDirectory lets the symlinked path
    through and writes outside the home tree.
    """
    pathOutside = tmp_path / "outside"
    os.makedirs(str(pathOutside), exist_ok=True)
    pathLink = tmp_path / "home" / "escape"
    os.symlink(str(pathOutside), str(pathLink))
    responseHttp = fixtureSetupClient.post(
        "/api/setup/save",
        json={
            "sProjectDirectory": str(pathLink),
            "dictConfig": {"projectName": "escape"},
        },
    )
    assert responseHttp.status_code == 403
    assert not os.path.exists(
        os.path.join(str(pathOutside), "vaibify.yml")
    )
