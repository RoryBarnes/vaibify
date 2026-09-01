"""A missing step directory must be reported once, not once per command.

Pre-flight validates a step's directory and then each of its commands.
The command check runs ``cd <dir> && test -f <script>``, so when the
directory is absent EVERY command in that step reports "command not
found" — consequences of the one fault above, printed as though they
were independent problems.

A researcher met that as twelve near-identical lines describing two
broken directories, including three copies of ``command not found:
pytest`` per step (2026-09-01). Every line after the first was noise,
and the noise buried the two lines that mattered. Reporting the cause
and stopping is what makes the list actionable.

The opposite error matters too and is asserted here: a step whose
directory is FINE must still have its commands checked. Pre-flight
exists to catch a missing script before a long run starts, and a
suppression that reached too far would switch that protection off.
"""

import pytest

from vaibify.gui.pipelineRunner import _flistPreflightValidate


class _ConnectionAnsweringDirectories:
    """A fake docker that reports which paths exist.

    Answers the directory probe from ``setPresentDirectories`` and the
    command probe by whether its ``cd`` target is present — the same
    coupling the real shell has, which is the whole reason the cascade
    exists.
    """

    def __init__(self, setPresentDirectories):
        self.setPresentDirectories = setPresentDirectories
        self.listCommandChecks = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        del sContainerId
        if sCommand.startswith("test -d "):
            sPath = sCommand.split("'")[1]
            if sPath in self.setPresentDirectories:
                return 0, "ok\n"
            return 0, "missing\n"
        # Every command probe answers "not found", so a step whose
        # directory IS sound still produces an error. Without that the
        # falsification below could not tell "the commands were
        # checked" from "the check was skipped and found nothing".
        self.listCommandChecks.append(sCommand)
        return 1, ""


def _fdictWorkflow():
    return {"listSteps": [
        {
            "sName": "Broken", "sDirectory": "Broken",
            "sStepId": "broken",
            "saDataCommands": ["python makeData.py"],
            "saTestCommands": ["pytest", "pytest", "pytest"],
            "saPlotCommands": ["python makePlot.py"],
        },
        {
            "sName": "Sound", "sDirectory": "Sound",
            "sStepId": "sound",
            "saDataCommands": ["python makeData.py"],
        },
    ]}


def _flistValidate(setPresentDirectories):
    import asyncio
    connectionFake = _ConnectionAnsweringDirectories(setPresentDirectories)
    listErrors = asyncio.run(_flistPreflightValidate(
        connectionFake, "cid", _fdictWorkflow(),
        {"sRepoRoot": "/repo"},
    ))
    return listErrors, connectionFake


def test_a_missing_directory_reports_once_not_once_per_command():
    """The cause, not the five consequences of the cause."""
    listErrors, _ = _flistValidate({"/repo/Sound"})
    listBroken = [s for s in listErrors if "Broken" in s]
    assert len(listBroken) == 1, (
        "the missing directory is reported once per command in the "
        f"step, which buries it: {listBroken}"
    )
    assert "directory does not exist" in listBroken[0], listBroken


def test_the_commands_of_a_broken_step_are_never_probed():
    """Probing them costs a container round trip per command, for noise."""
    _, connectionFake = _flistValidate({"/repo/Sound"})
    assert not any(
        "/repo/Broken" in sCommand
        for sCommand in connectionFake.listCommandChecks
    ), connectionFake.listCommandChecks


@pytest.mark.falsification
def test_a_command_no_run_executes_is_never_validated():
    """Pre-flight must check what the RUN does, not everything declared.

    Kills: adding "saTestCommands" back to T_EXECUTED_COMMAND_KEYS —
    the probe fires for a test tool and the run is refused over a
    command no run path invokes.

    No run mode executes ``saTestCommands``: the runner runs setup,
    data, plot and generic commands, and tests are a separate action
    with its own lane. Validating them here refused one operation
    because a different one could not run, which cost a researcher
    four rounds of Level 3 verification over a missing ``pytest`` that
    ``reproduce.sh`` never calls.
    """
    _, connectionFake = _flistValidate({"/repo/Sound", "/repo/Broken"})
    assert not any(
        "pytest" in sCommand
        for sCommand in connectionFake.listCommandChecks
    ), connectionFake.listCommandChecks
    assert any(
        "makeData.py" in sCommand
        for sCommand in connectionFake.listCommandChecks
    ), connectionFake.listCommandChecks


@pytest.mark.falsification
def test_a_sound_directory_still_has_every_command_checked(
):
    """The suppression must not reach a step whose directory is fine.

    Kills: replacing the "did the directory check add an error"
    condition with an unconditional `continue` — every command check
    is skipped for every step, and pre-flight silently stops catching
    the missing scripts it exists to catch.
    """
    listErrors, connectionFake = _flistValidate({"/repo/Sound"})
    assert any(
        "/repo/Sound" in sCommand
        for sCommand in connectionFake.listCommandChecks
    ), connectionFake.listCommandChecks
    assert any(
        "Sound" in sError and "command not found" in sError
        for sError in listErrors
    ), listErrors
