"""The shadow rerun's admission, and the refusal it exists to prevent.

This file exists because of a defect that was written and would have
shipped invisibly. The L3 verify route opens a mode-(c) durable carrier
for the RESEARCHER's project container, then hands the work off to a
background thread. The rerun inside that thread drives the ordinary
``DockerConnection`` — and every exec it makes asks the mutation gate
whether *this container id* is admitted. The shadow container's id is
not the project container's, so every step of the rerun would have
raised ``MutationNotAdmittedError`` from inside a background task, and
the researcher would have seen an attestation fail for a reason nothing
in the message could explain.

Nothing in the ordinary route tests could catch it: their connection
doubles answer a write by storing bytes and never consult the gate at
all, which is the same blind spot recorded for the carrier migration.
So these tests drive the REAL gate, with two container identities kept
distinct throughout, and assert the refusal as carefully as the
admission — an admission that authorized everything would satisfy the
positive half and destroy the property.
"""

import pytest

from vaibify.config import mutationAdmission
from vaibify.gui import commitCarrier


S_PROJECT_CONTAINER = "researcherProjectContainer"
S_SHADOW_CONTAINER = "vaibifyDisposableShadowFEEDFACE"


@pytest.fixture
def fnOpenProjectDurableCarrier(monkeypatch):
    """Open a durable admission for the PROJECT container, and close it.

    Minted directly through the carrier's own mint rather than by
    driving a route, because what is under test is the gate's answer for
    two container ids, not the route that produced the first one.
    """
    listTokens = []

    def fnOpen():
        tokenLane = mutationAdmission.ftokenMarkEnforcedLane()
        admission = mutationAdmission._fadmissionMintForCommitCarrier(
            S_PROJECT_CONTAINER, S_PROJECT_CONTAINER,
            mutationAdmission.S_ADMISSION_MODE_DURABLE_TASK,
            bDurable=True,
        )
        tokenAdmission = mutationAdmission.ftokenActivateAdmission(
            admission)
        listTokens.append((tokenLane, tokenAdmission))

    yield fnOpen
    for tTokens in reversed(listTokens):
        commitCarrier.fnCloseRequestAdmission(tTokens)


def testTheProjectsCarrierDoesNotAdmitTheShadowContainer(
    fnOpenProjectDurableCarrier,
):
    """The defect itself, pinned so it cannot come back silently.

    This is the state the shadow rerun runs in on the dashboard lane:
    an enforced lane holding a durable admission for the project
    container. An exec aimed at the shadow must be refused, because
    the admission names one container and the shadow is not it. If this
    test ever passes without the shadow's own admission, the gate has
    stopped being per-container and the whole model is gone.
    """
    fnOpenProjectDurableCarrier()
    mutationAdmission.fnAssertContainerCommandAdmitted(
        S_PROJECT_CONTAINER, "ftRunInContainerStreamed",
    )
    with pytest.raises(mutationAdmission.MutationNotAdmittedError):
        mutationAdmission.fnAssertContainerCommandAdmitted(
            S_SHADOW_CONTAINER, "ftRunInContainerStreamed",
        )


def testTheShadowsOwnAdmissionMakesItsRerunPossible(
    fnOpenProjectDurableCarrier,
):
    """Inside the shadow's admission, the shadow's execs are admitted."""
    fnOpenProjectDurableCarrier()
    tTokens = commitCarrier.ftOpenDisposableContainerAdmission(
        S_SHADOW_CONTAINER, S_SHADOW_CONTAINER,
    )
    try:
        mutationAdmission.fnAssertContainerCommandAdmitted(
            S_SHADOW_CONTAINER, "ftRunInContainerStreamed",
        )
    finally:
        commitCarrier.fnCloseRequestAdmission(tTokens)


@pytest.mark.falsification
def testTheShadowsAdmissionSatisfiesTheDurableExecGate(
    fnOpenProjectDurableCarrier,
):
    """The rerun's STEPS run through the durable gate, not the plain one.

    Preflight execs ask ``fnAssertContainerCommandAdmitted``; the steps
    themselves stream through ``ftRunInContainerStreamedWithChunks``,
    whose gate is ``fnAssertDurableExecAdmitted`` and filters on
    ``bDurable``. An admission that satisfies only the plain gate passes
    every test above, survives preflight on a live daemon, and refuses
    the FIRST real step -- so the lane looks wired right up until a
    workflow actually survives preflight. That is how it shipped: minted
    non-durable, and no live run had yet reached a step.

    Kills: In ftOpenDisposableContainerAdmission, mint the admission
    without bDurable=True, so the durable gate finds no qualifying
    admission for the shadow and refuses every step of every rerun.
    """
    fnOpenProjectDurableCarrier()
    with pytest.raises(mutationAdmission.MutationNotAdmittedError):
        mutationAdmission.fnAssertDurableExecAdmitted(
            S_SHADOW_CONTAINER, "ftRunInContainerStreamedWithChunks",
        )
    tTokens = commitCarrier.ftOpenDisposableContainerAdmission(
        S_SHADOW_CONTAINER, S_SHADOW_CONTAINER,
    )
    try:
        mutationAdmission.fnAssertDurableExecAdmitted(
            S_SHADOW_CONTAINER, "ftRunInContainerStreamedWithChunks",
        )
    finally:
        commitCarrier.fnCloseRequestAdmission(tTokens)


