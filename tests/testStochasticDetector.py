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
