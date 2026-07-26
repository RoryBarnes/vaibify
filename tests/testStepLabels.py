"""Tests for canonical step-label helpers in pipelineUtils.

Step labels are per-type sequential: ``A09`` is the 9th *automated*
step, ``I01`` is the 1st *interactive* step. This mapping is not
positional — with two leading interactive steps, ``A09`` lands at
``listSteps[10]``, not ``listSteps[9]``. These tests pin the
semantics and the decorator helpers that expose ``sLabel`` on the
wire so agents do not have to translate in their heads.
"""

import itertools

import pytest

from vaibify.gui.pipelineUtils import (
    fbStepIsInteractive,
    fdictStepWithLabel,
    fdictWorkflowWithLabels,
    fiStepIndexFromLabel,
    flistComputeAllStepLabels,
    flistStepsWithLabels,
    fnAttachStepLabels,
    fsLabelFromStepIndex,
)


def _fdictMixedWorkflow():
    """One leading interactive, then 10 automated steps."""
    listSteps = [{"sName": "Intro", "bInteractive": True}]
    for iNum in range(1, 11):
        listSteps.append({"sName": f"Auto{iNum}"})
    return {"listSteps": listSteps}


def _fdictTwoInteractivePrefix():
    """Two leading interactive steps, then 10 automated.

    This is the case that bit in 2026-04: ``A09`` here is
    ``listSteps[10]``, not ``listSteps[9]``. Any helper that
    maps labels to indices must honor that.
    """
    listSteps = [
        {"sName": "Intro", "bInteractive": True},
        {"sName": "Survey", "bInteractive": True},
    ]
    for iNum in range(1, 11):
        listSteps.append({"sName": f"Auto{iNum}"})
    return {"listSteps": listSteps}


class TestFsLabelFromStepIndex:

    def test_first_automated_step_one_leading_interactive(self):
        dictWorkflow = _fdictMixedWorkflow()
        assert fsLabelFromStepIndex(dictWorkflow, 1) == "A01"

    def test_ninth_automated_step_one_leading_interactive(self):
        dictWorkflow = _fdictMixedWorkflow()
        assert fsLabelFromStepIndex(dictWorkflow, 9) == "A09"

    def test_ninth_automated_with_two_leading_interactive(self):
        """A09 lands at listSteps[10] when two I-steps precede it."""
        dictWorkflow = _fdictTwoInteractivePrefix()
        assert fsLabelFromStepIndex(dictWorkflow, 10) == "A09"
        # And the step at index 9 is A08, not A09:
        assert fsLabelFromStepIndex(dictWorkflow, 9) == "A08"

    def test_interactive_labels(self):
        dictWorkflow = _fdictTwoInteractivePrefix()
        assert fsLabelFromStepIndex(dictWorkflow, 0) == "I01"
        assert fsLabelFromStepIndex(dictWorkflow, 1) == "I02"

    def test_out_of_range_falls_back_to_numeric(self):
        dictWorkflow = {"listSteps": []}
        assert fsLabelFromStepIndex(dictWorkflow, 4) == "05"


class TestFiStepIndexFromLabel:

    def test_inverse_across_mixed_workflow(self):
        dictWorkflow = _fdictTwoInteractivePrefix()
        for iIndex in range(len(dictWorkflow["listSteps"])):
            sLabel = fsLabelFromStepIndex(dictWorkflow, iIndex)
            assert fiStepIndexFromLabel(
                dictWorkflow, sLabel) == iIndex

    def test_a09_resolves_to_index_ten_with_two_interactive_prefix(self):
        dictWorkflow = _fdictTwoInteractivePrefix()
        assert fiStepIndexFromLabel(dictWorkflow, "A09") == 10

    def test_a09_resolves_to_index_nine_with_one_interactive_prefix(self):
        dictWorkflow = _fdictMixedWorkflow()
        assert fiStepIndexFromLabel(dictWorkflow, "A09") == 9

    def test_lowercase_label_accepted(self):
        dictWorkflow = _fdictMixedWorkflow()
        assert fiStepIndexFromLabel(dictWorkflow, "a03") == 3

    def test_unknown_label_raises_with_message(self):
        dictWorkflow = _fdictMixedWorkflow()
        with pytest.raises(ValueError) as excInfo:
            fiStepIndexFromLabel(dictWorkflow, "A99")
        sMessage = str(excInfo.value)
        assert "A99" in sMessage
        assert "10" in sMessage
        assert "automated" in sMessage

    def test_malformed_label_raises(self):
        dictWorkflow = _fdictMixedWorkflow()
        with pytest.raises(ValueError):
            fiStepIndexFromLabel(dictWorkflow, "foo")

    def test_non_string_raises(self):
        dictWorkflow = _fdictMixedWorkflow()
        with pytest.raises(ValueError):
            fiStepIndexFromLabel(dictWorkflow, 3)


