"""The stored build tail keeps the step's own output through BuildKit's echo.

BuildKit ends a failed build by echoing the whole failing step: the
separators, a ``> [2/2] RUN ...`` header, a ``Dockerfile:23`` locator
and every source line as ``23 | >>> ...``. For a long RUN that is
sixty lines, and a single fifty-line window held only the echo while
``mkdir: cannot create directory: No space left on device``, printed
three lines before it, had been evicted (a live build, 2026-09-21).
The catalog then had nothing to read and the overlay's banner blamed
the network.
"""

from types import SimpleNamespace

import pytest

from vaibify.docker import imageBuilder


def _flistPostMortem(iSourceLines):
    listLines = [
        "------\n",
        " > [2/2] RUN if ! curl -fsSL https://host.example/install.sh; then exit 1; fi:\n",
        "------\n",
        "Dockerfile.claude:23\n",
        "--------------------\n",
    ]
    listLines.extend(
        f"  {23 + iIndex} | >>>         printf '%s\\n' \"line {iIndex}\" >&2; \\\n"
        for iIndex in range(iSourceLines)
    )
    listLines.append("--------------------\n")
    listLines.append("ERROR: failed to solve: process did not complete successfully: exit code: 1\n")
    return listLines


def _fsCapture(listLines):
    return imageBuilder._fsStreamAndCaptureStderr(
        SimpleNamespace(stderr=iter(listLines)),
    )


@pytest.mark.falsification
def test_the_reason_survives_a_post_mortem_longer_than_the_window():
    """Kills: treating no line as post-mortem, so one window holds
    everything and the echo evicts the reason as it did before."""
    listLines = [
        "#7 0.402 mkdir: cannot create directory '/home/user/.claude': No space left on device\n",
        "#7 0.418 \n",
        "#7 0.418 vaibify build (claude overlay): Claude Code installer failed.\n",
    ] + _flistPostMortem(imageBuilder._I_BUILD_STDERR_TAIL_LINES + 20)
    sTail = _fsCapture(listLines)
    assert "No space left on device" in sTail
    assert "ERROR: failed to solve" in sTail


def test_the_post_mortem_is_still_kept_after_the_output():
    """The echo is evidence too; the modal shows it after the output."""
    listLines = ["#7 0.1 pip says no\n"] + _flistPostMortem(3)
    sTail = _fsCapture(listLines)
    assert sTail.index("pip says no") < sTail.index("Dockerfile.claude:23")
    assert sTail.endswith("exit code: 1\n")


def test_output_lines_are_never_read_as_post_mortem():
    for sLine in (
        "#7 0.402 mkdir: cannot create directory: No space left on device\n",
        "0.418   1. Disable the Claude overlay (features: { claude: false }).\n",
        "Step 3/9 : RUN apt-get install\n",
        "E: Version '1.0' for 'gcc' was not found\n",
    ):
        assert not imageBuilder._fbLineIsBuildKitPostMortem(sLine), sLine
    for sLine in _flistPostMortem(2):
        assert imageBuilder._fbLineIsBuildKitPostMortem(sLine), sLine
