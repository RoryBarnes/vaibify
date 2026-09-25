"""The readiness route is proven by a REQUEST, never by its source text.

``GET .../level3/readiness`` called ``fdictParsePinnedVersions`` through
``dependencyPinning``, where that symbol does not live -- it is in
``declaredPackages`` -- and the import sat outside every ``try``. Every
request raised ``ImportError`` and answered 500, so the Dependency-lock
row, the "Do this next" arrow and the verification pre-flight all
rendered "unknown" for the one question this route exists to answer.

The guard written for that very function was GREEN throughout. It read
the handler with ``inspect.getsource`` and checked that the call was
spelled there, which it was. Source presence is a claim about text; a
route's behaviour is a claim about a request, and only a request can
settle it. Five neighbours were red, two of them in the browser lane.

So the tests here drive the app. The one source-text assertion that
survives is in ``testTheLockIsCheckedBeforeARerunIsSpent`` and guards a
genuinely textual property -- that two call sites spell the SAME
constant -- which no request can observe.
"""

import pytest

from tests.testCarrierMigratedRoutes import (
    DockerDoubleHoldingALockTheContainerFails,
    S_CONTAINER_ID,
    _tConnectGatedClient,
)


@pytest.fixture
def tclientReadinessRequest():
    """A connected client over a project whose lock the container fails."""
    return _tConnectGatedClient(
        DockerDoubleHoldingALockTheContainerFails(),
    )


@pytest.mark.falsification
def test_the_readiness_request_answers_two_hundred(tclientReadinessRequest):
    """The regression itself: the route must not raise on every call.

    Kills: restoring the ``dependencyPinning`` import path for
    ``fdictParsePinnedVersions``. A boolean flip would survive this --
    the bug was an EXCEPTION, not a wrong answer, so the mutation that
    reproduces it is the wrong import, and the only observation that
    sees it is a request.
    """
    client, _connectionDocker = tclientReadinessRequest
    responseHttp = client.get(
        f"/api/workflow/{S_CONTAINER_ID}/level3/readiness",
    )
    assert responseHttp.status_code == 200, (
        "the readiness route raised instead of answering, which is "
        "exactly the failure a source-text guard could not see: "
        f"{responseHttp.status_code} {responseHttp.text[:400]}"
    )


@pytest.mark.falsification
def test_the_readiness_request_carries_the_lock_verdict(
    tclientReadinessRequest,
):
    """A 200 is not enough: the verdict has to be IN the answer.

    The three silent surfaces all read this block, so a route that
    answered 200 with the key absent would leave every one of them
    rendering "unknown" -- the same screen the ImportError produced,
    reached a different way.

    Kills: dropping ``dictLockSatisfaction`` from the gaps dict, and
    any change that leaves the probe unable to reach a verdict.
    """
    client, _connectionDocker = tclientReadinessRequest
    dictGaps = client.get(
        f"/api/workflow/{S_CONTAINER_ID}/level3/readiness",
    ).json()["dictL3ReadinessGaps"]
    dictVerdict = dictGaps["dictLockSatisfaction"]
    assert dictVerdict["sState"] == "mismatch", dictVerdict
    assert dictVerdict["listMismatches"] == [
        "numpy==2.5.2 (image has 2.2.6)",
    ], dictVerdict


def test_the_measurement_and_the_policy_are_separate_fields(
    tclientReadinessRequest,
):
    """TWO fields, because they answer two different questions.

    The measurement is three-state and is about the container the
    researcher is working in; the policy boolean is about the image a
    rerun would grade. One flag doing both jobs asserted the pin from a
    measurement of the running container AND collapsed ``unknown`` onto
    ``clean``, which is the pair of claims ruling 5 forbids.

    This fixture's daemon cannot report a live image identity, so the
    running container is NOT proven to be the pin -- and the policy
    boolean must therefore stay True over a real mismatch.
    """
    client, _connectionDocker = tclientReadinessRequest
    dictGaps = client.get(
        f"/api/workflow/{S_CONTAINER_ID}/level3/readiness",
    ).json()["dictL3ReadinessGaps"]
    assert dictGaps["dictLockSatisfaction"]["sState"] == "mismatch"
    assert dictGaps["bLockDoesNotBlockVerification"] is True, (
        "a mismatch measured against a container nobody has shown to "
        "be the pinned image blocked a verification of the pin"
    )
    assert "bLockSatisfiedByImage" not in dictGaps, (
        "the old single flag is back; it asserts the PIN from a "
        "measurement of the RUNNING container"
    )


