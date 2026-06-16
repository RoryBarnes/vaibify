"""Scaling regression tests for pipelineUtils label helpers.

The bulk-label path used by ``fnAttachStepLabels`` and
``flistStepsWithLabels`` was historically O(N**2) because it called
``fsLabelFromStepIndex`` per step, which itself scanned 0..iIndex.
At 500 steps the cost dominated every workflow load and save. The
linear pass introduced by ``flistComputeAllStepLabels`` preserves
the per-type-sequential semantics while collapsing the cost to O(N).
"""

import time

from vaibify.gui.pipelineUtils import (
    flistComputeAllStepLabels,
    flistStepsWithLabels,
    fnAttachStepLabels,
    fsLabelFromStepIndex,
)


def _fdictWorkflowWithMixedSteps(iAutoCount, iInteractiveCount):
    """Build a workflow with leading interactive steps then automated."""
    listSteps = []
    for iNum in range(iInteractiveCount):
        listSteps.append({"sName": f"Inter{iNum}", "bInteractive": True})
    for iNum in range(iAutoCount):
        listSteps.append({"sName": f"Auto{iNum}"})
    return {"listSteps": listSteps}


def _flistLabelsViaSinglePass(dictWorkflow):
    """Compute labels by calling the public single-step API per index."""
    listSteps = dictWorkflow.get("listSteps", [])
    return [
        fsLabelFromStepIndex(dictWorkflow, iIndex)
        for iIndex in range(len(listSteps))
    ]


class TestFlistComputeAllStepLabels:

    def test_matches_per_step_calls_for_500_step_workflow(self):
        dictWorkflow = _fdictWorkflowWithMixedSteps(450, 50)
        listExpected = _flistLabelsViaSinglePass(dictWorkflow)
        listActual = flistComputeAllStepLabels(
            dictWorkflow["listSteps"],
        )
        assert listActual == listExpected

    def test_attaches_match_per_step_calls_for_500_steps(self):
        dictWorkflow = _fdictWorkflowWithMixedSteps(450, 50)
        listExpected = _flistLabelsViaSinglePass(dictWorkflow)
        fnAttachStepLabels(dictWorkflow)
        listActual = [
            dictStep["sLabel"]
            for dictStep in dictWorkflow["listSteps"]
        ]
        assert listActual == listExpected

    def test_list_with_labels_matches_per_step_calls(self):
        dictWorkflow = _fdictWorkflowWithMixedSteps(450, 50)
        listExpected = _flistLabelsViaSinglePass(dictWorkflow)
        listOut = flistStepsWithLabels(dictWorkflow)
        listActual = [dictStep["sLabel"] for dictStep in listOut]
        assert listActual == listExpected

    def test_handles_empty_workflow(self):
        assert flistComputeAllStepLabels([]) == []

    def test_500_step_attach_completes_under_one_second(self):
        dictWorkflow = _fdictWorkflowWithMixedSteps(450, 50)
        fStart = time.perf_counter()
        fnAttachStepLabels(dictWorkflow)
        fElapsed = time.perf_counter() - fStart
        assert fElapsed < 1.0
