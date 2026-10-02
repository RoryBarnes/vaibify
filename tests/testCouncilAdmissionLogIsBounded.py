"""The credential store's admission audit log keeps only its newest entries.

The log lives in a document that every admission rewrites, so an unbounded
list would grow the file (and the time to rewrite it) with every turn a
researcher ever ran. The bound keeps the most recent admissions and drops
the oldest.
"""

import pytest

from vaibify.gui import agentCouncilCredentialStore


@pytest.mark.falsification
def testTheAdmissionLogKeepsOnlyTheNewestEntries():
    """Kills: fnRecordAdmission: the trailing
    `del dictDocument["listAdmissions"][:-I_MAX_RECORDED_ADMISSIONS]`
    replaced by `pass`.
    """
    iMaximum = agentCouncilCredentialStore.I_MAX_RECORDED_ADMISSIONS
    dictDocument = {"listAdmissions": []}
    for iAdmission in range(iMaximum + 3):
        agentCouncilCredentialStore.fnRecordAdmission(
            dictDocument, "providerKey", {"iAdmission": iAdmission},
        )
    listKept = [
        dictEntry["iAdmission"]
        for dictEntry in dictDocument["listAdmissions"]
    ]
    assert listKept == list(range(3, iMaximum + 3))
