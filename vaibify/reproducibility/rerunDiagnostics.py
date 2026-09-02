"""Keep the reason a shadow rerun failed, instead of discarding it.

A tier 5 rerun executes the whole workflow inside a shadow container
that is destroyed as soon as the comparison is made. Until this module
existed, its status callback was ``_fnDiscardStatusEvent`` -- the
pipeline's step results and every line of its output went to the
floor -- so a failed rerun produced exactly one sentence, "pipeline
rerun exited non-zero", and the container holding the evidence was
already gone.

A researcher meeting that has nothing to act on and nowhere to look:
they cannot re-run the shadow, cannot read its logs, and cannot tell a
missing dependency from a genuine scientific divergence
(researcher-reported, 2026-09-01).

WHAT IS KEPT, AND WHY SO LITTLE
-------------------------------

The failing step's label, its name, its exit code, and a bounded TAIL
of the output around it. Not the whole log: this record is embedded in
``l3_attestation.json``, which is committed, published to Zenodo, and
read by strangers. An unbounded log would put a researcher's entire
console output -- paths, hostnames, anything a traceback picked up --
into a public artefact. The tail is the part that says what broke.

Only the FIRST failure is kept. Later steps in a broken pipeline fail
because the first one did, and naming the last failure would point the
researcher at the symptom furthest from the cause.

A pipeline can also fail WITHOUT any step failing. Preflight validation
runs before the first step and refuses the whole run -- a missing
script, a step directory that is not there, duplicate step ids -- and
emits ``preflightFailed`` rather than ``stepFail``. Watching only for
step failures left ``dictRerunFailure`` empty for exactly that case,
which is how a researcher came to see "pipeline rerun exited non-zero"
and nothing else on a run that never started (2026-09-01). Every kind
carries ``sKind`` so a reader can tell "a step ran and failed" from
"the run was refused before anything ran" -- they are different
problems with different fixes.
"""

__all__ = [
    "I_MAX_OUTPUT_TAIL_LINES",
    "S_FAILURE_KIND_PREFLIGHT",
    "S_FAILURE_KIND_STEP",
    "ftBuildRerunDiagnosticsCollector",
]


I_MAX_OUTPUT_TAIL_LINES = 40

# The status events this collector understands. Any other event type is
# forwarded untouched: this is an observer on the pipeline's stream, not
# a participant in it.
_S_EVENT_STEP_STARTED = "stepStarted"
_S_EVENT_STEP_FAILED = "stepFail"
_S_EVENT_OUTPUT_LINE = "output"
_S_EVENT_OUTPUT_BATCH = "outputBatch"
_S_EVENT_PREFLIGHT_FAILED = "preflightFailed"

# What kind of failure the record describes. A reader must be able to
# tell a step that ran and failed from a run that was refused before
# anything ran: the first is a scientific or environmental problem in
# the step, the second is a workflow that could not start at all.
S_FAILURE_KIND_STEP = "step"
S_FAILURE_KIND_PREFLIGHT = "preflight"


def ftBuildRerunDiagnosticsCollector(dictWorkflow, fnForwardTo=None):
    """Return ``(fnStatusCallback, dictDiagnostics)`` for one rerun.

    The callback is what the pipeline runner is given. It records into
    ``dictDiagnostics`` and then forwards every event to ``fnForwardTo``
    when one is supplied, so a caller that wants live progress -- the
    CLI prints it -- keeps getting it. Forwarding is unconditional and
    happens even for events this collector reads, because an observer
    that swallowed the events it understood would silently break the
    caller it was added beside.

    ``dictDiagnostics`` is filled in place and is empty until something
    fails, which is the shape the attestation writer wants: an empty
    dict means "no step reported a failure", never "we did not look".
    """
    dictDiagnostics = {}
    dictProgress = {"iCurrentStep": 0, "listRecentLines": []}

    async def fnCollectStatusEvent(dictEvent):
        _fnRecordEvent(dictWorkflow, dictEvent, dictProgress,
                       dictDiagnostics)
        if fnForwardTo is not None:
            await fnForwardTo(dictEvent)

    return fnCollectStatusEvent, dictDiagnostics


def _fnRecordEvent(dictWorkflow, dictEvent, dictProgress,
                   dictDiagnostics):
    """Fold one status event into the progress and failure records."""
    sType = (dictEvent or {}).get("sType") or ""
    if sType == _S_EVENT_STEP_STARTED:
        dictProgress["iCurrentStep"] = dictEvent.get("iStepNumber") or 0
        dictProgress["listRecentLines"] = []
        return
    if sType in (_S_EVENT_OUTPUT_LINE, _S_EVENT_OUTPUT_BATCH):
        _fnAppendOutputLines(dictEvent, dictProgress)
        return
    if dictDiagnostics:
        return
    if sType == _S_EVENT_PREFLIGHT_FAILED:
        dictDiagnostics.update(_fdictDescribePreflightFailure(dictEvent))
        return
    if sType == _S_EVENT_STEP_FAILED:
        dictDiagnostics.update(_fdictDescribeFailure(
            dictWorkflow, dictEvent, dictProgress,
        ))


def _fnAppendOutputLines(dictEvent, dictProgress):
    """Keep only the most recent lines, so the tail stays bounded."""
    listLines = dictEvent.get("listLines")
    if listLines is None:
        sLine = dictEvent.get("sLine")
        listLines = [sLine] if sLine is not None else []
    listRecent = dictProgress["listRecentLines"]
    listRecent.extend(str(sLine) for sLine in listLines)
    if len(listRecent) > I_MAX_OUTPUT_TAIL_LINES:
        dictProgress["listRecentLines"] = listRecent[
            -I_MAX_OUTPUT_TAIL_LINES:
        ]


def _fdictDescribePreflightFailure(dictEvent):
    """Return the record of a run refused before any step executed.

    There is no step to name and no output to tail: preflight reports
    its own errors and nothing has run. Every error is kept, not just
    the first, because they are independent findings about different
    steps rather than a cascade from one cause.
    """
    return {
        "sKind": S_FAILURE_KIND_PREFLIGHT,
        "listErrors": [
            str(sError) for sError in dictEvent.get("listErrors") or []
        ],
        "listOutputTail": [],
    }


def _fdictDescribeFailure(dictWorkflow, dictEvent, dictProgress):
    """Return the record of the first step failure, in label terms.

    The label, not the raw step number: labels are what a researcher
    reads on the dashboard and what every error message in vaibify
    speaks, and the translation is per-type sequential rather than
    positional -- see ``pipelineUtils``.
    """
    from vaibify.gui.pipelineUtils import fsLabelFromStepIndex

    iStepNumber = dictEvent.get("iStepNumber") or (
        dictProgress["iCurrentStep"]
    )
    iStepIndex = max(int(iStepNumber) - 1, 0)
    listSteps = (dictWorkflow or {}).get("listSteps") or []
    dictStep = listSteps[iStepIndex] if iStepIndex < len(listSteps) else {}
    return {
        "sKind": S_FAILURE_KIND_STEP,
        "sStepLabel": (
            fsLabelFromStepIndex(dictWorkflow, iStepIndex)
            if iStepIndex < len(listSteps) else ""
        ),
        "sStepName": dictStep.get("sName", ""),
        "iExitCode": int(dictEvent.get("iExitCode") or 0),
        "listOutputTail": list(dictProgress["listRecentLines"]),
    }