@pytest.mark.falsification
def test_a_busy_container_pauses_the_probe_instead_of_queuing(
    tclientReadinessRequest, monkeypatch,
):
    """The dashboard issues this request ITSELF, so it must never wait.

    A plain mode-(b) carrier queues for the drain, and waiting spends
    an unpredictable amount of a request nobody made -- a ``git
    fetch`` or a step run holds it for minutes. Measured on
    2026-09-15: with the queuing form, opening a project delayed the
    remote badge refresh past ten seconds and
    ``tests/browser/testProjectBlockBadgesAreActionable.py`` failed on
    a badge that was working. The same wait would hold a Run Step.

    Paused is a RESULT: the route answers 200 and says nobody asked.
    An unanswered question rendered as an answer is the whole class of
    defect the three-state verdict exists to prevent -- and the cache
    is deliberately left alone, so a perfectly good earlier answer is
    not replaced by silence because the container happened to be busy.

    Kills: reverting the probe to ``fgenericRunWorkerUnderTheDrain``,
    which waits for the drain instead of taking "" for an answer.
    """
    from vaibify.gui.routes import reproducibilityRoutes
    from vaibify.reproducibility import lockSatisfaction
    client, _connectionDocker = tclientReadinessRequest

    dictBefore = client.get(
        f"/api/workflow/{S_CONTAINER_ID}/level3/readiness",
    ).json()["dictL3ReadinessGaps"]
    assert dictBefore["dictLockSatisfaction"]["sState"] == "mismatch"

    async def fdictReportBusy(
        sContainerId, fnWorker, sOperationTarget, requestHttp,
    ):
        del sContainerId, fnWorker, sOperationTarget, requestHttp
        return {
            "bPaused": True, "sPausedBy": "a step is running",
            "objResult": None,
        }

    monkeypatch.setattr(
        reproducibilityRoutes, "fdictRunAutomaticReadUnderTheDrain",
        fdictReportBusy,
    )
    dictResponse = client.get(
        f"/api/workflow/{S_CONTAINER_ID}/level3/readiness",
    )
    assert dictResponse.status_code == 200, dictResponse.text
    dictBody = dictResponse.json()
    assert dictBody["bProbePaused"] is True
    assert dictBody["sProbePausedBy"] == "a step is running"
    assert dictBody["dictL3ReadinessGaps"]["dictLockSatisfaction"] is None
    assert dictBody["sRecordKind"] == "", (
        "a question nobody asked was answered 'undetermined', which "
        "claims git could not say"
    )
    assert [
        dictVerdict for tKey, dictVerdict
        in lockSatisfaction.DICT_LAST_LOCK_SATISFACTION.items()
        if tKey[0] == S_CONTAINER_ID and dictVerdict
    ], (
        "a paused probe overwrote the cached verdict with its own "
        "silence, so the row lost an answer it had already earned"
    )


@pytest.mark.falsification
def test_the_verify_route_refuses_a_lock_the_pinned_image_fails(
    tclientReadinessRequest, monkeypatch,
):
    """The policy is enforced on the side that cannot be skipped.

    The JavaScript pre-flight checks the same thing and is not a
    control: ``_fdictFetchL3Readiness`` swallows a failed readiness GET
    and returns ``null``, and the caller then proceeds deliberately --
    so an ordinary click during a failed fetch launched the rerun, as
    did any direct API call and the whole agent lane. The shadow does
    catch the mismatch, but only after exporting the repository and
    building a container, which is exactly the cost the check exists
    to avoid (found by review, 2026-09-15).

    The refusal is asserted by MEANING and by ORDER -- the cheap
    remedy before the expensive one -- never by sentence, because the
    wording will be edited and the advice must not.

    Kills: dropping the ``fbLockBlocksVerification`` conjunct from
    ``_fsRequireReadinessThenDigest``, which returns the route to
    answering 202 and spending a container export on a rerun that is
    going to refuse.
    """
    from vaibify.gui.routes import reproducibilityRoutes
    client, _connectionDocker = tclientReadinessRequest
    monkeypatch.setattr(
        reproducibilityRoutes, "fdictAssessEnvelopeImageCurrency",
        lambda *args, **kwargs: {
            "bPinnedImageIsLive": True,
            "sPinnedImageDigest": "sha256:pinned",
            "sLiveImageDigest": "sha256:pinned",
        },
    )
    responseHttp = client.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/verify",
    )
    assert responseHttp.status_code == 409, (
        "a rerun was launched against an image that does not satisfy "
        f"the lock: {responseHttp.status_code} {responseHttp.text[:300]}"
    )
    sDetail = responseHttp.json()["detail"].lower()
    assert "requirements.lock" in sDetail, sDetail
    assert sDetail.index("regenerate") < sDetail.index("rebuilding"), (
        f"the expensive remedy is named before the cheap one: {sDetail}"
    )


def test_the_verify_route_proceeds_when_the_pin_is_not_the_container(
    tclientReadinessRequest, monkeypatch,
):
    """The complement, and it is the half that keeps the gate honest.

    The rerun grades the PINNED image. A mismatch measured against a
    container nobody has shown to be that image is no evidence about
    it, so it must not block -- blocking on any running-container
    mismatch refuses reruns whose pinned image is perfectly fine,
    which is the failure this route would trade for the other one.

    Asserted by the ABSENCE of the lock sentence rather than by a 202:
    this fixture has an incomplete envelope, so the route refuses for
    real readiness reasons either way, and a status assertion would
    pass without ever reaching the question.
    """
    from vaibify.gui.routes import reproducibilityRoutes
    client, _connectionDocker = tclientReadinessRequest
    monkeypatch.setattr(
        reproducibilityRoutes, "fdictAssessEnvelopeImageCurrency",
        lambda *args, **kwargs: {
            "bPinnedImageIsLive": None,
            "sPinnedImageDigest": "",
            "sLiveImageDigest": "sha256:whatever",
        },
    )
    sDetail = client.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/verify",
    ).json()["detail"].lower()
    assert "requirements.lock" not in sDetail, (
        "a rerun of the pinned image was refused over a measurement "
        f"of a container nobody showed to be it: {sDetail}"
    )
