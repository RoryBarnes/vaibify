"""The Docker context vaibify exported as DOCKER_HOST can still be re-read.

vaibify seeds ``DOCKER_HOST`` from the active Docker context. A later
attempt (the dashboard's Retry after the researcher runs ``docker context
use``) asks the docker CLI which context is active, and with
``DOCKER_HOST`` set the real CLI answers with a synthetic ``default``
context whose endpoint IS that variable: the value vaibify had just
written. The earlier tests stubbed the read itself, which hid this. These
run the real subprocess boundary against a stand-in ``docker`` that
behaves that way.
"""

import os
import stat

import pytest

from vaibify.docker import dockerConnection, dockerContext

S_FAKE_DOCKER_SCRIPT = """#!/bin/sh
sState="$FAKE_DOCKER_STATE"
sCurrent=$(cat "$sState/current")
case "$1 $2" in
  "context show")
    if [ -n "$DOCKER_HOST" ]; then echo default; else echo "$sCurrent"; fi ;;
  "context inspect")
    if [ -n "$DOCKER_HOST" ]; then echo "$DOCKER_HOST"; \
else cat "$sState/$sCurrent.endpoint"; fi ;;
  *) exit 1 ;;
esac
"""

S_ENDPOINT_FIRST = "unix:///home/researcher/.alpha/docker.sock"
S_ENDPOINT_SECOND = "unix:///home/researcher/.beta/docker.sock"
S_ENDPOINT_OWN = "tcp://build-host.internal:2376"


class FakeDockerCli:
    """A stand-in ``docker`` whose active context a test can switch."""

    def __init__(self, pathState):
        self.pathState = pathState

    def fnDefineContext(self, sName, sEndpoint):
        (self.pathState / f"{sName}.endpoint").write_text(sEndpoint + "\n")

    def fnUseContext(self, sName):
        (self.pathState / "current").write_text(sName + "\n")


@pytest.fixture
def fakeDockerCli(tmp_path, monkeypatch):
    pathBin = tmp_path / "bin"
    pathState = tmp_path / "state"
    pathBin.mkdir()
    pathState.mkdir()
    pathScript = pathBin / "docker"
    pathScript.write_text(S_FAKE_DOCKER_SCRIPT)
    pathScript.chmod(pathScript.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{pathBin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(pathState))
    monkeypatch.setenv("DOCKER_HOST", "placeholder")
    monkeypatch.delenv("DOCKER_HOST")
    monkeypatch.setattr(dockerContext, "_sDockerHostExportedByVaibify", None)
    fake = FakeDockerCli(pathState)
    fake.fnDefineContext("alpha", S_ENDPOINT_FIRST)
    fake.fnDefineContext("beta", S_ENDPOINT_SECOND)
    fake.fnUseContext("alpha")
    return fake


@pytest.mark.falsification
def testARetryAfterAContextSwitchSeesTheNewEndpoint(fakeDockerCli):
    """Kills: asking the CLI with vaibify's own export still in the environment."""
    dockerConnection._fnEnsureDockerHost()
    assert os.environ["DOCKER_HOST"] == S_ENDPOINT_FIRST
    fakeDockerCli.fnUseContext("beta")
    dockerConnection._fnEnsureDockerHost()
    assert os.environ["DOCKER_HOST"] == S_ENDPOINT_SECOND


def testTheContextNameSurvivesVaibifysOwnExport(fakeDockerCli):
    fakeDockerCli.fnDefineContext("colima", "unix:///tmp/elsewhere.sock")
    fakeDockerCli.fnUseContext("colima")
    dockerConnection._fnEnsureDockerHost()
    assert os.environ["DOCKER_HOST"] == "unix:///tmp/elsewhere.sock"
    assert dockerContext.fsActiveDockerContext() == "colima"
    assert dockerContext.fbColimaActive() is True


def testAResearchersOwnDockerHostIsNeitherOverwrittenNorMasked(
    fakeDockerCli, monkeypatch,
):
    monkeypatch.setenv("DOCKER_HOST", S_ENDPOINT_OWN)
    dockerConnection._fnEnsureDockerHost()
    assert os.environ["DOCKER_HOST"] == S_ENDPOINT_OWN
    assert dockerContext.fbDockerHostIsExportedByVaibify() is False
    assert dockerContext.fsActiveDockerContext() == "default"


@pytest.mark.falsification
def testTheReportedEndpointNamesItsRealSource(fakeDockerCli, monkeypatch):
    """Kills: calling vaibify's own export 'from DOCKER_HOST' in the report."""
    dockerConnection._fnEnsureDockerHost()
    assert dockerContext.fsResolveDockerEndpoint() == (
        S_ENDPOINT_FIRST + " (from the active Docker context)")
    monkeypatch.setenv("DOCKER_HOST", S_ENDPOINT_OWN)
    assert dockerContext.fsResolveDockerEndpoint() == (
        S_ENDPOINT_OWN + " (from DOCKER_HOST)")


def testOnlyTheExactValueVaibifyWroteCountsAsItsOwn(fakeDockerCli, monkeypatch):
    dockerConnection._fnEnsureDockerHost()
    assert dockerContext.fbDockerHostIsExportedByVaibify() is True
    monkeypatch.setenv("DOCKER_HOST", S_ENDPOINT_FIRST + "-changed")
    assert dockerContext.fbDockerHostIsExportedByVaibify() is False
    monkeypatch.delenv("DOCKER_HOST")
    assert dockerContext.fbDockerHostIsExportedByVaibify() is False
