"""Tests for ``vaibify/reproducibility/reproduceScriptGenerator.py``.

The renderer is a pure function so the tests primarily compare its
output against fixtures. The write helper now routes through a
docker connection because ``sProjectRepo`` is a container path; the
host filesystem must NEVER receive ``reproduce.sh`` even when a path
collision exists.
"""

import os
import shutil
import subprocess

import pytest

from vaibify.reproducibility.reproduceScriptGenerator import (
    S_REPRODUCE_SCRIPT_FILENAME,
    _S_HEREDOC_DELIMITER,
    flistRenderStepCommands,
    fnGenerateReproduceScript,
    fsRenderReproduceScript,
)


def _fbHostShellParsesCleanly(sScript, tmp_path):
    """Return True iff ``bash -n`` accepts the whole script.

    ``bash -n`` parses without executing. It is a genuine breakout
    oracle: with the pre-fix ``bash -c '...'`` host wrapper, a single
    quote inside a step command or name closed the host argument and
    left an unbalanced quote, so ``bash -n`` failed. With the quoted
    heredoc the body is opaque host data, so a clean parse proves the
    hostile content never reached the host shell.
    """
    sPath = tmp_path / "reproduce_under_test.sh"
    sPath.write_text(sScript, encoding="utf-8")
    tResult = subprocess.run(
        ["bash", "-n", str(sPath)],
        capture_output=True, text=True,
    )
    return tResult.returncode == 0


_bBashAvailable = shutil.which("bash") is not None
_skipWithoutBash = pytest.mark.skipif(
    not _bBashAvailable, reason="bash required for the parse oracle",
)


class _FakeDockerConnection:
    """Capture container-side write + chmod calls in dict form."""

    def __init__(self):
        self.dictWritten = {}
        self.listCommands = []

    def fnWriteFile(self, sContainerId, sFilePath, baContent):
        self.dictWritten[(sContainerId, sFilePath)] = baContent

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append((sContainerId, sCommand))
        return (0, "")


def _fdictBuildWorkflow(listSteps):
    """Return a synthetic workflow dict with the supplied steps."""
    return {"listSteps": listSteps}


def test_empty_workflow_renders_preamble_and_epilogue():
    sScript = fsRenderReproduceScript(_fdictBuildWorkflow([]))
    assert "#!/usr/bin/env bash" in sScript
    assert "docker pull" in sScript
    assert "sha256sum -c MANIFEST.sha256" in sScript


def test_step_commands_appear_in_render():
    dictWorkflow = _fdictBuildWorkflow([
        {"sName": "S1", "sDirectory": "src",
         "saDataCommands": ["python compute.py"]},
        {"sName": "S2", "sDirectory": "plots",
         "saPlotCommands": ["python plot.py"]},
    ])
    sScript = fsRenderReproduceScript(dictWorkflow)
    assert "python compute.py" in sScript
    assert "python plot.py" in sScript
    assert "src" in sScript
    assert "plots" in sScript


def test_step_with_no_commands_is_skipped():
    dictWorkflow = _fdictBuildWorkflow([
        {"sName": "AI Decl", "sStepKind": "ai-declaration"},
    ])
    listLines = flistRenderStepCommands(dictWorkflow)
    assert listLines == []


def test_repeated_render_is_deterministic():
    dictWorkflow = _fdictBuildWorkflow([
        {"sName": "A", "sDirectory": ".",
         "saDataCommands": ["echo 1"]},
    ])
    sFirst = fsRenderReproduceScript(dictWorkflow)
    sSecond = fsRenderReproduceScript(dictWorkflow)
    assert sFirst == sSecond


def test_render_handles_directory_with_single_quote():
    dictWorkflow = _fdictBuildWorkflow([
        {"sName": "X", "sDirectory": "weird'dir",
         "saDataCommands": ["echo hi"]},
    ])
    sScript = fsRenderReproduceScript(dictWorkflow)
    assert "weird'\\''dir" in sScript


