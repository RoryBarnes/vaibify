"""Tests for the AICS L3 reproducibility envelope generator."""

import subprocess
from unittest.mock import patch

from vaibify.reproducibility import dataArchiver


_MANIFEST_FILENAME = "MANIFEST.sha256"
_LOCK_FILENAME = "requirements.lock"
_ENVIRONMENT_RELPATH = ".vaibify/environment.json"


def _fnWriteFile(pathRepo, sRelPath, sContent=""):
    """Create a file under pathRepo with the given content."""
    pathFile = pathRepo / sRelPath
    pathFile.parent.mkdir(parents=True, exist_ok=True)
    pathFile.write_text(sContent)
    return pathFile


def _fdictWorkflow(saDataFiles=None):
    """Build a single-step workflow dict declaring the given outputs."""
    return {
        "listSteps": [
            {
                "sName": "OnlyStep",
                "saOutputFiles": [],
                "saPlotFiles": [],
                "saDataFiles": list(saDataFiles or []),
            },
        ],
    }


def _fnFakeUvWritesLock(pathRepo):
    """Return a side-effect that writes a stub requirements.lock."""
    def _fn(*args, **kwargs):
        pathLock = pathRepo / _LOCK_FILENAME
        pathLock.write_text(
            "alpha==1.0 \\\n"
            "    --hash=sha256:" + ("0" * 64) + "\n"
        )
        return subprocess.CompletedProcess(
            args=args[0] if args else [], returncode=0,
            stdout="", stderr="",
        )
    return _fn


def _fdictFakeImageDigest(sName):
    """Return a deterministic fake container-image digest payload."""
    return {
        "sContainerName": sName,
        "sImageDigest": "fakeImage@sha256:" + ("a" * 64),
    }


def _fdictFakeSystemTools():
    """Return a deterministic fake system-tools payload."""
    return {
        "sPython": "Fake 3.12",
        "sGcc": None,
        "sLibc": None,
        "sOsRelease": None,
    }


# ----------------------------------------------------------------------
# 1. Happy path: all three tiers
# ----------------------------------------------------------------------


def test_happy_path_writes_all_three_tiers(tmp_path):
    """When every prerequisite is present the envelope has all 3 tiers."""
    _fnWriteFile(tmp_path, "out.csv", "alpha,beta\n")
    _fnWriteFile(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    dictWorkflow = _fdictWorkflow(saDataFiles=["out.csv"])
    with patch(
        "vaibify.reproducibility.dependencyPinning.fbIsUvAvailable",
        return_value=True,
    ), patch(
        "vaibify.reproducibility.dependencyPinning.subprocess.run",
        side_effect=_fnFakeUvWritesLock(tmp_path),
    ), patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fdictCaptureContainerImageDigest",
        return_value=_fdictFakeImageDigest("vaibify-test"),
    ), patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fdictCaptureSystemTools",
        return_value=_fdictFakeSystemTools(),
    ):
        dataArchiver.fnGenerateReproducibilityEnvelope(
            str(tmp_path), dictWorkflow,
            sContainerName="vaibify-test",
        )
    assert (tmp_path / _MANIFEST_FILENAME).is_file()
    assert (tmp_path / _LOCK_FILENAME).is_file()
    assert (tmp_path / _ENVIRONMENT_RELPATH).is_file()


# ----------------------------------------------------------------------
# 2. Tier 2 skipped when uv is missing
# ----------------------------------------------------------------------


def test_tier_two_skipped_when_uv_missing(tmp_path, caplog):
    """Missing uv is logged but the other tiers still run."""
    _fnWriteFile(tmp_path, "out.csv", "x\n")
    dictWorkflow = _fdictWorkflow(saDataFiles=["out.csv"])
    with patch(
        "vaibify.reproducibility.dependencyPinning.fbIsUvAvailable",
        return_value=False,
    ):
        with caplog.at_level("WARNING", logger="vaibify"):
            dataArchiver.fnGenerateReproducibilityEnvelope(
                str(tmp_path), dictWorkflow,
            )
    assert (tmp_path / _MANIFEST_FILENAME).is_file()
    assert not (tmp_path / _LOCK_FILENAME).is_file()
    assert any(
        "requirements.lock" in rec.getMessage()
        for rec in caplog.records
    )


# ----------------------------------------------------------------------
# 3. Tier 3 skipped when sContainerName is None
# ----------------------------------------------------------------------


