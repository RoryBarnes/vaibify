"""A failed shadow rerun must survive the container that produced it.

The shadow is destroyed as soon as the comparison is made. Until this
collector existed the rerun's status callback was
``_fnDiscardStatusEvent``, so every step result and every line of
output went to the floor and a failed rerun produced one sentence --
"pipeline rerun exited non-zero" -- about a container that no longer
existed. The researcher could not re-run it, could not read its logs,
and could not tell a missing dependency from a real divergence
(researcher-reported, 2026-09-01).

Three properties are load-bearing and each is a way to make the record
useless or unsafe:

* The FIRST failure is what is kept. Later steps in a broken pipeline
  fail because the first one did, so keeping the last would point the
  researcher at the symptom furthest from the cause.
* The tail is BOUNDED. This record lands in ``l3_attestation.json``,
  which is committed and published to Zenodo, so an unbounded log would
  put a researcher's whole console -- paths, hostnames, whatever a
  traceback picked up -- into a public artefact.
* Events are FORWARDED. The collector is an observer on a stream the
  CLI also prints; one that swallowed the events it understood would
  silently break the caller it was added beside.
"""

import asyncio

import pytest

from vaibify.reproducibility.rerunDiagnostics import (
    I_MAX_OUTPUT_TAIL_LINES,
    ftBuildRerunDiagnosticsCollector,
)


def _fdictWorkflow():
    """Two automated steps, so labels are A01 and A02."""
    return {"listSteps": [
        {"sName": "GenerateSamples", "sDirectory": "GenerateSamples"},
        {"sName": "PlotHistogram", "sDirectory": "PlotHistogram"},
    ]}


def _fnDrive(listEvents, fnForwardTo=None):
    """Feed events through a fresh collector; return its diagnostics."""
    fnCollect, dictDiagnostics = ftBuildRerunDiagnosticsCollector(
        _fdictWorkflow(), fnForwardTo,
    )

    async def fnRunThemAll():
        for dictEvent in listEvents:
            await fnCollect(dictEvent)

    asyncio.run(fnRunThemAll())
    return dictDiagnostics


def test_a_clean_run_records_no_failure():
    """An empty dict means "no step failed", never "we did not look"."""
    dictSeen = _fnDrive([
        {"sType": "stepStarted", "iStepNumber": 1},
        {"sType": "output", "sLine": "all good"},
        {"sType": "stepPass", "iStepNumber": 1, "iExitCode": 0},
    ])
    assert dictSeen == {}


def test_the_failing_step_is_named_by_its_label_and_exit_code():
    """The researcher reads labels; every vaibify message speaks them."""
    dictSeen = _fnDrive([
        {"sType": "stepStarted", "iStepNumber": 2},
        {"sType": "output", "sLine": "ModuleNotFoundError: no 'scipy'"},
        {"sType": "stepFail", "iStepNumber": 2, "iExitCode": 1},
    ])
    assert dictSeen["sStepLabel"] == "A02"
    assert dictSeen["sStepName"] == "PlotHistogram"
    assert dictSeen["iExitCode"] == 1
    assert dictSeen["listOutputTail"] == [
        "ModuleNotFoundError: no 'scipy'",
    ]


def test_only_the_first_failure_is_kept():
    """A later step failing because an earlier one did is not the cause."""
    dictSeen = _fnDrive([
        {"sType": "stepStarted", "iStepNumber": 1},
        {"sType": "output", "sLine": "the real problem"},
        {"sType": "stepFail", "iStepNumber": 1, "iExitCode": 3},
        {"sType": "stepStarted", "iStepNumber": 2},
        {"sType": "output", "sLine": "downstream noise"},
        {"sType": "stepFail", "iStepNumber": 2, "iExitCode": 1},
    ])
    assert dictSeen["sStepLabel"] == "A01"
    assert dictSeen["iExitCode"] == 3
    assert dictSeen["listOutputTail"] == ["the real problem"]


def test_the_output_tail_is_bounded():
    """This record is committed and published; an unbounded log is a leak."""
    listEvents = [{"sType": "stepStarted", "iStepNumber": 1}]
    listEvents += [
        {"sType": "output", "sLine": f"line {iIndex}"}
        for iIndex in range(I_MAX_OUTPUT_TAIL_LINES * 3)
    ]
    listEvents.append(
        {"sType": "stepFail", "iStepNumber": 1, "iExitCode": 1},
    )
    dictSeen = _fnDrive(listEvents)
    assert len(dictSeen["listOutputTail"]) == I_MAX_OUTPUT_TAIL_LINES
    # The TAIL, not the head: the lines nearest the failure are the
    # ones that say what broke.
    assert dictSeen["listOutputTail"][-1] == (
        f"line {I_MAX_OUTPUT_TAIL_LINES * 3 - 1}"
    )


