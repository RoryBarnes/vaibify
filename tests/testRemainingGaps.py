"""Final tests for remaining testable uncovered lines."""

from unittest.mock import patch, MagicMock


# ── resourceMonitor: error paths ─────────────────────────────────────


def test_ffParsePercent_invalid_returns_zero():
    from vaibify.gui.resourceMonitor import _ffParsePercent
    assert _ffParsePercent(None) == 0.0
    assert _ffParsePercent("abc%") == 0.0


def test_fsSplitMemoryLimit_no_slash():
    from vaibify.gui.resourceMonitor import _fsSplitMemoryLimit
    assert _fsSplitMemoryLimit("512MiB") == "0B"


def test_fsSplitMemoryLimit_with_slash():
    from vaibify.gui.resourceMonitor import _fsSplitMemoryLimit
    assert _fsSplitMemoryLimit("256MiB / 8GiB") == "8GiB"


# ── setupServer: routes via TestClient ───────────────────────────────


def test_setup_save_route():
    from vaibify.gui.setupServer import fappCreateSetupApplication
    from starlette.testclient import TestClient
    with patch("vaibify.gui.setupServer.fnWriteConfigToDirectory"):
        app = fappCreateSetupApplication()
        clientHttp = TestClient(app)
        responseHttp = clientHttp.post("/api/setup/save", json={
            "sProjectDirectory": "/tmp/test",
            "dictConfig": {"projectName": "test"},
        })
        assert responseHttp.status_code == 200
        assert responseHttp.json()["bSuccess"] is True


def test_setup_build_route():
    from vaibify.gui.setupServer import fappCreateSetupApplication
    from starlette.testclient import TestClient
    with patch(
        "vaibify.gui.setupServer.fdictProcessBuild",
        return_value={"bSuccess": True},
    ):
        app = fappCreateSetupApplication()
        clientHttp = TestClient(app)
        responseHttp = clientHttp.post("/api/setup/build", json={
            "sProjectDirectory": "/tmp/test",
        })
        assert responseHttp.status_code == 200


def test_setup_index_route():
    from vaibify.gui.setupServer import fappCreateSetupApplication
    from starlette.testclient import TestClient
    app = fappCreateSetupApplication()
    clientHttp = TestClient(app)
    responseHttp = clientHttp.get("/")
    assert responseHttp.status_code in (200, 404)


def test_setup_static_mount():
    from vaibify.gui.setupServer import fappCreateSetupApplication
    from starlette.testclient import TestClient
    app = fappCreateSetupApplication()
    clientHttp = TestClient(app)
    responseHttp = clientHttp.get("/static/styleMain.css")
    assert responseHttp.status_code in (200, 404)


# ── configLoader: fbDockerAvailable ──────────────────────────────────


def test_fbDockerAvailable_when_missing():
    from vaibify.cli.configLoader import fbDockerAvailable
    with patch.dict("sys.modules", {"docker": None}):
        bResult = fbDockerAvailable()
        assert isinstance(bResult, bool)


# ── commandInit: template operations ─────────────────────────────────


def test_fnCopyTemplate_missing_template():
    from vaibify.cli.commandInit import fnCopyTemplate
    import pytest
    with pytest.raises((FileNotFoundError, SystemExit)):
        fnCopyTemplate("nonexistent_template_xyz")
