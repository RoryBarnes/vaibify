"""A reader's run in a real container reproduces the author's bytes.

The unit lane proves the decision (replay the recorded epoch for a
foreign manifest) is made in the one shared place; it cannot prove that
real matplotlib, in a real container, then writes the author's SVG and
PDF byte for byte. This does. The oracle is independent of the code under
test: the author's bytes are produced FIRST, by an author-identity run
dated by the epoch the author recorded, and the reader's reproduction is
judged against the hashes that run wrote into the manifest.

Journey (identity A, then identity B, in one container):

1. The author's step writes a JSON number, an ``np.savez`` grid, a PNG, an
   SVG and a PDF. The manifest pins their hashes; the envelope records
   the epoch; a publishing commit follows, so HEAD differs from it.
2. The reader's identity is configured and every output is corrupted
   (standing in for the reader's own host rerun). The manifest check
   lists exactly those outputs.
3. The reader's run goes through the real determinism lane and the real
   step command. The manifest check then lists nothing -- SVG and PDF
   included.
4. Control: the same step dated from HEAD (what a reader got before)
   does NOT match the vector outputs, so step 3 is not matching by luck.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one. The container installs git, numpy and matplotlib.
"""

import asyncio
import posixpath

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui import determinismEnvironment
from vaibify.gui.pipelineRunner import (
    S_ENV_PREFIX_KEY,
    _fnInjectDeterminismEnvPrefix,
)
from vaibify.reproducibility import manifestWriter
from vaibify.reproducibility.repoFiles import ContainerRepoFiles

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.12-slim"
S_USER = "researcher"
S_HOME = "/home/researcher"
S_REPO = S_HOME + "/proj"
S_STEP_DIRECTORY = "MakeGrid"
S_STEP_SCRIPT = S_STEP_DIRECTORY + "/generate.py"
LIST_OUTPUTS = [
    S_STEP_DIRECTORY + "/value.json", S_STEP_DIRECTORY + "/grid.npz",
    S_STEP_DIRECTORY + "/figure.png", S_STEP_DIRECTORY + "/figure.svg",
    S_STEP_DIRECTORY + "/figure.pdf",
]
I_RECORDED_EPOCH = 1790631466
S_AUTHOR = "author@example.invalid"
S_READER = "reader@example.invalid"

S_SCRIPT_TEXT = '''import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
json.dump({"value": 42}, open("MakeGrid/value.json", "w"))
np.savez("MakeGrid/grid.npz", grid=np.arange(12.0).reshape(3, 4))
plt.plot([1, 2, 3], [1, 4, 9])
for sExtension in ("png", "svg", "pdf"):
    plt.savefig("MakeGrid/figure." + sExtension)
'''


@pytest.fixture(scope="module")
def liveProject():
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import (
        DockerConnection, _fnEnsureDockerHost,
    )
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    try:
        clientDocker.images.get(S_IMAGE)
    except docker.errors.ImageNotFound:
        clientDocker.images.pull(S_IMAGE)
    container = clientDocker.containers.run(
        S_IMAGE, ["sleep", "1800"], detach=True, labels=fdictLabels(),
    )
    try:
        iExit, baOutput = container.exec_run(
            ["sh", "-c",
             f"useradd -m {S_USER} && apt-get update -qq && "
             "apt-get install -y -qq git >/dev/null && "
             "pip install --no-cache-dir --quiet numpy matplotlib"],
            user="root")
        assert iExit == 0, baOutput.decode("utf-8", errors="replace")
        yield container, DockerConnection()
    finally:
        container.remove(force=True)


def _ftRun(liveProject, sCommand, sWorkdir=S_REPO):
    container, connection = liveProject
    tResult = connection.ftRunInContainerStreamed(
        container.id, sCommand, sWorkdir=sWorkdir)
    return tResult.iExitCode, tResult.sStdout.strip(), tResult.sStderr


def _fnRunOk(liveProject, sCommand, sWorkdir=S_REPO):
    iExit, sOutput, sError = _ftRun(liveProject, sCommand, sWorkdir)
    assert iExit == 0, f"{sCommand}\n{sOutput}\n{sError}"
    return sOutput