def test_a_batch_of_lines_is_counted_line_by_line():
    """outputBatch carries many lines in one event; the cap is per LINE.

    Counting events instead would let one batch of ten thousand lines
    through the bound entirely.
    """
    dictSeen = _fnDrive([
        {"sType": "stepStarted", "iStepNumber": 1},
        {"sType": "outputBatch", "listLines": [
            f"line {iIndex}"
            for iIndex in range(I_MAX_OUTPUT_TAIL_LINES * 2)
        ]},
        {"sType": "stepFail", "iStepNumber": 1, "iExitCode": 1},
    ])
    assert len(dictSeen["listOutputTail"]) == I_MAX_OUTPUT_TAIL_LINES


def test_a_new_step_drops_the_previous_steps_output():
    """The tail must describe the failing step, not the one before it."""
    dictSeen = _fnDrive([
        {"sType": "stepStarted", "iStepNumber": 1},
        {"sType": "output", "sLine": "chatter from the step that passed"},
        {"sType": "stepPass", "iStepNumber": 1, "iExitCode": 0},
        {"sType": "stepStarted", "iStepNumber": 2},
        {"sType": "output", "sLine": "the actual error"},
        {"sType": "stepFail", "iStepNumber": 2, "iExitCode": 1},
    ])
    assert dictSeen["listOutputTail"] == ["the actual error"]


def test_every_event_is_forwarded_to_the_caller():
    """The CLI prints this stream; an observer must not consume it."""
    listForwarded = []

    async def fnForward(dictEvent):
        listForwarded.append(dictEvent["sType"])

    listEvents = [
        {"sType": "stepStarted", "iStepNumber": 1},
        {"sType": "output", "sLine": "x"},
        {"sType": "stepFail", "iStepNumber": 1, "iExitCode": 1},
    ]
    _fnDrive(listEvents, fnForward)
    assert listForwarded == ["stepStarted", "output", "stepFail"]


def test_a_run_refused_before_any_step_is_recorded():
    """A pipeline can fail with NO step failing, and did.

    Preflight validation runs ahead of the first step and refuses the
    whole run -- a missing script, an absent step directory, duplicate
    step ids. It emits preflightFailed, never stepFail, so a collector
    watching only for step failures recorded nothing and the
    researcher was told "the reproduction run failed" and no more,
    about a shadow container already destroyed.
    """
    dictSeen = _fnDrive([
        {"sType": "preflightFailed", "listErrors": [
            "Step 2 'PlotHistogram': script plot.py not found",
            "Step 3 'AI Declaration': directory missing",
        ]},
    ])
    assert dictSeen["sKind"] == "preflight"
    assert dictSeen["listErrors"] == [
        "Step 2 'PlotHistogram': script plot.py not found",
        "Step 3 'AI Declaration': directory missing",
    ]


def test_a_preflight_refusal_keeps_every_error_not_just_the_first():
    """Preflight errors are independent findings, not a cascade.

    Unlike step failures -- where later steps fail because the first
    did -- each preflight error is about a different step and each one
    has to be fixed. Keeping only the first would send the researcher
    round the loop once per broken step.
    """
    listErrors = [f"Step {iIndex}: broken" for iIndex in range(1, 6)]
    dictSeen = _fnDrive([
        {"sType": "preflightFailed", "listErrors": listErrors},
    ])
    assert dictSeen["listErrors"] == listErrors


def test_a_preflight_refusal_is_distinguishable_from_a_step_failure():
    """The two need different fixes, so they must not read alike.

    "A step ran and failed" is a problem inside that step. "The run
    was refused before anything ran" means the workflow could not
    start at all -- telling a researcher their step failed when it was
    never attempted sends them to the wrong place.
    """
    dictStep = _fnDrive([
        {"sType": "stepStarted", "iStepNumber": 1},
        {"sType": "stepFail", "iStepNumber": 1, "iExitCode": 1},
    ])
    dictPreflight = _fnDrive([
        {"sType": "preflightFailed", "listErrors": ["nope"]},
    ])
    assert dictStep["sKind"] != dictPreflight["sKind"]


def test_a_failure_with_no_prior_step_started_still_records():
    """A step can fail before it emits anything; the record must not be lost.

    A dependency-missing refusal fails the step directly, so the
    collector never sees stepStarted for it. Losing the record there
    would leave exactly the class of failure that is hardest to
    diagnose with no evidence at all.
    """
    dictSeen = _fnDrive([
        {"sType": "stepFail", "iStepNumber": 1, "iExitCode": 1},
    ])
    assert dictSeen["sStepLabel"] == "A01"
    assert dictSeen["listOutputTail"] == []
