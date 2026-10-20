"""Libraries the execution profiles do not cover are named, not passed over.

The profiles set NumPy, OpenBLAS and the C math library, nothing else
(design, "Known limits"). A project whose dependency lock pins another
library that chooses its own code by processor -- SciPy's bundled
OpenBLAS (until a live test, design 0c), oneDNN under PyTorch or
TensorFlow, Numba, FFTW, Julia -- or GPU packages, is told so by name.
The reading is evidence beside the question, never a gate.
"""

import pytest

from vaibify.reproducibility import determinismGate


S_LOCK_WITH_EVERY_FAMILY = "\n".join([
    "numpy==2.1.0 \\", "    --hash=sha256:" + "a" * 64,
    "scipy==1.14.1", "torch==2.4.0", "numba==0.60.0", "llvmlite==0.43.0",
    "pyFFTW==0.14.0", "juliacall==0.9.20", "cupy-cuda12x==13.3.0",
    "nvidia_cublas_cu12==12.4.5.8", "matplotlib==3.9.2", "",
])


def _fdictDetect(tmp_path, sLock):
    (tmp_path / "requirements.lock").write_text(sLock)
    return determinismGate.fdictDetectMathsLibrary(str(tmp_path))


@pytest.mark.falsification
def testEveryUncoveredFamilyIsNamedWithTheDistributionsThatBringIt(tmp_path):
    """Kills: dropping SciPy's bundled OpenBLAS from the uncovered families."""
    dictReading = _fdictDetect(tmp_path, S_LOCK_WITH_EVERY_FAMILY)
    assert dictReading["listUncoveredLibraries"] == [
        {"sLibrary": "SciPy's own OpenBLAS", "listPackages": ["scipy"]},
        {"sLibrary": "oneDNN (inside PyTorch or TensorFlow)",
         "listPackages": ["torch"]},
        {"sLibrary": "Numba", "listPackages": ["llvmlite", "numba"]},
        {"sLibrary": "FFTW", "listPackages": ["pyfftw"]},
        {"sLibrary": "Julia", "listPackages": ["juliacall"]},
        {"sLibrary": "GPU libraries",
         "listPackages": ["cupy-cuda12x", "nvidia-cublas-cu12"]},
    ]
    sNote = dictReading["sUncoveredNote"]
    assert "neither execution profile covers" in sNote
    assert "SciPy's own OpenBLAS (scipy)" in sNote
    assert "GPU libraries (cupy-cuda12x, nvidia-cublas-cu12)" in sNote
    assert "numpy" not in sNote and "matplotlib" not in sNote


def testACoveredOnlyLockNamesNothingExtra(tmp_path):
    dictReading = _fdictDetect(tmp_path, "numpy==2.1.0\nmatplotlib==3.9.2\n")
    assert dictReading["listUncoveredLibraries"] == []
    assert dictReading["sUncoveredNote"] == ""
    assert dictReading["bMklFound"] is False


def testTheMklReadingIsUnchangedBesideTheNewOne(tmp_path):
    dictReading = _fdictDetect(tmp_path, "mkl==2024.2.0\nscipy==1.14.1\n")
    assert dictReading["listMklPackages"] == ["mkl"]
    assert [dictFound["sLibrary"] for dictFound in
            dictReading["listUncoveredLibraries"]] == ["SciPy's own OpenBLAS"]


def testNoLockReadsAsUnknownWithNothingNamed(tmp_path):
    dictReading = determinismGate.fdictDetectMathsLibrary(str(tmp_path))
    assert dictReading["bMklFound"] is None
    assert dictReading["listUncoveredLibraries"] == []
