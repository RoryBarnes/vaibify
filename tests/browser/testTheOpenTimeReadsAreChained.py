"""The three open-time requests are a chain, not a race.

The readiness request carries the lock-satisfaction probe, an
automatic read that PAUSES rather than queues. Fired alongside the
badge refresh and the remote refresh on every project open, it lost
the carrier race, came back "paused", and the Dependency-lock verdict
was never measured on a real open at all -- visible only as a
"container probe 0.01s" timing line that read as fast rather than as
skipped (2026-09-16).

The order is badges -> readiness -> remote refresh. The remote
refresh goes LAST because awaiting it would serialize nothing: its
response says only which services it began asking about, and the
background checks it starts are exactly the durable work the probe
pauses against.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.mark.falsification
def test_readiness_runs_after_badges_and_before_the_remote_refresh(
    pageDashboard, serverHub,
):
    """ONE open, the real requests, their real order.

    Asserted on request START order via a capture attached before
    the open -- a response-order assertion would pass for concurrent
    requests that merely happened to answer politely.

    Kills: firing the readiness request alongside the others instead
    of chaining it -- the shipped shape, in which the probe pauses on
    every open and the lock verdict stays unmeasured.
    """
    listStarts = []

    def _fnRecord(requestSeen):
        sUrl = requestSeen.url
        if "/badges" in sUrl:
            listStarts.append("badges")
        elif "/level3/readiness" in sUrl:
            listStarts.append("readiness")
        elif "/fetch-project-repo" in sUrl:
            listStarts.append("driftFetch")
        elif "/remotes/refresh" in sUrl:
            listStarts.append("remoteRefresh")

    pageDashboard.on("request", _fnRecord)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    # The chain's tail: wait until the remote refresh has been seen
    # (or fail loudly after the timeout -- a chain that never reaches
    # its last link is not "in order").
    for _iTick in range(100):
        if "remoteRefresh" in listStarts:
            break
        pageDashboard.wait_for_timeout(100)
    listOrdered = [sName for sName in listStarts if sName in (
        "badges", "readiness", "remoteRefresh",
    )]
    assert "badges" in listOrdered, listOrdered
    assert "readiness" in listOrdered, listOrdered
    assert "remoteRefresh" in listOrdered, (
        "the chain never reached the remote refresh; the last link "
        f"is missing, not merely reordered: {listOrdered}"
    )
    iBadges = listOrdered.index("badges")
    iReadiness = listOrdered.index("readiness")
    iRefresh = listOrdered.index("remoteRefresh")
    assert iBadges < iReadiness < iRefresh, (
        "the open-time requests raced instead of chaining -- the "
        "readiness probe loses that race and the lock verdict goes "
        f"unmeasured: {listOrdered}"
    )
    # The origin-drift check opens a lock-held git-fetch carrier, and
    # it was the LAST racer standing: fired from fnSelectWorkflow it
    # held the drain at exactly the readiness probe's moment ("PAUSED
    # (carrier held by helper on git-fetch)", live log, 2026-09-16).
    # EVERY drift fetch must start after readiness -- asserting only
    # the first would pass with a reintroduced parallel call beside
    # the chained one.
    listEarlyFetches = [
        iIndex for iIndex, sName in enumerate(listStarts)
        if sName == "driftFetch"
        and iIndex < listStarts.index("readiness")
    ]
    assert listEarlyFetches == [], (
        "a git-fetch carrier opened before the readiness request; "
        f"the probe pauses against it: {listStarts}"
    )
