"""The determinism row names the libraries the profiles do not cover.

The reading comes from the REAL detector run over a real lock file and
is rendered by the real Project block; the row must show it verbatim
beside the MKL reading, as evidence, not a verdict.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.testUncoveredLibrariesAreNamed import S_LOCK_WITH_EVERY_FAMILY
from vaibify.reproducibility import determinismGate


pytestmark = pytest.mark.browser

_S_RENDER_DETERMINISM_GROUP = """(dictMaths) => {
    const elHost = document.createElement('div');
    elHost.innerHTML = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            dictDeterminism: null, bDeterminismDeclared: false,
            bRebuildAttestationCurrent: false,
            listDeterminismQuestions: dictMaths.listQuestions,
            dictMathsLibrary: dictMaths.dictReading,
        },
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['determinism']),
        setExpandedRequirementRows: new Set(['determinism-repeatability']),
        setToggledFileGroups: new Set(),
    });
    const sText = elHost.textContent;
    return sText;
}"""


@pytest.mark.falsification
def testTheRowShowsTheUncoveredLibrariesNote(
        pageDashboard, serverHub, tmp_path):
    """Kills: dropping the uncovered-libraries note from the row."""
    from vaibify.gui.routes.pipelineRoutes import (
        _flistEnvelopeDeterminismQuestions,
    )
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    (tmp_path / "requirements.lock").write_text(S_LOCK_WITH_EVERY_FAMILY)
    dictReading = determinismGate.fdictDetectMathsLibrary(str(tmp_path))
    sText = pageDashboard.evaluate(_S_RENDER_DETERMINISM_GROUP, {
        "dictReading": dictReading,
        "listQuestions": _flistEnvelopeDeterminismQuestions({}),
    })
    assert dictReading["sNote"] in sText
    assert dictReading["sUncoveredNote"] in sText
    assert pageDashboard.listPageErrors == []
