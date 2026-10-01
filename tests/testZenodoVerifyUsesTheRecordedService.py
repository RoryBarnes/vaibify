"""A Zenodo verify must ask the instance the project is configured for.

``fsResolveRecordedZenodoService`` answers "where does this project's
deposit live": the produced record's service, then the workflow-level
``sZenodoService`` the project declared (``zenodo`` is the production
instance's service key), then ``sandbox``. The endpoint
check in the Level 2 gate compares against that answer, but the verify
itself defaulted an unset record service straight to ``sandbox``, so a
project declared for the production instance verified against the sandbox, recorded
``sEndpointVerified: sandbox``, and was refused by its own gate for an
endpoint mismatch it had created.
"""

import hashlib
import os
from unittest.mock import MagicMock, patch

import pytest

from vaibify.reproducibility import scheduledReverify, syncBookkeeping

BA_DATA = b"a,b\n1,2\n"
S_DATA_SHA = hashlib.sha256(BA_DATA).hexdigest()


def _fdictWorkflow(sRepo, sTopLevelService, dictZenodoRemote):
    dictWorkflow = {
        "sProjectRepoPath": sRepo,
        "dictRemotes": {"zenodo": dictZenodoRemote},
        "listSteps": [{
            "sDirectory": "step01", "saOutputDataFiles": ["data.csv"],
            "saPlotFiles": [],
        }],
    }
    if sTopLevelService:
        dictWorkflow["sZenodoService"] = sTopLevelService
    return dictWorkflow


def _fsRepoWithData(tmp_path):
    sRepo = str(tmp_path / "project")
    os.makedirs(os.path.join(sRepo, "step01"))
    with open(os.path.join(sRepo, "step01", "data.csv"), "wb") as fileOut:
        fileOut.write(BA_DATA)
    return sRepo


def _fdictVerify(sRepo, dictWorkflow):
    mockFetch = MagicMock(return_value={"data.csv": S_DATA_SHA})
    with patch(
        "vaibify.reproducibility.zenodoClient.fdictFetchRemoteHashes",
        mockFetch,
    ), patch.object(
        scheduledReverify, "_fjsonFetchArchivedAttestation",
        return_value=None,
    ):
        dictStatus = scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "zenodo",
        )
    return dictStatus, mockFetch


@pytest.mark.falsification
def testAWorkflowDeclaredForTheProductionInstanceIsVerifiedAgainstIt(tmp_path):
    """Kills: scheduledReverify._fdictRequireServiceConfig: the Zenodo
    service fill-in from the recorded-service resolver removed."""
    sRepo = _fsRepoWithData(tmp_path)
    dictWorkflow = _fdictWorkflow(sRepo, "zenodo", {"sRecordId": "98765"})
    dictStatus, mockFetch = _fdictVerify(sRepo, dictWorkflow)
    assert mockFetch.call_args.kwargs["sService"] == "zenodo"
    assert dictStatus["sEndpointVerified"] == "zenodo"


def testTheVerifiedEndpointNowSatisfiesTheLevelTwoGate(tmp_path):
    """Cell and gate agree: the endpoint the verify records is the one the
    gate compares against, so it cannot refuse on a mismatch it created."""
    sRepo = _fsRepoWithData(tmp_path)
    dictWorkflow = _fdictWorkflow(
        sRepo, "zenodo", {"sRecordId": "98765", "sDoi": "10.5281/zenodo.98765"},
    )
    dictStatus, _mockFetch = _fdictVerify(sRepo, dictWorkflow)
    assert dictStatus["sEndpointVerified"] == (
        syncBookkeeping.fsResolveRecordedZenodoService(dictWorkflow))


def testARecordThatNamesItsServiceKeepsIt(tmp_path):
    sRepo = _fsRepoWithData(tmp_path)
    dictWorkflow = _fdictWorkflow(
        sRepo, "zenodo",
        {"sRecordId": "98765", "sService": "sandbox"},
    )
    dictStatus, mockFetch = _fdictVerify(sRepo, dictWorkflow)
    assert mockFetch.call_args.kwargs["sService"] == "sandbox"


def testAProjectThatDeclaredNothingStillDefaultsToSandbox(tmp_path):
    sRepo = _fsRepoWithData(tmp_path)
    dictWorkflow = _fdictWorkflow(sRepo, "", {"sRecordId": "98765"})
    dictStatus, mockFetch = _fdictVerify(sRepo, dictWorkflow)
    assert mockFetch.call_args.kwargs["sService"] == "sandbox"
