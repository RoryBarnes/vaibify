"""A CLI refusal names its cause, and only a way out that exists.

Source: ``vaibify/cli/packageShadowGuard.py`` and ``vaibify/cli/main.py``
(a shadowed import), ``vaibify/cli/hubSession.py`` and
``vaibify/cli/actionCommands.py`` (a claim refused while a dashboard
holds the container).

Both were met by a researcher on 2026-11-02. Run from the directory
that holds a checkout, with an empty entry in PYTHONPATH, ``vaibify``
imported the checkout folder as a namespace package and the CLI died
with a ``TypeError`` about ``None``. And a researcher-only action,
refused because a dashboard tab held the container, was told to use
``vaibify-do``, which refuses researcher-only actions too.
"""

import os
import pathlib
import subprocess
import sys
import types

import pytest

from vaibify.cli import actionCommands, hubSession, packageShadowGuard
from vaibify.gui.actionCatalog import LIST_AGENT_ACTIONS

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parents[1]


def _fmoduleShadow(sFolder):
    """A ``vaibify`` the way a shadowing folder imports it: a path, no file."""
    moduleShadow = types.ModuleType("vaibify")
    moduleShadow.__path__ = [sFolder]
    return moduleShadow


@pytest.mark.falsification
def testAShadowedPackageStopsNamingTheFolderAndTheFix(tmp_path, monkeypatch, capsys):
    """Exit status 2, and one sentence with the folder, the directory and the fix.

    Kills: the check returning before it looks at the package.
    """
    sFolder = str(tmp_path / "vaibify")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", os.pathsep + "/somewhere/else")
    with pytest.raises(SystemExit) as excInfo:
        packageShadowGuard.fnRefuseNamespaceShadowedPackage(_fmoduleShadow(sFolder))
    assert excInfo.value.code == 2
    sError = capsys.readouterr().err
    assert sFolder in sError and os.getcwd() in sError
    assert "empty entry is the current directory" in sError
    assert "run vaibify from another directory" in sError
    assert sError.count("\n") == 1


def testTheInstalledPackagePassesTheCheck():
    import vaibify
    assert packageShadowGuard.fnRefuseNamespaceShadowedPackage(vaibify) is None


def _ftRunInDirectory(sDirectory, sProgram, sHome):
    dictEnvironment = dict(os.environ, HOME=sHome)
    dictEnvironment.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-c", sProgram], cwd=sDirectory, env=dictEnvironment,
        capture_output=True, text=True, timeout=180,
    )


def testTheCliRefusesFromADirectoryThatShadowsThePackage(tmp_path):
    """Run the real entry point beside an empty ``vaibify/`` folder.

    Whether the folder shadows the package depends on how vaibify is
    installed: an editable install's finder runs after ``sys.path`` is
    scanned, so the folder wins; a package on ``sys.path`` wins over
    it. Both branches are asserted. The checkout's own package is
    appended to the import path so that its CLI, not an older
    installed copy, is the one that runs.
    """
    (tmp_path / "vaibify").mkdir()
    sHome = str(tmp_path / "home")
    os.makedirs(sHome)
    resultProbe = _ftRunInDirectory(str(tmp_path), (
        "import vaibify; print(getattr(vaibify, '__file__', None) is None)"
    ), sHome)
    bShadowed = resultProbe.stdout.strip() == "True"
    resultRun = _ftRunInDirectory(str(tmp_path), (
        "import sys, vaibify; "
        f"vaibify.__path__.append({str(PATH_REPOSITORY / 'vaibify')!r}); "
        "sys.argv = ['vaibify', '--help']; "
        "from vaibify.cli.main import main; main()"
    ), sHome)
    if bShadowed:
        assert resultRun.returncode == 2, resultRun.stderr
        assert "imported the folder" in resultRun.stderr
        assert str(tmp_path / "vaibify") in resultRun.stderr
        assert "Traceback" not in resultRun.stderr
    else:
        assert resultRun.returncode == 0, resultRun.stderr
        assert "Usage" in resultRun.stdout


_DICT_IN_USE = {"detail": {"sMessage": "a dashboard tab holds it"}}


def testAnActionTheAgentLaneOffersNamesThatLane():
    sMessage = hubSession.fsExplainClaimConflict("projectAlpha", _DICT_IN_USE, True)
    assert "a dashboard tab holds it" in sMessage
    assert "vaibify-do" in sMessage


@pytest.mark.falsification
def testAResearcherOnlyActionNeverNamesTheAgentLane():
    """The way out named for a researcher-only action is one that exists.

    Kills: appending the agent-lane pointer whatever the action.
    """
    sMessage = hubSession.fsExplainClaimConflict("projectAlpha", _DICT_IN_USE, False)
    assert "vaibify-do" not in sMessage
    assert "only you can run" in sMessage
    assert "Release the container from the dashboard's picker" in sMessage


def _fdictCatalogEntry(sName):
    return next(dictEntry for dictEntry in LIST_AGENT_ACTIONS if dictEntry["sName"] == sName)


@pytest.mark.parametrize("sAction", ["confirm-remote-binding", "decide-token-rotation"])
def testAGeneratedResearcherOnlyCommandClaimsAsResearcherOnly(monkeypatch, sAction):
    dictEntry = _fdictCatalogEntry(sAction)
    assert dictEntry["bAgentSafe"] is False
    listClaims = []

    def fdictRecordTheClaim(sContainerName, iPort, sWorkflowPath, bAgentLaneCanRunIt=False):
        listClaims.append(bAgentLaneCanRunIt)
        raise hubSession.HubSessionError("held")

    monkeypatch.setattr(hubSession, "fdictOpenResearcherSession", fdictRecordTheClaim)
    with pytest.raises(hubSession.HubSessionError):
        actionCommands._fnDispatchUnderLease(dictEntry, "projectAlpha", {
            "iPort": None, "sWorkflowPath": None,
        })
    assert listClaims == [False]


def testAGeneratedAgentSafeCommandClaimsAsAgentSafe(monkeypatch):
    dictEntry = next(dictEntry for dictEntry in LIST_AGENT_ACTIONS if dictEntry.get("bAgentSafe"))
    listClaims = []

    def fdictRecordTheClaim(sContainerName, iPort, sWorkflowPath, bAgentLaneCanRunIt=False):
        listClaims.append(bAgentLaneCanRunIt)
        raise hubSession.HubSessionError("held")

    monkeypatch.setattr(hubSession, "fdictOpenResearcherSession", fdictRecordTheClaim)
    with pytest.raises(hubSession.HubSessionError):
        actionCommands._fnDispatchUnderLease(dictEntry, "projectAlpha", {
            "iPort": None, "sWorkflowPath": None,
        })
    assert listClaims == [True]