def _fsPrefixFor(iEpoch):
    return asyncio.run(determinismEnvironment._fsBuildDeterminismEnvPrefix(
        None, "cid", "", iSourceDateEpochOverride=iEpoch))


def _fnPublishAsAuthor(liveProject):
    """Author phase: bytes dated by the recorded epoch, then published."""
    _fnRunOk(liveProject, f"mkdir -p {S_REPO}/{S_STEP_DIRECTORY}", S_HOME)
    _fnRunOk(liveProject, "git init -q && git config user.email "
             f"{S_AUTHOR} && git config user.name Author")
    container, connection = liveProject
    connection.fnWriteFile(
        container.id, posixpath.join(S_REPO, S_STEP_SCRIPT),
        S_SCRIPT_TEXT.encode("utf-8"))
    _fnRunOk(liveProject, _fsPrefixFor(I_RECORDED_EPOCH)
             + f"python3 {S_STEP_SCRIPT}")
    _fnRunOk(liveProject, "mkdir -p .vaibify && printf "
             "'{\"iSourceDateEpoch\": %d}' " % I_RECORDED_EPOCH
             + "> .vaibify/environment.json")
    _fnRunOk(liveProject, "(echo '# vaibify manifest v1'; "
             "sha256sum " + " ".join(LIST_OUTPUTS) + " | "
             "sed 's/  / /;s/ /  /') > MANIFEST.sha256")
    _fnRunOk(liveProject, "git add -A && git commit -q -m 'author publishes'")
    _fnRunOk(liveProject, "git commit -q --allow-empty -m 'publish the record'")


def _flistMismatches(liveProject):
    container, connection = liveProject
    filesRepo = ContainerRepoFiles(connection, container.id, S_REPO)
    return sorted(
        dictEntry["sPath"] if isinstance(dictEntry, dict) else dictEntry
        for dictEntry in manifestWriter.flistVerifyManifest(filesRepo)
    )


def _fdictRunTheReadersStep(liveProject):
    container, connection = liveProject
    dictVariables = {}
    dictWorkflow = {"sProjectRepoPath": S_REPO, "listSteps": []}
    asyncio.run(_fnInjectDeterminismEnvPrefix(
        connection, container.id, dictWorkflow, dictVariables))
    _fnRunOk(liveProject, dictVariables[S_ENV_PREFIX_KEY]
             + f"python3 {S_STEP_SCRIPT}")
    return dictVariables


@pytest.mark.falsification
def testAReadersContainerRunReproducesTheAuthorsVectorFigures(liveProject):
    """The whole journey, ending in zero mismatches with SVG and PDF included.

    Kills: dating the reader's run from anything but the recorded epoch.
    """
    _fnPublishAsAuthor(liveProject)
    _fnRunOk(liveProject, f"git config user.email {S_READER}")
    sHeadEpoch = _fnRunOk(liveProject, "git log -1 --format=%ct HEAD")
    assert int(sHeadEpoch) != I_RECORDED_EPOCH
    for sOutput in LIST_OUTPUTS:
        _fnRunOk(liveProject, f"echo corrupted > {sOutput}")
    assert _flistMismatches(liveProject) == sorted(LIST_OUTPUTS)
    dictVariables = _fdictRunTheReadersStep(liveProject)
    assert f"SOURCE_DATE_EPOCH={I_RECORDED_EPOCH}" in dictVariables[
        S_ENV_PREFIX_KEY]
    assert _flistMismatches(liveProject) == []
    # Control: dated from HEAD, as a reader's run was before, the vector
    # outputs do not match, so the zero above is not luck.
    _fnRunOk(liveProject, _fsPrefixFor(int(sHeadEpoch))
             + f"python3 {S_STEP_SCRIPT}")
    listAfterHead = _flistMismatches(liveProject)
    assert S_STEP_DIRECTORY + "/figure.pdf" in listAfterHead
    assert S_STEP_DIRECTORY + "/figure.svg" in listAfterHead
