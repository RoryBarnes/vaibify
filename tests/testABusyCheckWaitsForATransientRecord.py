"""An operation still in flight is not a project that needs reconciling.

Promoting from inside the open project refused ITSELF. The sequence,
measured end to end in a real browser on 2026-09-16:

1. the dashboard polls file-status, which runs a command in the
   container to read test markers -- a READ, but it travels the
   arbitrary-exec path, so it is admitted and journaled like a
   mutation;
2. promotion releases the caller's own session, deliberately, because
   refusing the researcher's own tab once made in-browser promotion
   impossible at all;
3. that release makes the in-flight poll's admission stale, and the
   poll ends;
4. promotion's own busy check reads the journal in the gap before that
   record settles, finds it unsettled, and answers 409 "has operations
   that are not settled; reconcile it before you promote it".

So the promotion created the condition it refused itself over, and
sent the researcher to `vaibify reconcile` for a container that needed
none. Measured: the record settled 142ms and 154ms after the refusal
on two runs, and the whole journey failed two times in three on
Firefox, occasionally on Chromium -- an engine's polling cadence is
what decides whether the gap is hit.

This test is DETERMINISTIC where the journey is not. A browser test
cannot be the guard here: it reproduces the race only sometimes, so it
would report green against a broken check about a third of the time.
The resolver is driven directly, unsettled for a fixed number of reads
and settled after, which pins the behaviour rather than the timing.

Both directions are asserted. A check that waited forever would pass
the first test and is the opposite defect -- a project genuinely busy
with a long run must still be refused, and told so.

Kills (confirmed -- the mutation was applied, this test run, and the
named assertion observed to fail):
  - the bounded wait replaced by a single read, i.e. the original
    check -> the transient record is refused and the first test fails.
"""

import asyncio

import pytest

from vaibify.config import operationJournal
from vaibify.gui import registryRoutes


S_PROJECT = "projectUnderTest"


class _FakeResolver:
    """Answer unsettled for the first N reads, then settled."""

    def __init__(self, iUnsettledReads, sUnsettled=None):
        self.iUnsettledReads = iUnsettledReads
        self.iReads = 0
        self.sUnsettled = (
            sUnsettled or operationJournal.S_RESOLUTION_BUSY
        )

    def fdictResolve(self, sName, connectionDocker=None,
                     bPersistResolution=True):
        assert bPersistResolution is False, (
            "the busy check must READ the resolution, never persist a "
            "verdict about a record it is only waiting on"
        )
        self.iReads += 1
        if self.iReads <= self.iUnsettledReads:
            return {"sResolution": self.sUnsettled}
        return {"sResolution": operationJournal.S_RESOLUTION_SETTLED}


@pytest.fixture
def fnPatchResolver(monkeypatch):
    def fnApply(resolverFake):
        monkeypatch.setattr(
            operationJournal, "fdictResolveContainerJournal",
            resolverFake.fdictResolve,
        )
    return fnApply


@pytest.mark.falsification
def testARecordThatSettlesIsNotCalledUnsettled(fnPatchResolver):
    """A record in flight at the first read is waited out, not refused.

    Kills: the bounded wait replaced by a single read -> the transient
    record is refused and this assertion fails.
    """
    resolverFake = _FakeResolver(iUnsettledReads=3)
    fnPatchResolver(resolverFake)

    bSettled = asyncio.get_event_loop_policy().new_event_loop(
    ).run_until_complete(
        registryRoutes._fbWaitForJournalToSettle(S_PROJECT, {})
    )

    assert bSettled is True, (
        "a record that settles 3 reads in was reported unsettled; the "
        "researcher would be sent to reconcile a container that needed "
        "none"
    )
    assert resolverFake.iReads > 1, (
        "the resolution was read once, so nothing was waited for"
    )


def testAProjectThatStaysBusyIsStillRefused(fnPatchResolver, monkeypatch):
    """The wait is bounded: a genuinely busy project still refuses.

    Not a falsification entry of its own -- it is the symmetric half
    that stops the fix above from being "wait forever", which would
    hang the request instead of answering it.
    """
    monkeypatch.setattr(
        registryRoutes, "F_JOURNAL_SETTLE_DEADLINE_SECONDS", 0.2,
    )
    resolverFake = _FakeResolver(iUnsettledReads=10_000)
    fnPatchResolver(resolverFake)

    bSettled = asyncio.get_event_loop_policy().new_event_loop(
    ).run_until_complete(
        registryRoutes._fbWaitForJournalToSettle(S_PROJECT, {})
    )

    assert bSettled is False, (
        "a project whose journal never settles must still be refused; "
        "waiting forever hangs the request instead of answering it"
    )