class TestFlistStepsWithLabels:

    def test_adds_slabel_to_every_step(self):
        dictWorkflow = _fdictTwoInteractivePrefix()
        listOut = flistStepsWithLabels(dictWorkflow)
        assert len(listOut) == len(dictWorkflow["listSteps"])
        assert listOut[0]["sLabel"] == "I01"
        assert listOut[1]["sLabel"] == "I02"
        assert listOut[2]["sLabel"] == "A01"
        assert listOut[10]["sLabel"] == "A09"

    def test_does_not_mutate_input_workflow(self):
        dictWorkflow = _fdictMixedWorkflow()
        flistStepsWithLabels(dictWorkflow)
        for dictStep in dictWorkflow["listSteps"]:
            assert "sLabel" not in dictStep

    def test_returned_dicts_are_independent_copies(self):
        dictWorkflow = _fdictMixedWorkflow()
        listOut = flistStepsWithLabels(dictWorkflow)
        listOut[0]["sName"] = "MUTATED"
        assert dictWorkflow["listSteps"][0]["sName"] != "MUTATED"

    def test_idempotent_on_repeat_calls(self):
        dictWorkflow = _fdictMixedWorkflow()
        first = flistStepsWithLabels(dictWorkflow)
        second = flistStepsWithLabels(dictWorkflow)
        assert first == second


class TestFdictWorkflowWithLabels:

    def test_preserves_other_top_level_fields(self):
        dictWorkflow = {
            "sName": "Example",
            "sProjectRepoPath": "/repo",
            "listSteps": [{"sName": "A"}],
        }
        dictOut = fdictWorkflowWithLabels(dictWorkflow)
        assert dictOut["sName"] == "Example"
        assert dictOut["sProjectRepoPath"] == "/repo"
        assert dictOut["listSteps"][0]["sLabel"] == "A01"

    def test_does_not_mutate_input(self):
        dictWorkflow = {"listSteps": [{"sName": "A"}]}
        fdictWorkflowWithLabels(dictWorkflow)
        assert "sLabel" not in dictWorkflow["listSteps"][0]


class TestFdictStepWithLabel:

    def test_returns_step_with_slabel(self):
        dictWorkflow = _fdictTwoInteractivePrefix()
        dictStep = fdictStepWithLabel(dictWorkflow, 10)
        assert dictStep["sLabel"] == "A09"
        assert dictStep["sName"] == "Auto9"

    def test_independent_copy(self):
        dictWorkflow = _fdictMixedWorkflow()
        dictStep = fdictStepWithLabel(dictWorkflow, 0)
        dictStep["sName"] = "MUTATED"
        assert dictWorkflow["listSteps"][0]["sName"] != "MUTATED"


class TestFnAttachStepLabels:

    def test_mutates_in_place(self):
        dictWorkflow = _fdictMixedWorkflow()
        fnAttachStepLabels(dictWorkflow)
        assert dictWorkflow["listSteps"][0]["sLabel"] == "I01"
        assert dictWorkflow["listSteps"][1]["sLabel"] == "A01"

    def test_recomputes_after_insertion(self):
        """Inserting an interactive step shifts subsequent A-labels."""
        dictWorkflow = _fdictMixedWorkflow()
        fnAttachStepLabels(dictWorkflow)
        assert dictWorkflow["listSteps"][9]["sLabel"] == "A09"
        dictWorkflow["listSteps"].insert(
            5, {"sName": "NewInter", "bInteractive": True},
        )
        fnAttachStepLabels(dictWorkflow)
        assert dictWorkflow["listSteps"][5]["sLabel"] == "I02"
        assert dictWorkflow["listSteps"][10]["sLabel"] == "A09"

    def test_overwrites_stale_label(self):
        dictWorkflow = _fdictMixedWorkflow()
        dictWorkflow["listSteps"][1]["sLabel"] = "STALE"
        fnAttachStepLabels(dictWorkflow)
        assert dictWorkflow["listSteps"][1]["sLabel"] == "A01"

    def test_idempotent(self):
        dictWorkflow = _fdictTwoInteractivePrefix()
        fnAttachStepLabels(dictWorkflow)
        listFirst = [s["sLabel"] for s in dictWorkflow["listSteps"]]
        fnAttachStepLabels(dictWorkflow)
        listSecond = [s["sLabel"] for s in dictWorkflow["listSteps"]]
        assert listFirst == listSecond

    def test_empty_workflow(self):
        dictWorkflow = {"listSteps": []}
        fnAttachStepLabels(dictWorkflow)
        assert dictWorkflow == {"listSteps": []}