def test_generate_writes_into_container_not_host(tmp_path):
    """The script must land in the container; never on the host."""
    dictWorkflow = _fdictBuildWorkflow([])
    fakeConnection = _FakeDockerConnection()
    sContainerRepo = "/workspace/foo"
    sReturned = fnGenerateReproduceScript(
        sContainerRepo, dictWorkflow,
        connectionDocker=fakeConnection,
        sContainerId="cid-xyz",
    )
    sExpectedPath = sContainerRepo + "/" + S_REPRODUCE_SCRIPT_FILENAME
    assert sReturned == sExpectedPath
    assert ("cid-xyz", sExpectedPath) in fakeConnection.dictWritten
    baContent = fakeConnection.dictWritten[("cid-xyz", sExpectedPath)]
    assert b"#!/usr/bin/env bash" in baContent


def test_generate_chmods_inside_container(tmp_path):
    """Executable bits must be set via docker exec, not host chmod."""
    dictWorkflow = _fdictBuildWorkflow([])
    fakeConnection = _FakeDockerConnection()
    fnGenerateReproduceScript(
        "/workspace/foo", dictWorkflow,
        connectionDocker=fakeConnection,
        sContainerId="cid-xyz",
    )
    listChmodCommands = [
        sCommand
        for _, sCommand in fakeConnection.listCommands
        if "chmod" in sCommand
    ]
    assert listChmodCommands
    assert "a+x" in listChmodCommands[0]


def test_generate_never_writes_to_host_at_container_path(tmp_path):
    """Regression: never create a host file at /workspace/<...>."""
    sHostShadow = "/workspace/foo/" + S_REPRODUCE_SCRIPT_FILENAME
    bExistedBefore = os.path.exists(sHostShadow)
    dictWorkflow = _fdictBuildWorkflow([])
    fakeConnection = _FakeDockerConnection()
    fnGenerateReproduceScript(
        "/workspace/foo", dictWorkflow,
        connectionDocker=fakeConnection,
        sContainerId="cid-xyz",
    )
    bExistsAfter = os.path.exists(sHostShadow)
    assert bExistsAfter == bExistedBefore


def test_generate_refuses_when_docker_connection_missing():
    """Caller must provide a docker connection — no host fallback."""
    dictWorkflow = _fdictBuildWorkflow([])
    with pytest.raises(ValueError):
        fnGenerateReproduceScript("/workspace/foo", dictWorkflow)


def test_generate_refuses_when_container_id_empty():
    """Empty sContainerId must be rejected, not silently fall back."""
    dictWorkflow = _fdictBuildWorkflow([])
    fakeConnection = _FakeDockerConnection()
    with pytest.raises(ValueError):
        fnGenerateReproduceScript(
            "/workspace/foo", dictWorkflow,
            connectionDocker=fakeConnection,
            sContainerId="",
        )


# -----------------------------------------------------------------------
# Shell-injection hardening (2026-07-09 security audit): the body was
# emitted inside a host-side ``bash -c '...'`` single-quoted argument,
# so a single quote in a step command — or a workflow-controlled step
# name — closed that argument and executed on the reproducer's HOST.
# The body now travels to the container via a quoted heredoc, which the
# host shell never interprets.
# -----------------------------------------------------------------------


def test_body_is_delivered_via_quoted_heredoc_not_host_bash_c():
    """The reproduction body must reach the container through a quoted
    heredoc fed to ``bash -s`` on stdin, never through a host-side
    ``bash -c '...'`` argument (the injection sink)."""
    sScript = fsRenderReproduceScript(_fdictBuildWorkflow([
        {"sName": "S", "sDirectory": "src",
         "saDataCommands": ["python run.py"]},
    ]))
    assert "<<'" + _S_HEREDOC_DELIMITER + "'" in sScript, (
        "the body must be delivered through a quoted heredoc"
    )
    assert "bash -s" in sScript
    assert "docker run --rm -i " in sScript, (
        "stdin must be attached (-i) so bash -s reads the heredoc"
    )
    assert "bash -c '" not in sScript, (
        "the host-side bash -c single-quote wrapper is the "
        "confirmed injection sink and must not return"
    )


