"""The remote-data timestamp says only what vaibify observed.

The field shipped as ``sRetrievedUtc`` and asserted a retrieval nobody
watched: it is stamped while HASHING, after every command in the step
has already run, and only when the digest differs from the recorded
one. So it was neither a retrieval time (arbitrary commands may run
between any fetch and the hash, and the file may never have been
downloaded) nor a last-hashed time (an unchanged file is re-hashed
every run and keeps its old stamp).

It is now ``sDigestBecameCurrentUtc``, which is what the guard
actually implements. Two earlier candidate names — ``sHashedUtc`` and
``sDigestFirstObservedUtc`` — were rejected for the same underlying
reason, and the A → B → A case below is what rejects them: a digest
that recurs gets a NEW stamp, so the value is neither the first
observation of that digest nor the last time it was hashed.
"""

import pytest

from vaibify.gui import workflowMigrations
from vaibify.gui.pipelineRunner import _fbApplyRemoteDataHashes
from vaibify.gui.workflowMigrations import S_DIGEST_TIMESTAMP_KEY


_S_SHA_A = "a" * 64
_S_SHA_B = "b" * 64
_S_PATH = "Data/samples.csv"


def _fdictStepWithDigest(sSha256="", sStamp=""):
    return {
        "sStepId": "fetch", "sName": "Fetch", "sDirectory": "Fetch",
        "listRemoteData": [{
            "sPath": _S_PATH,
            "sSourceUrl": "https://example.invalid/samples.csv",
            "sSha256": sSha256,
            S_DIGEST_TIMESTAMP_KEY: sStamp,
        }],
    }


def testARecurringDigestIsStampedAgain():
    """A → B → A re-stamps, so the value is not a FIRST observation.

    This is the case that rejected ``sDigestFirstObservedUtc``. If the
    field were ever changed to record a first sighting, this test is
    the one that must be updated deliberately rather than silently.
    """
    dictStep = _fdictStepWithDigest(
        sSha256=_S_SHA_A, sStamp="2020-01-01T00:00:00Z",
    )
    assert _fbApplyRemoteDataHashes(dictStep, {_S_PATH: _S_SHA_B})
    sAfterB = dictStep["listRemoteData"][0][S_DIGEST_TIMESTAMP_KEY]
    assert sAfterB != "2020-01-01T00:00:00Z"

    assert _fbApplyRemoteDataHashes(dictStep, {_S_PATH: _S_SHA_A}), (
        "returning to a previously seen digest must count as a change"
    )
    dictRecord = dictStep["listRemoteData"][0]
    assert dictRecord["sSha256"] == _S_SHA_A
    assert dictRecord[S_DIGEST_TIMESTAMP_KEY] != "2020-01-01T00:00:00Z", (
        "the stamp for the returning digest is its most recent "
        "becoming-current, not the first time it was seen — which is "
        "why the field is not named FirstObserved"
    )


def testAnUnchangedDigestKeepsItsStamp():
    """Re-hashing an unchanged file must not move the timestamp.

    This is the case that rejected ``sHashedUtc``: the file IS hashed
    on this run, so a last-hashed field would have to move, and it
    deliberately does not.
    """
    dictStep = _fdictStepWithDigest(
        sSha256=_S_SHA_A, sStamp="2020-01-01T00:00:00Z",
    )
    assert not _fbApplyRemoteDataHashes(dictStep, {_S_PATH: _S_SHA_A})
    assert dictStep["listRemoteData"][0][S_DIGEST_TIMESTAMP_KEY] == (
        "2020-01-01T00:00:00Z"
    )


def testTheLegacyNameMigratesCarryingItsValue():
    """An existing project keeps its timestamp under the honest name."""
    dictWorkflow = {
        "iWorkflowSchemaVersion": 11,
        "listSteps": [{
            "sStepId": "fetch", "sDirectory": "Fetch",
            "listRemoteData": [{
                "sPath": _S_PATH, "sSha256": _S_SHA_A,
                "sRetrievedUtc": "2026-03-04T05:06:07Z",
            }],
        }],
    }
    workflowMigrations.fiApplyMigrations(dictWorkflow)
    dictRecord = dictWorkflow["listSteps"][0]["listRemoteData"][0]
    assert "sRetrievedUtc" not in dictRecord
    assert dictRecord[S_DIGEST_TIMESTAMP_KEY] == "2026-03-04T05:06:07Z"


def testMigrationPrefersAnExistingHonestValue():
    """A record carrying both keys keeps the new one, not the legacy."""
    dictWorkflow = {
        "iWorkflowSchemaVersion": 11,
        "listSteps": [{
            "sStepId": "fetch", "sDirectory": "Fetch",
            "listRemoteData": [{
                "sPath": _S_PATH, "sSha256": _S_SHA_A,
                "sRetrievedUtc": "2020-01-01T00:00:00Z",
                S_DIGEST_TIMESTAMP_KEY: "2026-03-04T05:06:07Z",
            }],
        }],
    }
    workflowMigrations.fiApplyMigrations(dictWorkflow)
    dictRecord = dictWorkflow["listSteps"][0]["listRemoteData"][0]
    assert "sRetrievedUtc" not in dictRecord
    assert dictRecord[S_DIGEST_TIMESTAMP_KEY] == "2026-03-04T05:06:07Z"


@pytest.mark.parametrize("dictMalformed", [
    {"listSteps": [{"listRemoteData": ["not-a-dict"]}]},
    {"listSteps": ["not-a-dict"]},
    {"listSteps": [{"listRemoteData": None}]},
    {},
])
def testMigrationToleratesMalformedDocuments(dictMalformed):
    """A hand-edited project must not crash the loader."""
    dictMalformed["iWorkflowSchemaVersion"] = 11
    workflowMigrations.fiApplyMigrations(dictMalformed)
