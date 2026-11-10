"""The projects' stage, built by the real builder on a real daemon.

``tests/testDockerfileHardening.py`` holds the stage's text to one joint
install. Only pip can say what that install does: that every project's
declared version is the one that ends up installed, that two projects
asking for incompatible versions stop the build, and that the sentence
the researcher reads then names which project asked for which side.

The base is a small stand-in carrying what the stage reads from a
vaibify base image: ``/etc/vaibify/requirements.txt``, one feature's
``requirements.d`` list, and an unprivileged ``CONTAINER_USER``. The
stage is built through ``imageBuilder.fnApplyOverlay``, the call
``vaibify build`` makes, from a context staged by the real writer.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``). pip installs
from the package index.
"""

import json
import os
import shutil
import subprocess
import uuid

import pytest

from tests.liveContainerLabels import flistLabelArguments
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.cli import projectDeclarations
from vaibify.docker import imageBuilder
from vaibify.docker.dockerErrorDiagnosis import fsExplainBuildFailure
from vaibify.resources import fpathContainerImageRoot

pytestmark = pytest.mark.docker_live

S_STAND_IN_BASE = (
    "FROM python:3.12-slim\n"
    "RUN useradd -m researcher \\\n"
    "    && mkdir -p /etc/vaibify/requirements.d \\\n"
    "    && printf 'six\\n' > /etc/vaibify/requirements.txt \\\n"
    "    && printf 'attrs\\n' > /etc/vaibify/requirements.d/plotting.txt \\\n"
    "    && pip install --no-cache-dir six attrs\n"
    "ENV CONTAINER_USER=researcher\n"
    "USER researcher\n"
)


def _fsRunDocker(listArguments, iTimeout=900):
    return subprocess.run(
        ["docker", *listArguments], capture_output=True, text=True,
        check=True, timeout=iTimeout,
    ).stdout.strip()


@pytest.fixture
def dictStagedBuild(tmp_path, monkeypatch):
    """Yield a function that stages declarations and builds the stage on a stand-in."""
    fnRequireDaemonReachable()
    sProjectName = f"projectstage{uuid.uuid4().hex[:8]}"
    pathBase = tmp_path / "base"
    pathBase.mkdir()
    (pathBase / "Dockerfile").write_text(S_STAND_IN_BASE, encoding="utf-8")
    _fsRunDocker(["build", "-q", "-t", f"{sProjectName}:base", str(pathBase)])
    pathContext = tmp_path / "context"
    (pathContext / "overlays").mkdir(parents=True)
    shutil.copy(
        os.path.join(str(fpathContainerImageRoot()), "overlays",
                     "projectDependencies.dockerfile"),
        pathContext / "overlays")
    (pathContext / "pip-flags.txt").write_text("--prefer-binary\n")

    def fsBuild(dictProjects):
        listDeclarations = [
            {"sRepository": sName, "sUrl": f"https://host.example/{sName}",
             "sBranch": "main", "sCommit": "a" * 40,
             "sFile": "requirements.txt", "listRequirements": listLines}
            for sName, listLines in dictProjects.items()
        ]
        monkeypatch.setattr(
            projectDeclarations, "flistReadProjectDeclarations",
            lambda listRepositories, *aArgs, **kwargs: listDeclarations)
        projectDeclarations.flistStageProjectDeclarations([], str(pathContext))
        imageBuilder.fnApplyOverlay(
            sProjectName, imageBuilder.S_PROJECT_DEPENDENCIES_OVERLAY,
            str(pathContext), "base", bNoCache=True,
            listExtraLabelArguments=imageBuilder._flistOverlayOwnLabelArguments(
                imageBuilder.S_PROJECT_DEPENDENCIES_OVERLAY, str(pathContext)),
        )
        return f"{sProjectName}:{imageBuilder.S_PROJECT_DEPENDENCIES_OVERLAY}"
    yield {"fsBuild": fsBuild, "pathContext": pathContext,
           "sProjectName": sProjectName}
    for sTag in ("base", imageBuilder.S_PROJECT_DEPENDENCIES_OVERLAY):
        subprocess.run(["docker", "rmi", "-f", f"{sProjectName}:{sTag}"],
                       capture_output=True)


def _fsRunInImage(sImage, sProgram):
    return _fsRunDocker([
        "run", "--rm", *flistLabelArguments(), "--entrypoint", "python3",
        sImage, "-c", sProgram,
    ], iTimeout=120)


@pytest.mark.falsification
def testEveryProjectsDeclaredVersionIsTheOneInstalled(dictStagedBuild):
    """Kills: leaving the projects' files out of the joint install.

    The stand-in base already holds the newest ``six``; only the
    project's own pin can bring 1.16.0. Mutation: the stage's source
    list drops ``/etc/vaibify/projectRequirements/*.txt``.
    """
    sImage = dictStagedBuild["fsBuild"]({
        "alpha": ["six==1.16.0"], "beta": ["packaging>=23"],
    })
    assert _fsRunInImage(
        sImage, "import six, packaging, attrs; print(six.__version__)",
    ) == "1.16.0"


def testTheStageRecordsWhatItInstalledWhereTheStartCanReadIt(dictStagedBuild):
    sImage = dictStagedBuild["fsBuild"]({"alpha": ["six==1.16.0"]})
    listIndex = json.loads(_fsRunInImage(
        sImage, "print(open('/etc/vaibify/projectRequirements/index.json')"
                ".read())"))
    assert [d["sRepository"] for d in listIndex] == ["alpha"]
    assert _fsRunDocker([
        "image", "inspect", "--format",
        '{{index .Config.Labels "'
        + projectDeclarations.S_PROJECT_REQUIREMENTS_IMAGE_LABEL + '"}}',
        sImage,
    ]) == projectDeclarations.fsProjectRequirementsDigest(
        str(dictStagedBuild["pathContext"]))
    assert _fsRunDocker([
        "image", "inspect", "--format", "{{.Config.User}}", sImage,
    ]) == "researcher"


def testTwoProjectsAskingForIncompatibleVersionsStopTheBuildByName(
    dictStagedBuild,
):
    with pytest.raises(RuntimeError) as errorInfo:
        dictStagedBuild["fsBuild"]({
            "alpha": ["six>=1.16"], "beta": ["six<1.16"],
        })
    sSentence = fsExplainBuildFailure(
        dictStagedBuild["sProjectName"], str(errorInfo.value),
        errorInfo.value.sStderrTail)
    assert "'six>=1.16' is declared by project alpha" in sSentence
    assert "'six<1.16' is declared by project beta" in sSentence
    assert "${" not in sSentence.split("(Docker said:")[0], (
        "the step's own source, echoed by BuildKit, was read as a declarer")
