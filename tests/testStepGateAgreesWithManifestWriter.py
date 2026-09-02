"""The step gate and the manifest writer must name outputs identically.

Two functions decided what a step's declared outputs are CALLED, and
they disagreed:

* ``manifestPaths.flistStepOutputRepoPaths`` -- used by the manifest
  WRITER -- joins a non-templated file to the step directory and
  resolves ``{token}`` entries against the workflow's top-level string
  fields.
* ``levelGates._flistStepOutputFiles`` -- used by the step GATE --
  returned the raw declarations unchanged.

So a correctly written manifest held ``AiPowerOverTime/fits.json`` and
``Plot/fig.png`` while the gate searched it for ``fits.json`` and
``{sPlotDirectory}/fig.{sFigureType}``. Neither could ever match. The
step reported ``missing-from-manifest`` permanently and told the
researcher to refresh the manifest -- which rewrites exactly the
resolved paths the gate was not asking for, so no action could clear
it. The literal ``{sFigureType}`` shown in the blocker text was the
visible symptom.

The same raw list fed the per-step SYNC projection, where the failure
inverts and worsens: a path that cannot intersect the diverged set
yields no blocker at all, so a diverged file reads as published.

These pin the two collectors to each other rather than to a fixture,
because that is the property -- either may change how a path is
spelled, but they may not disagree.
"""

import pytest

from vaibify.reproducibility.levelGates import _flistStepOutputFiles
from vaibify.reproducibility.manifestPaths import (
    fdictWorkflowTemplateValues,
    flistStepOutputRepoPaths,
)


DICT_WORKFLOW = {"sPlotDirectory": "Plot", "sFigureType": "png"}

LIST_STEP_SHAPES = [
    pytest.param(
        {"sDirectory": "StepOne",
         "saOutputDataFiles": ["fits.json"], "saPlotFiles": []},
        id="bare-name-gets-step-directory"),
    pytest.param(
        {"sDirectory": "StepOne", "saOutputDataFiles": [],
         "saPlotFiles": ["{sPlotDirectory}/fig.{sFigureType}"]},
        id="templated-plot-resolves-to-plot-directory"),
    pytest.param(
        {"sDirectory": "StepOne",
         "saOutputDataFiles": ["fits.json", "grid.npz"],
         "saPlotFiles": ["{sPlotDirectory}/fig.{sFigureType}"]},
        id="mixed-both-kinds"),
    pytest.param(
        {"sDirectory": "", "saOutputDataFiles": ["root.json"],
         "saPlotFiles": []},
        id="no-step-directory"),
    pytest.param(
        {"sDirectory": "StepOne", "saOutputDataFiles": [],
         "saPlotFiles": ["{sNoSuchToken}/fig.png"]},
        id="unresolvable-token-skipped-by-both"),
]


@pytest.mark.parametrize("dictStep", LIST_STEP_SHAPES)
def test_the_gate_and_the_writer_name_the_same_paths(dictStep):
    """Whatever the writer pins, the gate must look for -- exactly."""
    dictValues = fdictWorkflowTemplateValues(DICT_WORKFLOW)
    assert sorted(_flistStepOutputFiles(dictStep, dictValues)) == sorted(
        flistStepOutputRepoPaths(dictStep, dictValues)
    )


def test_a_templated_plot_path_never_reaches_a_caller_unresolved():
    """No caller may receive a literal '{token}' to show or compare.

    Asserting agreement alone would pass if BOTH sides regressed to
    raw declarations, so this pins the resolved form directly. It is
    also what the researcher sees: the blocker text printed
    '{sFigureType}' on screen.
    """
    dictStep = {
        "sDirectory": "AiPowerOverTime",
        "saOutputDataFiles": ["fits.json"],
        "saPlotFiles": ["{sPlotDirectory}/fig.{sFigureType}"],
    }
    listPaths = _flistStepOutputFiles(
        dictStep, fdictWorkflowTemplateValues(DICT_WORKFLOW),
    )
    assert "Plot/fig.png" in listPaths
    assert "AiPowerOverTime/fits.json" in listPaths
    assert not any("{" in sPath for sPath in listPaths), listPaths


def test_the_template_values_argument_is_required():
    """No default, so a missed call site fails loudly.

    A default would let a hop that forgot to thread the values fall
    back to the broken behaviour silently -- indistinguishable from
    the bug this file exists for, and invisible to every other test.
    """
    with pytest.raises(TypeError):
        _flistStepOutputFiles({"sDirectory": "A"})


# --------------------------------------------------------- context parity


def test_both_per_step_contexts_carry_the_same_reader_keys():
    """Two independent context builders feed the same per-step readers.

    ``_fdictL3PerStepContext`` and ``_fdictStepProjectionContext`` are
    built separately and handed to overlapping consumers. Threading
    ``dictTemplateValues`` into only the first raised ``KeyError`` on
    the poll path -- a 500 that blanks every badge and level cell on
    the dashboard, which is the documented failure mode for a gate
    that raises during a poll.

    Pinned as a relationship rather than a key list: whichever keys
    the readers share, both builders must supply. Asserting one key by
    name would go stale the next time a reader needs something new.
    """
    from vaibify.reproducibility import levelGates
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/demo",
        "sPlotDirectory": "Plot", "sFigureType": "png",
        "listSteps": [],
    }
    dictProjection = levelGates._fdictStepProjectionContext(
        dictWorkflow, [], [], [],
    )
    dictPerStep = levelGates._fdictL3PerStepContext(dictWorkflow, "")
    for sKey in ("dictTemplateValues", "listDeclaredBinaries"):
        assert sKey in dictPerStep, (
            f"_fdictL3PerStepContext is missing {sKey!r}"
        )
        assert sKey in dictProjection, (
            f"_fdictStepProjectionContext is missing {sKey!r}; a "
            "per-step reader handed this context raises KeyError, "
            "which 500s the poll and blanks the dashboard"
        )
