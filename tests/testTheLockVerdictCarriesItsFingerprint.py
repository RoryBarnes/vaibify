"""The cached lock verdict is invalidated by a FINGERPRINT, not by a call.

Reading the installed package list costs an exec, and the poll may add
none, so the answer is measured where execs are permitted and cached for
every surface that renders it. A cache needs invalidation, and the first
shape of it was ``fnForgetLockSatisfaction`` -- a function with ZERO
callers, which is exactly how a hand-invalidated cache fails: silently,
the first time a write path forgets.

So the authority is a fingerprint over the state the verdict describes,
compared on every read at no cost. Both halves are load-bearing and a
lock-only fingerprint misses the second, which is the one ruling 5 makes
load-bearing: the measurement is taken against the RUNNING container, so
replacing that container must make the answer unknown even when the lock
has not moved.

Every test here removes ``fnForgetLockSatisfaction`` first, so what is
proven is the fingerprint rather than a call somebody remembered to make.
"""

import pytest

from vaibify.reproducibility import lockSatisfaction


class _FakeRepoFilesHoldingOneLock:
    """A repo adapter whose lock bytes can be rewritten between reads."""

    def __init__(self, sSha256):
        self.sSha256 = sSha256

    def fdictHashFiles(self, listRelPaths):
        return {
            sRel: {
                "sSha256": self.sSha256 if sRel == "requirements.lock"
                else None,
                "sSymlinkSegment": None,
                "bEscapesRoot": False,
            }
            for sRel in listRelPaths
        }


S_CONTAINER = "container-under-test"


@pytest.fixture
def fnForgetIsGone(monkeypatch):
    """Remove the hand invalidation, so only the fingerprint can act."""
    monkeypatch.setattr(
        lockSatisfaction, "fnForgetLockSatisfaction",
        lambda sContainerId: None,
    )
    lockSatisfaction.DICT_LAST_LOCK_SATISFACTION.pop(S_CONTAINER, None)
    yield
    lockSatisfaction.DICT_LAST_LOCK_SATISFACTION.pop(S_CONTAINER, None)


def _fnRecordCleanAgainst(filesRepo, sImageIdentity):
    """Record a CLEAN verdict stamped with the state it was measured on."""
    lockSatisfaction.fnRecordLockSatisfaction(
        S_CONTAINER,
        lockSatisfaction.fdictDescribeLockSatisfaction(
            {"numpy": "2.5.2"}, {"numpy": "2.5.2"},
        ),
        lockSatisfaction.fsFingerprintLockState(filesRepo, sImageIdentity),
    )


def test_an_unmoved_state_reads_back_the_answer(fnForgetIsGone):
    """The control: without this, every test below passes vacuously."""
    filesRepo = _FakeRepoFilesHoldingOneLock("aaa")
    _fnRecordCleanAgainst(filesRepo, "sha256:image-one")
    dictRead = lockSatisfaction.fdictReadLockSatisfaction(
        S_CONTAINER,
        lockSatisfaction.fsFingerprintLockState(
            filesRepo, "sha256:image-one",
        ),
    )
    assert dictRead["sState"] == lockSatisfaction.S_LOCK_CLEAN


@pytest.mark.falsification
def test_a_rewritten_lock_downgrades_the_answer_to_unknown(fnForgetIsGone):
    """Regenerating the envelope rewrites the lock under the verdict.

    Kills: dropping the lock digest from the fingerprint, which leaves
    a "clean" verdict standing over a lock nobody has compared.
    """
    _fnRecordCleanAgainst(
        _FakeRepoFilesHoldingOneLock("aaa"), "sha256:image-one",
    )
    dictRead = lockSatisfaction.fdictReadLockSatisfaction(
        S_CONTAINER,
        lockSatisfaction.fsFingerprintLockState(
            _FakeRepoFilesHoldingOneLock("bbb"), "sha256:image-one",
        ),
    )
    assert dictRead["sState"] == lockSatisfaction.S_LOCK_UNKNOWN
    assert dictRead["listMismatches"] == []
    assert dictRead["sReason"], "unknown must say why it is unknown"


@pytest.mark.falsification
def test_a_replaced_running_image_downgrades_the_answer_too(fnForgetIsGone):
    """The half a lock-only fingerprint misses, and the load-bearing one.

    The verdict is measured against the container the researcher is
    RUNNING (ruling 5), so a rebuild or a switch replaces the very
    thing that was measured while the lock file sits untouched. A
    fingerprint over the lock alone keeps answering about an image
    that is gone.

    Kills: dropping the running-image identity from the fingerprint.
    """
    filesRepo = _FakeRepoFilesHoldingOneLock("aaa")
    _fnRecordCleanAgainst(filesRepo, "sha256:image-one")
    dictRead = lockSatisfaction.fdictReadLockSatisfaction(
        S_CONTAINER,
        lockSatisfaction.fsFingerprintLockState(
            filesRepo, "sha256:image-two",
        ),
    )
    assert dictRead["sState"] == lockSatisfaction.S_LOCK_UNKNOWN


def test_a_container_never_asked_reads_as_never_asked(fnForgetIsGone):
    """Absent is None, not a fabricated unknown verdict.

    Every surface renders a missing answer exactly as it did before
    this check existed. A synthesized ``unknown`` dict would be
    indistinguishable from one a failed exec produced, and the reason
    string would be a sentence about a measurement nobody attempted.
    """
    assert lockSatisfaction.fdictReadLockSatisfaction(
        S_CONTAINER, "anything",
    ) is None


def test_a_probe_with_no_lock_file_stores_nothing(fnForgetIsGone):
    """``None`` means there is no lock to be satisfied, and is not cached.

    Storing it would put a falsy record where the reader tests for
    one, and the reader would then have to tell "no lock" from "never
    asked" -- two absences that render identically and would not stay
    that way.
    """
    lockSatisfaction.fnRecordLockSatisfaction(S_CONTAINER, None, "fp")
    assert lockSatisfaction.fdictReadLockSatisfaction(
        S_CONTAINER, "fp",
    ) is None


def test_an_unreadable_lock_fingerprints_differently_from_a_read_one():
    """A repo the poll could not sample must not confirm a stale answer.

    The snapshot answers ``sSha256: None`` for a path it did not
    sample, and the honest consequence is a fingerprint that matches
    no real one -- so the verdict degrades to unknown rather than
    being confirmed by an absence.
    """
    sReadable = lockSatisfaction.fsFingerprintLockState(
        _FakeRepoFilesHoldingOneLock("aaa"), "image",
    )
    sUnsampled = lockSatisfaction.fsFingerprintLockState(
        _FakeRepoFilesHoldingOneLock(None), "image",
    )
    assert sReadable != sUnsampled
