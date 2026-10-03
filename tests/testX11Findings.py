"""X11 problems reach the dashboard and `vaibify doctor`, with one fix each.

The container's DISPLAY and socket mount are fixed when it is created,
so a container made before ``x11Forwarding`` was turned on (or after it
was turned off) disagrees with vaibify.yml and every graphical program
in it fails with an empty DISPLAY. These compare what ``docker inspect``
reports with what the project asks for, in both directions, and drive
the readiness payload with a container id that differs from its name.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vaibify.cli import doctorX11Checks
from vaibify.cli.preflightResult import (
    S_LEVEL_INFO, S_LEVEL_NOT_CHECKED, S_LEVEL_OK, S_LEVEL_WARN,
)
from vaibify.docker import x11Forwarding
from vaibify.docker.x11Forwarding import (
    fdictAssessContainerX11, ftDescribeX11Findings,
)
from vaibify.gui.routes import systemRoutes

S_CONTAINER_ID = "0123456789abcdef"
S_CONTAINER_NAME = "namedProject"
DICT_SOCKET_MOUNT = {"Destination": "/tmp/.X11-unix", "Type": "bind"}


def _fjsonInspect(listEnvironment, listMounts=()):
    return {
        "Config": {"Env": list(listEnvironment)}, "Mounts": list(listMounts),
    }


JSON_LINUX_FORWARDED = _fjsonInspect(["DISPLAY=:0"], [DICT_SOCKET_MOUNT])
JSON_MAC_FORWARDED = _fjsonInspect(["DISPLAY=host.docker.internal:0"])
JSON_NOT_FORWARDED = _fjsonInspect(["PATH=/usr/bin"])


@pytest.mark.falsification
def testAContainerCreatedWithoutX11IsReportedWhenTheProjectWantsIt():
    """Kills: staying silent about a container with no DISPLAY."""
    dictAssessment = fdictAssessContainerX11(True, JSON_NOT_FORWARDED)
    assert dictAssessment["bReady"] is False
    assert dictAssessment["sState"] == "created-without-x11"
    assert dictAssessment["sCommand"] == "vaibify stop && vaibify start"
    assert "created without X11 forwarding" in dictAssessment["sMessage"]


@pytest.mark.falsification
def testAContainerStillCarryingX11IsReportedWhenTheProjectTurnedItOff():
    """Kills: leaving a display channel in place after the opt-in is withdrawn."""
    for jsonForwarded in (JSON_LINUX_FORWARDED, JSON_MAC_FORWARDED):
        dictAssessment = fdictAssessContainerX11(False, jsonForwarded)
        assert dictAssessment["sState"] == "created-with-x11"
        assert "remove it" in dictAssessment["sFix"]


@pytest.mark.parametrize("bRequested, jsonInspect", [
    (True, JSON_LINUX_FORWARDED), (True, JSON_MAC_FORWARDED),
    (False, JSON_NOT_FORWARDED),
])
def testAContainerThatMatchesTheSettingIsReady(bRequested, jsonInspect):
    assert fdictAssessContainerX11(bRequested, jsonInspect)["bReady"] is True


def testADisplayVariableAloneIsNotAForwardedDisplay():
    """An image that sets DISPLAY itself must not read as forwarding."""
    jsonImageDisplay = _fjsonInspect(["DISPLAY=:99"])
    assert fdictAssessContainerX11(False, jsonImageDisplay)["bReady"] is True
    assert fdictAssessContainerX11(True, jsonImageDisplay)["sState"] == (
        "created-without-x11")


def testAnUnansweredInspectClaimsNothing():
    dictAssessment = fdictAssessContainerX11(True, {})
    assert dictAssessment["sState"] == "not-checked"
    assert ftDescribeX11Findings(True, {})[0] == []


def testHostProblemsAreOnlyReportedWhenTheProjectWantsX11(monkeypatch):
    monkeypatch.setattr(
        x11Forwarding, "fdictAssessHostX11",
        lambda: x11Forwarding._fdictAssessment(
            "no-server", "No X server is installed on this Mac.", "Install."))
    assert ftDescribeX11Findings(False, JSON_NOT_FORWARDED) == ([], [])
    listContainer, listHost = ftDescribeX11Findings(True, JSON_NOT_FORWARDED)
    assert len(listContainer) == 1 and "vaibify stop" in listContainer[0]
    assert listHost == ["No X server is installed on this Mac. Install."]


def _fdictSettledReadiness():
    return {
        "bReady": True, "sStatus": "ok", "sReason": "",
        "saWarnings": [], "iWarningCount": 0,
    }


@pytest.mark.falsification
def testTheReadinessPayloadCarriesTheX11FindingsToTheDashboard():
    """Kills: leaving X11 problems on stderr instead of the dashboard payload."""
    with patch.object(
        systemRoutes, "_fdictProbeContainerReadiness",
        return_value=_fdictSettledReadiness(),
    ), patch.object(
        systemRoutes, "_flistDescribeUnresolvableSecrets", return_value=[],
    ), patch.object(
        systemRoutes, "_flistDescribeConfigurationDrift", return_value=[],
    ), patch.object(
        systemRoutes, "_ftDescribeX11Findings",
        return_value=(["container line"], ["host line"]),
    ):
        dictReadiness = systemRoutes._fdictReadinessWithSecretWarnings(
            None, S_CONTAINER_ID)
    assert dictReadiness["listConfigurationDrift"] == ["container line"]
    assert dictReadiness["saWarnings"] == ["host line"]
    assert dictReadiness["iWarningCount"] == 1


def test_the_x11_lookup_resolves_the_container_id_to_a_name_then_inspects():
    """The registry is keyed by NAME and the route is handed an ID."""
    dictSeen = {}

    def fdictLookUp(sKey):
        dictSeen["sRegistryKey"] = sKey
        return {"sConfigPath": "/unused/vaibify.yml"}

    def fjsonRecordInspect(sIdentifier):
        dictSeen["sInspected"] = sIdentifier
        return JSON_NOT_FORWARDED

    with patch(
        "vaibify.gui.pipelineServer.fsContainerNameForId",
        return_value=S_CONTAINER_NAME,
    ), patch(
        "vaibify.config.registryManager.fdictGetProject",
        side_effect=fdictLookUp,
    ), patch(
        "vaibify.cli.configLoader.fconfigLoadFromPath",
        return_value=SimpleNamespace(bX11Forwarding=True),
    ), patch(
        "vaibify.docker.containerManager.fjsonInspectContainer",
        side_effect=fjsonRecordInspect,
    ), patch.object(
        x11Forwarding, "fdictAssessHostX11",
        return_value=x11Forwarding._fdictAssessment("ready", "", ""),
    ):
        listContainer, listHost = systemRoutes._ftDescribeX11Findings(
            None, S_CONTAINER_ID)
    assert dictSeen["sRegistryKey"] == S_CONTAINER_NAME
    assert dictSeen["sInspected"] == S_CONTAINER_ID
    assert len(listContainer) == 1 and listHost == []


def test_an_unreadable_project_gives_no_x11_lines_rather_than_a_guess():
    with patch(
        "vaibify.gui.pipelineServer.fsContainerNameForId",
        side_effect=RuntimeError("daemon went away"),
    ):
        assert systemRoutes._ftDescribeX11Findings(
            None, S_CONTAINER_ID) == ([], [])


def test_doctor_says_x11_is_off_for_a_project_that_did_not_opt_in():
    listResults = doctorX11Checks.flistCheckX11Host(
        SimpleNamespace(bX11Forwarding=False))
    assert listResults[0].sLevel == S_LEVEL_INFO
    assert "x11Forwarding: true" in listResults[0].sMessage


def test_doctor_names_the_host_cause_and_fix_when_no_server_exists(
    monkeypatch,
):
    monkeypatch.setattr(
        doctorX11Checks, "fdictAssessHostX11",
        lambda: x11Forwarding.fdictAssessMacX11(None, 6000))
    resultFound = doctorX11Checks.flistCheckX11Host(
        SimpleNamespace(bX11Forwarding=True))[0]
    assert resultFound.sLevel == S_LEVEL_WARN
    assert "No X server" in resultFound.sMessage
    assert "xquartz.org" in resultFound.sRemediation


@pytest.mark.falsification
def testDoctorTellsYouToRecreateAContainerMadeWithoutX11():
    """Kills: a doctor that passes a container with an empty DISPLAY."""
    resultFound = doctorX11Checks.flistCheckX11Container(
        SimpleNamespace(bX11Forwarding=True), JSON_NOT_FORWARDED)[0]
    assert resultFound.sLevel == S_LEVEL_WARN
    assert "created without X11 forwarding" in resultFound.sMessage
    assert resultFound.sCommand == "vaibify stop && vaibify start"


def test_doctor_passes_a_matching_container_and_declines_to_guess():
    config = SimpleNamespace(bX11Forwarding=True)
    assert doctorX11Checks.flistCheckX11Container(
        config, JSON_LINUX_FORWARDED)[0].sLevel == S_LEVEL_OK
    assert doctorX11Checks.flistCheckX11Container(
        config, {})[0].sLevel == S_LEVEL_NOT_CHECKED
