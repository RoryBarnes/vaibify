"""What the Project Hub's routes say when they refuse.

Two contracts. A control-plane refusal's sentence is written for the
researcher and names the reconcile that clears it, so the client-facing
sanitizer passes it through where every other unexpected exception
stays generic. And the frontend's duplicate-name recovery must match
the sentences the server actually sends.
"""

import os

from vaibify.config.mutationAdmission import (
    ControlPlaneRefusalError, MutationNotAdmittedError,
)
from vaibify.gui.pipelineServer import fsSanitizeExceptionForClient

S_STATIC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def test_a_control_plane_refusal_reaches_the_client_in_its_own_words():
    sRefusal = (
        "Container 'proj' has a quarantined journal record (abc): a "
        "'file-write' operation on '/workspace/x' was left unfinished, so "
        "no new operation may commit until it is reconciled."
    )
    assert fsSanitizeExceptionForClient(
        MutationNotAdmittedError(sRefusal)) == sRefusal
    assert fsSanitizeExceptionForClient(
        ControlPlaneRefusalError("refused")) == "refused"


def test_every_other_unexpected_exception_stays_generic():
    assert fsSanitizeExceptionForClient(
        RuntimeError("/Users/someone/secret/path exploded")
    ) == "Pipeline action failed. Check server logs for details."


def test_the_wizard_recognises_the_servers_collision_sentences():
    """The recovery keyed on a word the server never sends was dead."""
    sSource = open(
        os.path.join(S_STATIC, "scriptNewWorkflowWizard.js"),
    ).read()
    iStart = sSource.index("function _fbErrorIsNameCollision(")
    sBlock = sSource[iStart:sSource.index("\n    }\n", iStart)]
    assert '"already exists"' in sBlock
    assert 'indexOf("workflow")' not in sBlock


def test_the_project_hub_reports_failures_through_the_diagnosis_module():
    sSource = open(
        os.path.join(S_STATIC, "scriptWorkflowManager.js"),
    ).read()
    iStart = sSource.index("async function fnSelectWorkflow(")
    sBlock = sSource[iStart:sSource.index("\n    }\n", iStart)]
    assert "VaibifyDiagnosis.fnReportFailureFromError(error)" in sBlock
