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
    fnRegisterRegistryRoutes(app, {"require": lambda *aArgs: None, "docker": None})
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
