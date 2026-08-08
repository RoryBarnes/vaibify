"""Container-path preview commands must not permit shell injection.

The npy/hdf5 preview and the file fetch build a ``python3 -c`` command
around a path taken from the workflow (saOutputDataFiles / saPlotFiles
in project.json). The paths once went into a double-quoted ``bash -c``
string via ``repr()``, which is Python escaping, not shell escaping —
``$(...)`` and unbalanced quotes in a crafted filename executed on the
host-invoked exec, triggered merely by the dashboard previewing or
fetching the file, before any step ran.

These tests capture the actual command string the code would send to
the container and execute it through a real ``/bin/bash -c`` in a
scratch directory. If the injection is live, the payload's sentinel
file appears; with correct quoting it never does. This is a real
falsification of the exploit, not an assertion about the string's
shape.
"""

import subprocess
from unittest.mock import patch

import pytest

from vaibify.gui import dataPreview
from vaibify.docker.dockerConnection import DockerConnection, ExecResult


_LIST_INJECTION_PATHS = [
    "/data/a$(touch INJECTED)b.npy",
    '/data/a"; touch INJECTED; echo "b.npy',
    "/data/a`touch INJECTED`b.npy",
    "/data/a$(touch INJECTED)b.h5",
]


class _ConnectionCapturing:
    """Docker stand-in that records the command and returns empty output."""

    def __init__(self):
        self.sCommand = ""

    def ftResultExecuteCommand(self, sContainerId, sCommand, **dictKwargs):
        self.sCommand = sCommand
        return (1, "")


def _fbSentinelSurvivesBash(sCommand, pathScratch):
    """Run sCommand through bash in pathScratch; True iff no INJECTED file.

    ``python3`` may or may not be present, and the program will fail to
    load the bogus path either way — what matters is only whether bash
    evaluated the embedded payload, which a correctly single-quoted
    argument prevents.
    """
    subprocess.run(
        ["/bin/bash", "-c", sCommand],
        cwd=str(pathScratch),
        capture_output=True,
        timeout=15,
    )
    return not (pathScratch / "INJECTED").exists()


@pytest.mark.parametrize("sPath", _LIST_INJECTION_PATHS)
def test_preview_command_does_not_execute_injected_payload(
    sPath, tmp_path,
):
    conn = _ConnectionCapturing()
    dataPreview.fsPreviewDataFile(conn, "cid", sPath, "/data")
    assert conn.sCommand, "no command was built"
    assert _fbSentinelSurvivesBash(conn.sCommand, tmp_path), (
        f"path {sPath!r} broke out of the preview command: "
        f"INJECTED was created"
    )


@pytest.mark.falsification
def test_npy_preview_quotes_the_whole_program(tmp_path):
    """A crafted .npy path must not execute on preview.

    Kills: In dataPreview._fsPreviewNpy, build the command as
    ``"python3 -c \\"" + ... + "\\""`` (double-quoted bash string with
    the repr'd path embedded) instead of
    ``"python3 -c " + fsShellQuote(sProgram)``.
    """
    conn = _ConnectionCapturing()
    dataPreview.fsPreviewDataFile(
        conn, "cid", "/data/x$(touch INJECTED)y.npy", "/data",
    )
    assert _fbSentinelSurvivesBash(conn.sCommand, tmp_path)


@pytest.mark.falsification
def test_file_fetch_does_not_execute_injected_payload(tmp_path):
    """A crafted file path must not execute when fetched.

    fbaFetchFile builds a base64 read command around the path; the path
    reaches it from figure/output fetches. Same repr-into-bash-c hole.

    Kills: In dockerConnection.DockerConnection.fbaFetchFile, build the
    command as ``"python3 -c \\"..." + repr(sFilePath) + ...\\""`` (the
    old double-quoted form) instead of
    ``"python3 -c " + shlex.quote(sProgram)``.
    """
    with patch(
        "vaibify.docker.dockerConnection._fmoduleGetDocker",
    ):
        conn = DockerConnection()
    dictCaptured = {}

    def _fCapture(sContainerId, sCommand, **dictKwargs):
        dictCaptured["sCommand"] = sCommand
        return ExecResult(iExitCode=0, sStdout="", sStderr="")

    conn.ftRunInContainerStreamed = _fCapture
    conn.fbaFetchFile("cid", "/workspace/fig/a$(touch INJECTED)b.png")
    assert _fbSentinelSurvivesBash(dictCaptured["sCommand"], tmp_path), (
        "file fetch path broke out: INJECTED was created"
    )


def test_preview_still_previews_a_benign_path(tmp_path):
    """The quoting change must not break an ordinary preview command."""
    pathReal = tmp_path / "sample.txt"
    pathReal.write_text("line one\nline two\n")
    conn = _ConnectionCapturing()
    dataPreview.fsPreviewDataFile(conn, "cid", str(pathReal), "/data")
    # The text-preview path shell-quotes with the same helper; running
    # its command in bash should read the file, not error on quoting.
    result = subprocess.run(
        ["/bin/bash", "-c", conn.sCommand],
        capture_output=True, text=True, timeout=15,
    )
    assert "line one" in result.stdout