def testTheShadowsAdmissionReachesNothingButTheShadow():
    """The narrowness IS the safety property, so assert it directly.

    A shadow admission that authorized the project container would let
    a rerun reach back into the researcher's own workspace — which is
    the thing this whole lane was built to stop doing. Opened here
    WITHOUT the project's carrier so the refusal cannot be satisfied by
    some other admission being absent.
    """
    tTokens = commitCarrier.ftOpenDisposableContainerAdmission(
        S_SHADOW_CONTAINER, S_SHADOW_CONTAINER,
    )
    try:
        with pytest.raises(mutationAdmission.MutationNotAdmittedError):
            mutationAdmission.fnAssertContainerCommandAdmitted(
                S_PROJECT_CONTAINER, "ftRunInContainerStreamed",
            )
    finally:
        commitCarrier.fnCloseRequestAdmission(tTokens)


def testTheAdmissionDoesNotOutliveTheContainerItNames(
    fnOpenProjectDurableCarrier,
):
    """A leaked admission would be inherited by whatever ran next.

    The shadow container is destroyed at the end of the lane. An
    admission for its id left active on the context would then name a
    container that no longer exists — and, worse, a name a future
    disposable could be handed. Closing in ``finally`` is what prevents
    it, and this asserts the closure rather than trusting the keyword.
    """
    fnOpenProjectDurableCarrier()
    tTokens = commitCarrier.ftOpenDisposableContainerAdmission(
        S_SHADOW_CONTAINER, S_SHADOW_CONTAINER,
    )
    commitCarrier.fnCloseRequestAdmission(tTokens)
    with pytest.raises(mutationAdmission.MutationNotAdmittedError):
        mutationAdmission.fnAssertContainerCommandAdmitted(
            S_SHADOW_CONTAINER, "ftRunInContainerStreamed",
        )


@pytest.mark.falsification
def testTheShadowLaneOpensTheAdmissionAroundTheComparison(monkeypatch):
    """Drive the real lane and observe the gate from inside it.

    The three tests above establish what the gate does. This one
    establishes that the LANE uses it — a distinction worth its own
    test, because a threaded seam can be written, accepted, and then not
    called, and every call site still reads correctly. The comparison
    stub asks the real gate the question the real rerun would ask.
    
    Kills: dropping the shadow's own admission from the lane, which
    is the defect this file was written for -- every step of the
    rerun then raises MutationNotAdmittedError inside a background
    task.
    """
    from tests.testShadowRerun import (
        _FakeConnection, _fdictEnvironment, _fdictWorkflow,
        S_LIVE_REPO, S_LIVE_WORKFLOW, dictHarness,
    )
    del dictHarness
    listAdmitted = []

    def fdictCompare(connection, sContainerId, dictWorkflow,
                     sWorkflowPath, filesRepo, fnStatusCallback=None):
        del connection, dictWorkflow, sWorkflowPath, filesRepo
        del fnStatusCallback
        mutationAdmission.fnAssertContainerCommandAdmitted(
            sContainerId, "ftRunInContainerStreamed",
        )
        listAdmitted.append(sContainerId)
        return {"bPassed": True, "iOutputHashesMatched": 0,
                "iOutputHashesTotal": 0, "listDivergedHashes": []}

    tokenLane = mutationAdmission.ftokenMarkEnforcedLane()
    admission = mutationAdmission._fadmissionMintForCommitCarrier(
        S_PROJECT_CONTAINER, S_PROJECT_CONTAINER,
        mutationAdmission.S_ADMISSION_MODE_DURABLE_TASK, bDurable=True,
    )
    tokenAdmission = mutationAdmission.ftokenActivateAdmission(admission)
    try:
        from vaibify.reproducibility import shadowRerun
        _fnInstallFakeDaemon(monkeypatch)
        shadowRerun.fdictRerunInShadowContainer(
            _FakeConnection(), S_PROJECT_CONTAINER, _fdictWorkflow(),
            S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
            fdictRunAndVerify=fdictCompare,
        )
    finally:
        commitCarrier.fnCloseRequestAdmission(
            (tokenLane, tokenAdmission))
    assert len(listAdmitted) == 1
    assert listAdmitted[0] != S_PROJECT_CONTAINER, (
        "the comparison ran against the project container, not a shadow"
    )


def _fnInstallFakeDaemon(monkeypatch):
    """Point the shadow lane's gateway at the unit suite's fake daemon."""
    import io

    from tests import testShadowRerun
    from vaibify.docker import disposableSpecification
    from vaibify.reproducibility import shadowRerun

    dictState = {
        "listCreated": [], "listCopies": [], "setRemoved": set(),
        "dictLabels": {}, "bRemovalFails": False,
    }
    monkeypatch.setattr(
        shadowRerun.disposableContainer, "fdockerCreateDisposableClient",
        lambda: testShadowRerun._FakeDockerClient(dictState))
    monkeypatch.setattr(
        shadowRerun.disposableContainer, "_fmoduleGetDocker",
        lambda: type("_M", (), {"errors": type(
            "_E", (), {"NotFound": testShadowRerun._FakeNotFound})})())
    monkeypatch.setattr(
        disposableSpecification, "fbufferRepackArchiveStamped",
        lambda baArchive, sPrefix="": io.BytesIO(baArchive))
