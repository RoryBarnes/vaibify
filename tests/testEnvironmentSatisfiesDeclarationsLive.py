"""The build's last check runs in the environment image with its start bypassed.

A vaibify container's start installs the listed repositories, so a
package that only the start provides is present in every running
container and absent for the Level 3 shadow and ``reproduce.sh``, which
run the image with the entrypoint overridden. The check after the build
must see what they see. The stand-in image here has an entrypoint that
installs the one declared package, from a wheel it carries, before
running its command: a check that let the entrypoint run would report
it satisfied.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``).
"""

import os
import shutil
import subprocess
import uuid

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.docker import imageBuilder
from vaibify.docker.dockerErrorDiagnosis import fsExplainBuildFailure
from vaibify.resources import fpathContainerImageRoot

pytestmark = pytest.mark.docker_live

S_STAND_IN = (
    "FROM python:3.12-slim\n"
    "RUN pip install --no-cache-dir packaging \\\n"
    "    && pip download --no-cache-dir -d /wheels six==1.16.0 \\\n"
    "    && mkdir -p /etc/vaibify/projectRequirements \\\n"
    "    && printf '{sDeclared}\\n' > /etc/vaibify/projectRequirements/alpha.txt \\\n"
    "    && printf '#!/bin/sh\\npip install -q --no-index --find-links /wheels "
    "six==1.16.0 >/dev/null 2>&1\\nexec \"$@\"\\n' > /start.sh \\\n"
    "    && chmod +x /start.sh\n"
    "COPY checkDeclaredRequirements.py "
    "/usr/local/lib/vaibify/checkDeclaredRequirements.py\n"
    "ENTRYPOINT [\"/start.sh\"]\n"
)


@pytest.fixture
def fsBuildStandIn(tmp_path):
    """Yield a function that builds the stand-in declaring one requirement."""
    fnRequireDaemonReachable()
    listTags = []

    def fsBuild(sDeclared):
        sTag = f"satisfiescheck{uuid.uuid4().hex[:8]}:env"
        pathContext = tmp_path / sTag.replace(":", "-")
        pathContext.mkdir()
        (pathContext / "Dockerfile").write_text(
            S_STAND_IN.replace("{sDeclared}", sDeclared), encoding="utf-8")
        shutil.copy(os.path.join(str(fpathContainerImageRoot()),
                                 "checkDeclaredRequirements.py"), pathContext)
        subprocess.run(["docker", "build", "-q", "-t", sTag, str(pathContext)],
                       check=True, capture_output=True, timeout=900)
        listTags.append(sTag)
        return sTag
    yield fsBuild
    for sTag in listTags:
        subprocess.run(["docker", "rmi", "-f", sTag], capture_output=True)


@pytest.mark.falsification
def testAPackageOnlyTheStartInstallsFailsTheBuild(fsBuildStandIn):
    """Kills: checking in a container whose entrypoint ran.

    Mutation: the check's ``--entrypoint ""`` override is dropped, so
    the stand-in's start installs six before the checker looks.
    """
    sTag = fsBuildStandIn("six==1.16.0")
    with pytest.raises(RuntimeError) as errorInfo:
        imageBuilder.fnRequireEnvironmentSatisfiesDeclarations(sTag)
    sSentence = fsExplainBuildFailure(
        "proj", str(errorInfo.value), errorInfo.value.sStderrTail)
    assert "'six==1.16.0' (project alpha) has none" in sSentence
    assert "the container still has the previous image" in sSentence


def testADeclarationTheImageSatisfiesPasses(fsBuildStandIn):
    sTag = fsBuildStandIn("packaging>=20")
    imageBuilder.fnRequireEnvironmentSatisfiesDeclarations(sTag)
