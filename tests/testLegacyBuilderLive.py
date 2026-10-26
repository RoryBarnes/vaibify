"""What Docker's legacy builder does with BuildKit syntax, against a daemon.

``buildKitRequirement`` refuses a build on a machine without buildx for
exactly the syntax these builds show the legacy builder refusing or
building wrongly, and passes everything it builds correctly. Each rule
is measured here rather than trusted: if Docker changes either way,
this file fails and the rule is re-measured. Every build forces the
legacy builder with ``DOCKER_BUILDKIT=0``, so the answer does not
depend on whether the machine has buildx. No container is created; the
images are removed after each build.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one. The builds pull one small pinned Alpine image.
"""

import os
import subprocess
import uuid

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.docker.buildKitRequirement import flistDescribeBuildKitSyntax

pytestmark = pytest.mark.docker_live

S_BASE_IMAGE = (
    "alpine:3.20@sha256:"
    "d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc")
S_FROM = f"FROM {S_BASE_IMAGE}\n"
S_ARM_ON_INTEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "containerImage", "Dockerfile.claudeArmOnIntel")
I_BUILD_TIMEOUT_SECONDS = 600


def _ftBuildWithTheLegacyBuilder(pathContext, sDockerfileText):
    """Return ``(iExitCode, sOutput)`` of a legacy build of this text."""
    pathContext.mkdir(parents=True, exist_ok=True)
    (pathContext / "Dockerfile").write_text(sDockerfileText, encoding="utf-8")
    (pathContext / "f").write_text("x\n", encoding="utf-8")
    sTag = "vaibify-legacy-builder-probe:" + uuid.uuid4().hex[:12]
    dictEnvironment = dict(os.environ, DOCKER_BUILDKIT="0")
    try:
        processBuild = subprocess.run(
            ["docker", "build", "--no-cache", "-t", sTag, str(pathContext)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=dictEnvironment,
            timeout=I_BUILD_TIMEOUT_SECONDS)
    finally:
        subprocess.run(["docker", "rmi", "-f", sTag], capture_output=True,
                       timeout=I_BUILD_TIMEOUT_SECONDS)
    return processBuild.returncode, processBuild.stdout + processBuild.stderr


@pytest.fixture(scope="module", autouse=True)
def fnRequireADaemon():
    fnRequireDaemonReachable()


@pytest.mark.parametrize("sBody, sDockerSays", [
    ("FROM --platform=$BUILDPLATFORM " + S_BASE_IMAGE + "\n",
     'failed to parse platform : ""'),
    ("FROM --platform=$TARGETPLATFORM " + S_BASE_IMAGE + "\n",
     'failed to parse platform : ""'),
    (S_FROM + "RUN --mount=type=cache,target=/c true\n", "requires BuildKit"),
    (S_FROM + "RUN --network=none true\n", "requires BuildKit"),
    (S_FROM + "COPY --chmod=755 f /f\n", "requires BuildKit"),
    (S_FROM + "COPY --parents f /\n", "unknown flag: --parents"),
])
def testTheLegacyBuilderRefusesWhatTheScannerRefuses(
        tmp_path, sBody, sDockerSays):
    iExit, sOutput = _ftBuildWithTheLegacyBuilder(tmp_path, sBody)
    assert iExit != 0, sOutput
    assert sDockerSays in sOutput, sOutput
    assert flistDescribeBuildKitSyntax(sBody)


@pytest.mark.parametrize("sBody", [
    S_FROM + "RUN <<EOF\necho hi > /out\nEOF\nRUN test -f /out\n",
    S_FROM + 'ARG TARGETARCH\nRUN test -n "$TARGETARCH"\n',
])
def testTheLegacyBuilderSilentlyDropsWhatTheScannerRefuses(tmp_path, sBody):
    """The step after the BuildKit syntax checks what it should have done.

    A heredoc's body never runs and ``$TARGETARCH`` is empty, so the
    check fails; with BuildKit both would pass.
    """
    iExit, sOutput = _ftBuildWithTheLegacyBuilder(tmp_path, sBody)
    assert iExit != 0, sOutput
    assert "returned a non-zero code" in sOutput, sOutput
    assert flistDescribeBuildKitSyntax(sBody)


def testTheLegacyBuilderIgnoresAWrongChecksum(tmp_path):
    """An integrity check that is silently skipped is refused, not trusted."""
    sBody = S_FROM + "ADD --checksum=sha256:" + "0" * 64 + " f /f\n"
    iExit, sOutput = _ftBuildWithTheLegacyBuilder(tmp_path, sBody)
    assert iExit == 0, sOutput
    assert flistDescribeBuildKitSyntax(sBody)


def testTheLegacyBuilderBuildsWhatTheScannerPasses(tmp_path):
    sBody = S_FROM + "COPY --link f /f\nCOPY --chown=1000:1000 f /g\nRUN true\n"
    iExit, sOutput = _ftBuildWithTheLegacyBuilder(tmp_path, sBody)
    assert iExit == 0, sOutput
    assert flistDescribeBuildKitSyntax(sBody) == []


def testTheArmOnIntelOverlayFailsOnTheLegacyBuilderAtItsFirstFrom(tmp_path):
    """The real shipped Dockerfile, not a fixture modelled on it.

    Its first steps are global ARGs; the build stops at the FROM that
    reads ``$BUILDPLATFORM``, before anything runs.
    """
    with open(S_ARM_ON_INTEL, encoding="utf-8") as fileDockerfile:
        sBody = fileDockerfile.read()
    iExit, sOutput = _ftBuildWithTheLegacyBuilder(tmp_path, sBody)
    assert iExit != 0, sOutput
    assert 'failed to parse platform : ""' in sOutput, sOutput
    listSteps = [sLine for sLine in sOutput.splitlines()
                 if sLine.startswith("Step ")]
    assert "FROM --platform=$BUILDPLATFORM" in listSteps[-1], sOutput