# ---------------------------------------------------------------------------
# The label round trip must be TOTAL.
#
# Oracle (independent of the implementation): CLAUDE.md's Traps section
# states that a label names a step, that labels are per-type
# sequential, and that ``fiStepIndexFromLabel`` is the translation
# every caller must use. Naming a step and then resolving that name
# must therefore return the same step, for EVERY shape the persisted
# ``bInteractive`` field can take — it is JSON the in-container agent
# edits by hand and an ``Optional[bool]`` on the wire. Labeller and
# resolver disagreeing is not cosmetic: an agent told to run ``A02``
# runs a different step, silently.
# ---------------------------------------------------------------------------

_ABSENT_FLAG = object()

_T_INTERACTIVE_FLAG_VALUES = (
    True, False, None, "true", "false", 0, 1, _ABSENT_FLAG,
)


def _fdictStepWithFlag(sName, valueFlag):
    """Build a step whose bInteractive carries one persisted shape."""
    dictStep = {"sName": sName}
    if valueFlag is not _ABSENT_FLAG:
        dictStep["bInteractive"] = valueFlag
    return dictStep


@pytest.mark.falsification
def test_label_round_trip_is_total_over_every_flag_shape():
    """Every computed label resolves back to the step it names.

    Kills: the resolver classifying by equality against a bool.
    ``None`` is falsy but not equal to ``False``, so the labeller and
    the resolver disagreed and ``A01`` resolved to the SECOND step —
    executed against the real module before the fix.
    """
    for tFlags in itertools.product(
        _T_INTERACTIVE_FLAG_VALUES, repeat=3,
    ):
        listSteps = [
            _fdictStepWithFlag(f"Step{iIndex}", valueFlag)
            for iIndex, valueFlag in enumerate(tFlags)
        ]
        dictWorkflow = {"listSteps": listSteps}
        listLabels = flistComputeAllStepLabels(listSteps)
        assert len(set(listLabels)) == len(listLabels), (
            f"duplicate labels {listLabels} for flags {tFlags}"
        )
        for iIndex, sLabel in enumerate(listLabels):
            assert fiStepIndexFromLabel(
                dictWorkflow, sLabel) == iIndex, (
                f"label {sLabel} (step {iIndex}) resolved elsewhere "
                f"for flags {tFlags}"
            )


def test_label_round_trip_survives_a_hand_edited_null_flag():
    """The exact document that shipped the wrong-step bug."""
    listSteps = [
        {"sName": "One", "bInteractive": None},
        {"sName": "Two"},
    ]
    dictWorkflow = {"listSteps": listSteps}
    assert flistComputeAllStepLabels(listSteps) == ["A01", "A02"]
    assert fiStepIndexFromLabel(dictWorkflow, "A01") == 0
    assert fiStepIndexFromLabel(dictWorkflow, "A02") == 1


class TestFbStepIsInteractive:
    """The single classifier's reading of each persisted shape."""

    @pytest.mark.parametrize("valueFlag", [
        True, "true", "True", " yes ", 1, "on",
    ])
    def test_interactive_readings(self, valueFlag):
        assert fbStepIsInteractive({"bInteractive": valueFlag}) is True

    @pytest.mark.parametrize("valueFlag", [
        False, None, "", "false", "False", "0", "no", "off", "null", 0,
    ])
    def test_automated_readings(self, valueFlag):
        assert fbStepIsInteractive({"bInteractive": valueFlag}) is False

    def test_absent_flag_is_automated(self):
        assert fbStepIsInteractive({"sName": "A"}) is False

    def test_non_dict_is_automated(self):
        assert fbStepIsInteractive(None) is False
        assert fbStepIsInteractive("A01") is False

    @pytest.mark.falsification
    def test_the_string_false_is_not_read_as_a_non_empty_object(self):
        """A quoted "false" is the word it spells, not a truthy string.

        Kills: the labeller classifying by raw truthiness. A non-empty
        string is truthy, so the step would be labelled ``I01`` while
        every other reader — the resolver, the runner, the renderer —
        calls it automated.
        """
        dictWorkflow = {"listSteps": [{
            "sName": "One", "bInteractive": "false",
        }]}
        assert flistComputeAllStepLabels(
            dictWorkflow["listSteps"]) == ["A01"]
        assert fiStepIndexFromLabel(dictWorkflow, "A01") == 0
