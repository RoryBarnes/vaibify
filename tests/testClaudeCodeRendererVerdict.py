"""The renderer canary's verdict, pinned on every pull request.

``tests/testClaudeCodeRendererLive.py`` runs nightly against the real
CLI, so a mistake in how it READS the terminal output would only show
as a canary that never fails. Two such mistakes were found in review:
a byte match for ``ESC[?1049h`` passed the equivalent combined sequence
``ESC[?1049;1006h``, and a missing prompt was judged inconclusive
BEFORE the alternate screen was looked at, so output that plainly
entered it was skipped rather than failed. These tests drive the same
verdict function the canary uses, on hand-written terminal output.

Kills (each recorded in tests/falsificationRegistry.py):
  - matching the exact bytes instead of parsing the modes;
  - judging the missing prompt before the alternate screen.
"""

import pytest

from tests.testClaudeCodeRendererLive import (
    S_VERDICT_ENTERED_ALTERNATE_SCREEN,
    S_VERDICT_NEVER_REACHED_PROMPT,
    S_VERDICT_STAYED_CLASSIC,
    fsJudgeTheSwitchedRun,
)


@pytest.mark.falsification
def test_a_combined_mode_sequence_counts_as_the_alternate_screen():
    """One sequence setting 1049 among other modes still fails the run.

    Kills: matching the exact bytes ESC[?1049h instead of parsing modes.
    """
    baOutput = b"\x1b[?2004h\x1b[?1049;1006h\x1b[2J"
    assert fsJudgeTheSwitchedRun(baOutput) == (
        S_VERDICT_ENTERED_ALTERNATE_SCREEN)


@pytest.mark.falsification
def test_the_alternate_screen_fails_the_run_without_a_prompt():
    """Entering the alternate screen fails even if the prompt never came.

    Kills: judging the missing prompt (inconclusive) before the
    alternate screen (failure).
    """
    assert fsJudgeTheSwitchedRun(b"\x1b[?1049h\x1b[2J") == (
        S_VERDICT_ENTERED_ALTERNATE_SCREEN)


@pytest.mark.parametrize("sMode", ["47", "1047"])
def test_the_other_alternate_buffer_modes_count(sMode):
    """xterm switches buffers on 47 and 1047 too, not only on 1049."""
    baOutput = b"\x1b[?2004h\x1b[?" + sMode.encode("ascii") + b"h"
    assert fsJudgeTheSwitchedRun(baOutput) == (
        S_VERDICT_ENTERED_ALTERNATE_SCREEN)


def test_a_prompt_on_the_normal_screen_stays_classic():
    """The measured classic output of Claude Code 2.1.311 passes."""
    baOutput = b"\x1b[?25l\x1b[?2004h\x1b[?2031h\x1b[?1004h> "
    assert fsJudgeTheSwitchedRun(baOutput) == S_VERDICT_STAYED_CLASSIC


def test_output_with_neither_is_inconclusive():
    """No alternate screen and no prompt says nothing either way."""
    assert fsJudgeTheSwitchedRun(b"Please log in\r\n") == (
        S_VERDICT_NEVER_REACHED_PROMPT)