@_skipWithoutBash
def test_hostile_step_name_cannot_break_out_of_host_shell(tmp_path):
    """A step name carrying a single-quote breakout payload must stay
    confined to its inert ``# Step:`` comment; the host shell must
    still parse the whole script cleanly."""
    sScript = fsRenderReproduceScript(_fdictBuildWorkflow([
        {"sName": "x'; touch /tmp/pwned; echo '",
         "sDirectory": "d", "saDataCommands": ["python run.py"]},
    ]))
    assert _fbHostShellParsesCleanly(sScript, tmp_path), (
        "hostile step name broke out of the host shell — the pre-fix "
        "bash -c wrapper regressed"
    )
    # The payload survives only as a comment, never as live script.
    for sLine in sScript.splitlines():
        if "touch /tmp/pwned" in sLine:
            assert sLine.lstrip().startswith("# Step:"), (
                "the step-name payload escaped its comment line"
            )


@_skipWithoutBash
def test_hostile_command_cannot_break_out_of_host_shell(tmp_path):
    """A step command with an unbalanced single quote is delivered to
    the container verbatim; the host shell must not try to parse it
    (which, pre-fix, produced an unterminated-quote error)."""
    sScript = fsRenderReproduceScript(_fdictBuildWorkflow([
        {"sName": "S", "sDirectory": "d",
         "saDataCommands": ["echo '; rm -rf ~ #"]},
    ]))
    assert _fbHostShellParsesCleanly(sScript, tmp_path), (
        "hostile command broke out of the host shell"
    )


def test_legitimate_single_quoted_command_is_delivered_literally():
    """A common, legitimate command containing single quotes (e.g. "
    ``python -c '...'``) must survive verbatim — the fix must not
    shell-mangle real commands, only stop them reaching the host."""
    sCommand = "python -c 'import sys; print(sys.version)'"
    sScript = fsRenderReproduceScript(_fdictBuildWorkflow([
        {"sName": "S", "sDirectory": ".",
         "saDataCommands": [sCommand]},
    ]))
    assert sCommand in sScript, (
        "a legitimate single-quoted command was corrupted"
    )


def test_step_name_newline_is_flattened_to_one_comment_line():
    """A newline in a step name must be collapsed so it cannot open a
    new line of live script inside the heredoc body."""
    sScript = fsRenderReproduceScript(_fdictBuildWorkflow([
        {"sName": "before\ntouch /tmp/injected", "sDirectory": ".",
         "saDataCommands": ["python run.py"]},
    ]))
    listStepComments = [
        sLine for sLine in sScript.splitlines()
        if sLine.startswith("# Step:")
    ]
    assert listStepComments == ["# Step: before touch /tmp/injected"], (
        "the newline was not flattened into a single comment line"
    )
    assert "\ntouch /tmp/injected" not in sScript, (
        "the post-newline fragment became its own script line"
    )


def test_step_name_control_characters_are_stripped():
    """Escape and tab characters in a name collapse to spaces so the
    comment cannot carry terminal escapes or hidden structure."""
    sScript = fsRenderReproduceScript(_fdictBuildWorkflow([
        {"sName": "a\x1b[31m\tb", "sDirectory": ".",
         "saDataCommands": ["python run.py"]},
    ]))
    assert "# Step: a [31m b" in sScript, (
        "control characters were not neutralized"
    )
    assert "\x1b" not in sScript


def test_delimiter_forgery_in_command_is_rejected():
    """A command occupying a line equal to the heredoc terminator
    would close the heredoc early and inject onto the host; emitting
    such a script must fail loud instead."""
    with pytest.raises(ValueError):
        fsRenderReproduceScript(_fdictBuildWorkflow([
            {"sName": "S", "sDirectory": ".",
             "saDataCommands": [_S_HEREDOC_DELIMITER]},
        ]))


def test_ordinary_script_has_exactly_one_terminator_line():
    """Sanity: a benign render carries exactly one terminator line, so
    the forgery guard's invariant holds on the happy path."""
    sScript = fsRenderReproduceScript(_fdictBuildWorkflow([
        {"sName": "S", "sDirectory": "src",
         "saDataCommands": ["python run.py"]},
    ]))
    iTerminators = sum(
        1 for sLine in sScript.splitlines()
        if sLine == _S_HEREDOC_DELIMITER
    )
    assert iTerminators == 1
