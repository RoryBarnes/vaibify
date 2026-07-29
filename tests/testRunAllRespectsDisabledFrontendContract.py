"""Run All / Force Run All must not re-enable disabled steps.

Both dispatch paths once re-enabled every ``bRunEnabled === false``
step via ``fnToggleStepEnabled(iIndex, true)`` before sending the
action — contradicting the "Run all enabled steps?" prompt, persisting
the flip to project.json, and (since the flip is persisted) silently
clearing the Tier 5 reproduce refusal that names disabled steps. The
backend already honors bRunEnabled; the frontend was overriding it.

JavaScript is not executed by the Python suite; these are structural
assertions in the established frontend-contract pattern. The browser
lane separately proves the module loads and evaluates, and the backend
skip is covered by the pipeline-runner suite. The behavioural pairing
with the Tier 5 refusal is why this regression is guarded explicitly.
"""

import os

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadRunner():
    sPath = os.path.join(_sStaticDir, "scriptPipelineRunner.js")
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fsExtractFunctionBlock(sSource, sSignature):
    iStart = sSource.find(sSignature)
    assert iStart != -1, sSignature + " missing from source"
    iNext = sSource.find("\n    function ", iStart + 1)
    iNextAsync = sSource.find("\n    async function ", iStart + 1)
    iEnd = min(x for x in (iNext, iNextAsync, len(sSource)) if x != -1)
    return sSource[iStart:iEnd]


def test_run_all_does_not_toggle_step_enabled():
    """The Run All confirm callback must not re-enable disabled steps."""
    sBlock = _fsExtractFunctionBlock(_fsReadRunner(), "function fnRunAll(")
    assert "fnToggleStepEnabled" not in sBlock, (
        "Run All must not flip bRunEnabled; the backend skips disabled "
        "steps and the flip clears the Tier 5 refusal"
    )


def test_force_run_all_does_not_toggle_step_enabled():
    """Force Run All must not re-enable disabled steps either."""
    sBlock = _fsExtractFunctionBlock(
        _fsReadRunner(), "function _fnExecuteForceRunAll(",
    )
    assert "fnToggleStepEnabled" not in sBlock


def test_queue_helper_only_queues_enabled_steps():
    """The shared queueing helper must guard on bRunEnabled."""
    sBlock = _fsExtractFunctionBlock(
        _fsReadRunner(), "function _fnQueueEnabledSteps(",
    )
    assert "bRunEnabled !== false" in sBlock, (
        "only enabled steps may be marked queued"
    )
    assert "fnSetStepStatus" in sBlock


def test_run_all_uses_the_shared_queue_helper():
    """Both paths route through the single enabled-only queue helper."""
    sSource = _fsReadRunner()
    assert sSource.count("_fnQueueEnabledSteps()") >= 2, (
        "Run All and Force Run All must both use the enabled-only "
        "queue helper"
    )


def test_no_dispatch_path_still_re_enables_disabled_steps():
    """A belt check: the re-enable idiom appears nowhere in dispatch."""
    sSource = _fsReadRunner()
    assert "fnToggleStepEnabled(iIndex, true)" not in sSource, (
        "the persist-a-re-enable idiom must not reappear in the runner"
    )