def test_tier_three_skipped_when_container_none(tmp_path):
    """No container name => environment.json is not written."""
    _fnWriteFile(tmp_path, "out.csv", "x\n")
    dictWorkflow = _fdictWorkflow(saDataFiles=["out.csv"])
    with patch(
        "vaibify.reproducibility.dependencyPinning.fbIsUvAvailable",
        return_value=False,
    ):
        dataArchiver.fnGenerateReproducibilityEnvelope(
            str(tmp_path), dictWorkflow, sContainerName=None,
        )
    assert (tmp_path / _MANIFEST_FILENAME).is_file()
    assert not (tmp_path / _ENVIRONMENT_RELPATH).is_file()


# ----------------------------------------------------------------------
# 4. Tier-2 CalledProcessError isolated; tiers 1 + 3 still complete
# ----------------------------------------------------------------------


def test_partial_failure_tier_two_does_not_block_others(tmp_path, caplog):
    """A uv compile failure is logged but doesn't propagate."""
    _fnWriteFile(tmp_path, "out.csv", "x\n")
    _fnWriteFile(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    dictWorkflow = _fdictWorkflow(saDataFiles=["out.csv"])

    def _fnFailingUv(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0] if args else [], returncode=2,
            stdout="", stderr="resolution failed",
        )

    with patch(
        "vaibify.reproducibility.dependencyPinning.fbIsUvAvailable",
        return_value=True,
    ), patch(
        "vaibify.reproducibility.dependencyPinning.subprocess.run",
        side_effect=_fnFailingUv,
    ), patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fdictCaptureContainerImageDigest",
        return_value=_fdictFakeImageDigest("vaibify-test"),
    ), patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fdictCaptureSystemTools",
        return_value=_fdictFakeSystemTools(),
    ):
        with caplog.at_level("WARNING", logger="vaibify"):
            dataArchiver.fnGenerateReproducibilityEnvelope(
                str(tmp_path), dictWorkflow,
                sContainerName="vaibify-test",
            )
    assert (tmp_path / _MANIFEST_FILENAME).is_file()
    assert not (tmp_path / _LOCK_FILENAME).is_file()
    assert (tmp_path / _ENVIRONMENT_RELPATH).is_file()
    assert any(
        "uv compile failed" in rec.getMessage()
        for rec in caplog.records
    )


# ----------------------------------------------------------------------
# 5. Idempotent on Tier 1 (manifest is byte-exact for unchanged content)
# ----------------------------------------------------------------------


def test_tier_one_is_byte_exact_on_repeat(tmp_path):
    """Calling envelope twice with identical content yields identical files."""
    _fnWriteFile(tmp_path, "out.csv", "alpha,beta\n")
    dictWorkflow = _fdictWorkflow(saDataFiles=["out.csv"])
    with patch(
        "vaibify.reproducibility.dependencyPinning.fbIsUvAvailable",
        return_value=False,
    ):
        dataArchiver.fnGenerateReproducibilityEnvelope(
            str(tmp_path), dictWorkflow,
        )
        baFirst = (tmp_path / _MANIFEST_FILENAME).read_bytes()
        dataArchiver.fnGenerateReproducibilityEnvelope(
            str(tmp_path), dictWorkflow,
        )
        baSecond = (tmp_path / _MANIFEST_FILENAME).read_bytes()
    assert baFirst == baSecond


# ----------------------------------------------------------------------
# 6. fnArchiveOutputs invokes the envelope generator
# ----------------------------------------------------------------------


@patch("vaibify.reproducibility.dataArchiver._fnSaveProvenanceFile")
@patch("vaibify.reproducibility.dataArchiver.fnUpdateProvenance")
@patch("vaibify.reproducibility.dataArchiver.fnUploadToZenodo")
@patch(
    "vaibify.reproducibility.dataArchiver.flistDetectChangedOutputs",
    return_value=["/tmp/out.dat"],
)
@patch(
    "vaibify.reproducibility.dataArchiver._fdictLoadOrCreateProvenance",
    return_value={},
)
@patch(
    "vaibify.reproducibility.dataArchiver."
    "fnGenerateReproducibilityEnvelope",
)
def test_fnArchiveOutputs_invokes_envelope_generator(
    mockEnvelope, mockLoad, mockDetect, mockUpload, mockUpdate, mockSave,
):
    """The archive flow now also writes the reproducibility envelope."""
    dictConfig = {
        "sZenodoService": "sandbox",
        "sContainerName": "vaibify-test",
    }
    dictWorkflow = {"listSteps": []}
    dataArchiver.fnArchiveOutputs(dictConfig, dictWorkflow, "/work")
    mockEnvelope.assert_called_once()
    args, kwargs = mockEnvelope.call_args
    assert args[0] == "/work"
    assert args[1] is dictWorkflow
    assert kwargs.get("sContainerName") == "vaibify-test"
