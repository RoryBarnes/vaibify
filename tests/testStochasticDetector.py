"""Tests for the static stochastic-RNG detector."""

import os

import pytest

from vaibify.testing.stochasticDetector import (
    ftDetectStochastic,
    fnPrintReport,
)


@pytest.fixture
def fixtureSeededScript(tmp_path):
    sPath = os.path.join(tmp_path, "seeded.py")
    with open(sPath, "w") as fileHandle:
        fileHandle.write(
            "import numpy as np\n"
            "np.random.seed(42)\n"
            "daSamples = np.random.normal(size=100)\n"
        )
    return sPath


@pytest.fixture
def fixtureUnseededScript(tmp_path):
    sPath = os.path.join(tmp_path, "unseeded.py")
    with open(sPath, "w") as fileHandle:
        fileHandle.write(
            "import numpy as np\n"
            "daSamples = np.random.normal(size=100)\n"
        )
    return sPath


@pytest.fixture
def fixtureNonStochasticScript(tmp_path):
    sPath = os.path.join(tmp_path, "deterministic.py")
    with open(sPath, "w") as fileHandle:
        fileHandle.write(
            "import numpy as np\n"
            "daZeros = np.zeros(10)\n"
        )
    return sPath


def test_ftDetectStochastic_seeded(fixtureSeededScript):
    bStoch, listSrc, listSeeds = ftDetectStochastic(fixtureSeededScript)
    assert bStoch is True
    assert any(bSeeded for _, _, bSeeded in listSrc)
    assert any("seed" in sSeed for sSeed in listSeeds)


def test_ftDetectStochastic_unseeded(fixtureUnseededScript):
    bStoch, listSrc, _ = ftDetectStochastic(fixtureUnseededScript)
    assert bStoch is True
    assert all(not bSeeded for _, _, bSeeded in listSrc)


def test_ftDetectStochastic_no_randomness(fixtureNonStochasticScript):
    bStoch, listSrc, _ = ftDetectStochastic(fixtureNonStochasticScript)
    assert bStoch is False
    assert listSrc == []


def test_ftDetectStochastic_ignores_commented_calls(tmp_path):
    """A randomness call inside a comment must not register as a source."""
    sPath = os.path.join(tmp_path, "commented.py")
    with open(sPath, "w") as fileHandle:
        fileHandle.write(
            "# np.random.normal(size=100)  -- example, not a real call\n"
            "x = 42\n"
        )
    bStoch, listSrc, _ = ftDetectStochastic(sPath)
    assert bStoch is False


def test_ftDetectStochastic_emcee_seeded(tmp_path):
    """emcee with np.random.seed counts as a seeded MCMC sampler."""
    sPath = os.path.join(tmp_path, "emceeSeeded.py")
    with open(sPath, "w") as fileHandle:
        fileHandle.write(
            "import emcee\n"
            "import numpy as np\n"
            "np.random.seed(123)\n"
            "sampler = emcee.EnsembleSampler(32, 5, lambda x: 0)\n"
        )
    bStoch, listSrc, _ = ftDetectStochastic(sPath)
    assert bStoch is True
    listMcmc = [t for t in listSrc if t[0] == "MCMC / Nested Sampler"]
    assert listMcmc, "expected emcee detection"
    assert all(bSeeded for _, _, bSeeded in listMcmc)


def test_fnPrintReport_runs_without_error(fixtureSeededScript, capsys):
    bStoch, listSrc, listSeeds = ftDetectStochastic(fixtureSeededScript)
    fnPrintReport(fixtureSeededScript, bStoch, listSrc, listSeeds)
    captured = capsys.readouterr()
    assert "Stochastic Detection Report" in captured.out


def test_fnPrintReport_no_stochastic_path(
    fixtureNonStochasticScript, capsys,
):
    """Report for a deterministic script prints the no-stochastic message."""
    bStoch, listSrc, listSeeds = ftDetectStochastic(fixtureNonStochasticScript)
    fnPrintReport(fixtureNonStochasticScript, bStoch, listSrc, listSeeds)
    captured = capsys.readouterr()
    assert "No stochastic sampling detected" in captured.out


def test_fnPrintReport_unseeded_warning_path(fixtureUnseededScript, capsys):
    """An unseeded source emits a WARNING block and the seed-call hint."""
    bStoch, listSrc, listSeeds = ftDetectStochastic(fixtureUnseededScript)
    fnPrintReport(fixtureUnseededScript, bStoch, listSrc, listSeeds)
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "lack a fixed seed" in captured.out


# ---------------------------------------------------------------------------
# _fiFindUnquotedHash quote-tracking branches
# ---------------------------------------------------------------------------


def test_hash_inside_double_quoted_string_is_not_a_comment(tmp_path):
    """A # inside a double-quoted string must NOT terminate code."""
    sPath = os.path.join(tmp_path, "quoted.py")
    with open(sPath, "w") as fileHandle:
        fileHandle.write(
            'sLabel = "value#with#hash"\n'
            'import numpy as np\n'
            'x = np.random.normal(size=10)\n'
        )
    bStoch, listSrc, _ = ftDetectStochastic(sPath)
    assert bStoch is True


def test_hash_inside_single_quoted_string_is_not_a_comment(tmp_path):
    """A # inside a single-quoted string must NOT terminate code."""
    sPath = os.path.join(tmp_path, "single.py")
    with open(sPath, "w") as fileHandle:
        fileHandle.write(
            "sTag = 'a#b#c'\n"
            "import numpy as np\n"
            "x = np.random.normal(size=10)\n"
        )
    bStoch, listSrc, _ = ftDetectStochastic(sPath)
    assert bStoch is True


def test_inline_comment_after_random_call_does_not_hide_call(tmp_path):
    """A trailing #-comment must leave the preceding code visible to the scan."""
    sPath = os.path.join(tmp_path, "trailing.py")
    with open(sPath, "w") as fileHandle:
        fileHandle.write(
            "import numpy as np\n"
            "x = np.random.normal(size=10)  # active call\n"
        )
    bStoch, _, _ = ftDetectStochastic(sPath)
    assert bStoch is True


# ---------------------------------------------------------------------------
# CLI main() entry point
# ---------------------------------------------------------------------------


def _fiRunMainExpectingExit(monkeypatch, listArgv):
    """Run main() with patched argv; return the first exit code via SystemExit."""
    from vaibify.testing import stochasticDetector
    monkeypatch.setattr(stochasticDetector.sys, "argv", listArgv)
    with pytest.raises(SystemExit) as excInfo:
        stochasticDetector.main()
    return excInfo.value.code


def test_main_usage_error_exits_one(capsys, monkeypatch):
    """Running with no script paths prints usage and exits with code 1."""
    iCode = _fiRunMainExpectingExit(monkeypatch, ["scan"])
    assert iCode == 1
    captured = capsys.readouterr()
    assert "Usage" in captured.out


def test_main_seeded_script_exits_zero(
    fixtureSeededScript, capsys, monkeypatch,
):
    """A seeded script makes main exit with code 0."""
    iCode = _fiRunMainExpectingExit(
        monkeypatch, ["scan", fixtureSeededScript],
    )
    assert iCode == 0


def test_main_unseeded_script_exits_one(
    fixtureUnseededScript, capsys, monkeypatch,
):
    """An unseeded script makes main exit with code 1."""
    iCode = _fiRunMainExpectingExit(
        monkeypatch, ["scan", fixtureUnseededScript],
    )
    assert iCode == 1
