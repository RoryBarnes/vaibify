"""A failing step names its cause, not just a number.

Exit 127 is what a container-authored command produces the first time
it runs on a host: the image has ``python`` on PATH and a stock Debian
or Ubuntu does not — they ship ``python3`` and no ``python``. The
dashboard reported "Exit code: 127", which is precise and useless to a
researcher who did not grow up on POSIX (researcher-reported,
2026-09-04, driving the shipped example on Ubuntu).

The explanation is stamped ONCE, on the event, rather than rendered by
each surface: the dashboard, the run log and the CLI all report this
failure, and a diagnosis written three times is three diagnoses that
drift.
"""

import pytest

from vaibify.gui.pipelineUtils import fsExplainExitCode


def test_command_not_found_names_the_missing_program():
    """127 must name the program, or the reader still has to guess."""
    sExplanation = fsExplainExitCode(127, "solver analysis.in", True)
    assert "solver" in sExplanation
    assert "127" in sExplanation
    assert "PATH" in sExplanation


@pytest.mark.parametrize("sCommand,sProgram", [
    ("Rscript fit.R", "Rscript"),
    ("julia model.jl", "julia"),
    ("gfortran -O2 solver.f90", "gfortran"),
    ("make all", "make"),
])
def test_the_message_is_ecosystem_neutral(sCommand, sProgram):
    """It names the program that was invoked, and nothing else.

    An earlier draft appended a hint about Debian shipping ``python3``
    and no ``python`` -- true, the most common single cause, and still
    wrong here: vaibify is for containerized scientific workflows in
    general, and a researcher whose missing command is ``Rscript`` or
    a compiled model would have been handed advice about a language
    they are not using (researcher-reported, 2026-09-04).

    Kills reintroducing any language-specific remedy: the assertion
    that "python" appears nowhere fails the moment one comes back.
    """
    sExplanation = fsExplainExitCode(127, sCommand, True)
    assert sProgram in sExplanation
    assert "python" not in sExplanation.lower(), sExplanation


def test_a_path_form_program_is_not_described_as_a_path_lookup():
    """`./model` was never a PATH lookup, so PATH advice is wrong.

    ``which`` searches PATH and says nothing useful about a relative
    path, and telling a researcher to check PATH sends them after a
    problem they do not have. Scientific workflows invoke compiled
    models this way constantly.

    Kills collapsing the two shapes into one message.
    """
    sExplanation = fsExplainExitCode(127, "./solver input.in", True)
    assert "./solver" in sExplanation
    assert "PATH" not in sExplanation, sExplanation
    assert "which" not in sExplanation, sExplanation


def test_the_two_modes_offer_different_remedies():
    """Install-it-locally and add-it-to-the-image are not the same act.

    A researcher told to rebuild an image for a host project has no
    image; one told to install locally for a container project would
    install it where the step will never look.
    """
    sHost = fsExplainExitCode(127, "Rscript fit.R", True)
    sContainer = fsExplainExitCode(127, "Rscript fit.R", False)
    assert "install it or put it on PATH" in sHost
    assert "rebuild" not in sHost, sHost
    assert "rebuild the image" in sContainer


def test_host_and_container_say_where_the_search_happened():
    """The mode must not be invisible at the moment it decides meaning.

    Telling a researcher their CONTAINER lacks a program when the
    search happened on their laptop sends them into the wrong
    filesystem.
    """
    sHost = fsExplainExitCode(127, "solver x.in", True)
    sContainer = fsExplainExitCode(127, "solver x.in", False)
    assert "on this machine" in sHost
    assert "in the container" not in sHost
    assert "in the container" in sContainer
    assert "on this machine" not in sContainer


def test_an_ordinary_failure_gets_no_invented_cause():
    """Silence where there is nothing standard to say.

    Exit 1 is the researcher's own program failing, and its output is
    the diagnosis. A guess from vaibify would talk over it — the same
    reason a refusal names its cause instead of pointing at a tab.
    """
    assert fsExplainExitCode(1, "solver analysis.in", True) == ""
    assert fsExplainExitCode(2, "solver analysis.in", False) == ""


def test_a_signal_kill_is_named_without_claiming_a_cause():
    """137 is SIGKILL. It is NOT proof of an out-of-memory kill.

    vaibify cannot distinguish an OOM kill from any other SIGKILL, so
    the sentence names the signal and says it does not name a cause.
    Asserting the hedge because dropping it is how "killed by signal"
    becomes "you ran out of memory" — a confident claim nobody
    verified.
    """
    sExplanation = fsExplainExitCode(137, "solver big.in", True)
    assert "SIGKILL" in sExplanation
    assert "not a cause" in sExplanation


def test_a_large_exit_code_is_not_read_as_a_signal():
    """128+N only encodes a signal for real signal numbers.

    Without the upper bound, exit 200 would be reported as "killed by
    signal 72", inventing a cause from arithmetic.
    """
    assert fsExplainExitCode(200, "solver x.in", True) == ""


@pytest.mark.parametrize("valueOdd", [None, "127", 0.0])
def test_a_non_integer_exit_code_explains_nothing(valueOdd):
    assert fsExplainExitCode(valueOdd, "solver x.in", True) == ""


def test_a_missing_command_still_explains_the_code():
    """The code is knowable even when the command text is not."""
    sExplanation = fsExplainExitCode(127, "", True)
    assert "the program" in sExplanation
