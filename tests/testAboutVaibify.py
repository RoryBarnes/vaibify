"""GET /api/system/about says which vaibify is running, and names no path.

Source: ``vaibify/gui/routes/systemRoutes.py`` (the route) and
``vaibify/versionIdentity.py`` (``fdictDescribeForAbout``).

The hubs' help windows show it, and help windows end up in screenshots,
so the answer carries the version, the commit, whether the code is a
source checkout or an installed package, and the Python version, and no
filesystem path at all.
"""

import os
import platform

from fastapi import FastAPI
from fastapi.testclient import TestClient

import vaibify
from vaibify.gui.routes import systemRoutes


def _fclientAbout():
    app = FastAPI()
    systemRoutes._fnRegisterAbout(app)
    return TestClient(app)


def testTheAnswerNamesTheRunningCodeAndItsPython():
    dictAbout = _fclientAbout().get("/api/system/about").json()
    assert set(dictAbout) == {
        "sVersion", "sCommit", "sSource", "bModified", "sPythonVersion"}
    assert dictAbout["sSource"] in ("source checkout", "installed package")
    assert dictAbout["sPythonVersion"] == platform.python_version()
    assert dictAbout["sVersion"]


def testTheAnswerNamesNoPath():
    dictAbout = _fclientAbout().get("/api/system/about").json()
    sPackageDirectory = os.path.dirname(os.path.realpath(vaibify.__file__))
    for jsonValue in dictAbout.values():
        sValue = str(jsonValue)
        assert os.sep not in sValue, sValue
        assert os.path.expanduser("~") not in sValue
        assert sPackageDirectory not in sValue


def testTheAgentLaneIsRefused():
    responseHttp = _fclientAbout().get(
        "/api/system/about", headers={"X-Vaibify-Session": "agent-token"})
    assert responseHttp.status_code == 403
