"""Recognising a vaibify container, on a lane that forbids mutation.

Reported from a Linux machine running main: a registered project's tile
did nothing when clicked. The chain was long and every link was silent.

``GET /api/registry`` is read-only, but it is served by
``ContainerAwareRoute``, which marks its lane ENFORCED. Recognition ran
``test -d /workspace/.vaibify`` through ``ftResultExecuteCommand``, and
arbitrary command execution is ALWAYS treated as mutating -- the
primitive cannot know whether the text it was handed reads a file or
deletes a workspace. So the gate refused, a broad ``except`` turned the
refusal into ``False``, every container was reclassified as
unrecognized, the registered project never received its
``sContainerId``, and the frontend click handler returned early because
it could not resolve one. Nothing anywhere said no.

The two tests below are the two halves that were each individually
passable and together are not:

* recognition works on an ENFORCED lane, with the real admission gate
  in place -- the half that was broken;
* recognition does not depend on a Docker LABEL -- the half a
  label-based fix would break, silently and only for containers that
  predate the labelling path, which is the hardest kind of regression
  to notice because every container you create while testing has one.
"""

import pytest

from vaibify.config import mutationAdmission
from vaibify.gui import registryRoutes


S_CONTAINER_ID = "abc123container"


class _ConnectionRecognisingByMarker:
    """A double that answers the typed read and GUARDS the exec.

    ``ftResultExecuteCommand`` asserts the admission gate exactly as
    the real ``DockerConnection`` does, so a caller that reaches for an
    arbitrary command on this lane is refused here too. Without that,
    this test would pass against the very code it exists to reject.
    """

    def __init__(self, bHasMarker=True):
        self.bHasMarker = bHasMarker
        self.listExecCommands = []

    def flistContainerPathsExist(self, sContainerId, listPaths):
        del sContainerId
        return [self.bHasMarker for _ in listPaths]

    def ftResultExecuteCommand(self, sContainerId, sCommand, **dictKeywords):
        del sContainerId, dictKeywords
        self.listExecCommands.append(sCommand)
        mutationAdmission.fnAssertContainerCommandAdmitted(
            S_CONTAINER_ID, "ftResultExecuteCommand",
        )
        return (0, "")

    def fcontainerGetById(self, sContainerId):
        raise AssertionError(
            "recognition consulted Docker metadata for "
            f"{sContainerId}; a label is applied by one creation path "
            "and absent from every container that predates it"
        )


@pytest.fixture(autouse=True)
def fixtureEnforceTheLane():
    """Put the request lane in the state the real route runs in.

    The defect only exists where the gate is live. A test that skipped
    this would exercise the ambient lane, where an exec is admitted,
    and would have reported the broken code as working.
    """
    token = mutationAdmission.ftokenMarkEnforcedLane()
    try:
        yield
    finally:
        mutationAdmission.fnResetEnforcedLane(token)


@pytest.mark.falsification
def testRecognitionSurvivesTheMutationGate():
    """A read-only listing must not need mutation admission.

    Kills: recognising a container by running an arbitrary command,
    which the enforced lane refuses -- after which the broad except
    reports every container as not-vaibify and every registered
    project loses its container id.
    """
    connectionDocker = _ConnectionRecognisingByMarker(bHasMarker=True)
    assert registryRoutes._fbIsVaibifyContainer(
        connectionDocker, {"sContainerId": S_CONTAINER_ID},
    ) is True
    assert connectionDocker.listExecCommands == [], (
        "recognition ran an arbitrary command: "
        f"{connectionDocker.listExecCommands}"
    )


@pytest.mark.falsification
def testAContainerWithoutTheMarkerIsNotRecognised():
    """The other direction: recognition must still be able to say no.

    A check that answered True unconditionally would pass the test
    above and sweep every unrelated container on the machine into the
    researcher's project list.

    Kills: recognising anything that is merely reachable.
    """
    connectionDocker = _ConnectionRecognisingByMarker(bHasMarker=False)
    assert registryRoutes._fbIsVaibifyContainer(
        connectionDocker, {"sContainerId": S_CONTAINER_ID},
    ) is False


@pytest.mark.falsification
def testARefusedReadIsNotReportedAsNotAVaibifyContainer():
    """A refusal is an architectural fact, not an answer of "no".

    Swallowing it is what made the original defect invisible: the
    dashboard showed a shorter list rather than an error, and a
    researcher had no way to tell a missing container from a refused
    probe.

    Kills: catching ControlPlaneRefusalError and returning False.
    """
    class _ConnectionRefusing(_ConnectionRecognisingByMarker):
        def flistContainerPathsExist(self, sContainerId, listPaths):
            del sContainerId, listPaths
            raise mutationAdmission.MutationNotAdmittedError("refused")

    with pytest.raises(mutationAdmission.ControlPlaneRefusalError):
        registryRoutes._fbIsVaibifyContainer(
            _ConnectionRefusing(), {"sContainerId": S_CONTAINER_ID},
        )


def testAnOrdinaryFailureStillAnswersNo():
    """A container that vanished mid-listing is not an error to raise."""
    class _ConnectionExploding(_ConnectionRecognisingByMarker):
        def flistContainerPathsExist(self, sContainerId, listPaths):
            del sContainerId, listPaths
            raise RuntimeError("no such container")

    assert registryRoutes._fbIsVaibifyContainer(
        _ConnectionExploding(), {"sContainerId": S_CONTAINER_ID},
    ) is False
