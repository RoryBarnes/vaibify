"""Tests for the conftest marker plugin template and its hash logic.

The conftest plugin is shipped as a Python source template into each
step's tests directory at container time. We cannot run a full pytest
session here, but we can validate the template's standalone helpers
against an isolated workspace to ensure the dictOutputHashes
round-trip works end-to-end (see Phase 2 of the
workspace-as-git-repo plan).
"""

import ast
import json
import os
import sys
import textwrap
import types
from pathlib import Path

import pytest

from vaibify.gui import conftestManager, hashStaleness, mtimeCache


def _fsWrite(sRoot, sRelPath, sContent=""):
    sAbsPath = os.path.join(sRoot, *sRelPath.split("/"))
    os.makedirs(os.path.dirname(sAbsPath) or sAbsPath, exist_ok=True)
    if not os.path.isdir(sAbsPath):
        with open(sAbsPath, "w") as f:
            f.write(sContent)
    return sAbsPath


def _fnWriteJson(sRoot, sRelPath, dictContent):
    sAbsPath = os.path.join(sRoot, *sRelPath.split("/"))
    os.makedirs(os.path.dirname(sAbsPath), exist_ok=True)
    with open(sAbsPath, "w") as f:
        json.dump(dictContent, f)


def test_template_mentions_hash_fields():
    sTemplate = conftestManager.fsConftestContent()
    assert "dictOutputHashes" in sTemplate
    assert "sRunAtUtc" in sTemplate
    assert "blob " in sTemplate


def test_template_parses_as_valid_python():
    sTemplate = conftestManager.fsConftestContent()
    ast.parse(sTemplate)


def test_template_uses_workspace_root():
    sTemplate = conftestManager.fsConftestContent()
    assert '_WORKSPACE_ROOT = Path("/workspace")' in sTemplate


def _fnExecTemplateWithRoot(tmp_path):
    """Exec the template with _WORKSPACE_ROOT rewritten to tmp_path.

    Returns a module-like namespace containing the helper functions
    so tests can exercise them without a live pytest session.
    """
    sTemplate = conftestManager.fsConftestContent()
    sPatched = sTemplate.replace(
        '_WORKSPACE_ROOT = Path("/workspace")',
        '_WORKSPACE_ROOT = Path(%r)' % str(tmp_path),
    )
    moduleNs = types.ModuleType("vaibify_conftest_test_ns")
    moduleNs.__dict__["__name__"] = "vaibify_conftest_test_ns"
    exec(compile(sPatched, "<template>", "exec"), moduleNs.__dict__)
    return moduleNs


# ----------------------------------------------------------------------
# _flistStepOutputFiles
# ----------------------------------------------------------------------


def test_flistStepOutputFiles_returns_empty_without_workflows(tmp_path):
    ns = _fnExecTemplateWithRoot(tmp_path)
    sStepDir = str(tmp_path / "step1")
    assert ns._flistStepOutputFiles(sStepDir) == []


def test_flistStepOutputFiles_matches_step_by_directory(tmp_path):
    _fnWriteJson(str(tmp_path), ".vaibify/workflows/main.json", {
        "listSteps": [{
            "sDirectory": "/workspace/step1",
            "saDataFiles": ["data/out.csv"],
            "saPlotFiles": ["Plot/fig.pdf"],
        }],
    })
    ns = _fnExecTemplateWithRoot(tmp_path)
    listResult = ns._flistStepOutputFiles(str(tmp_path / "step1"))
    assert "step1/data/out.csv" in listResult
    assert "step1/Plot/fig.pdf" in listResult


def test_flistStepOutputFiles_skips_template_placeholders(tmp_path):
    _fnWriteJson(str(tmp_path), ".vaibify/workflows/main.json", {
        "listSteps": [{
            "sDirectory": "/workspace/step1",
            "saDataFiles": ["out_{iteration}.csv", "out.csv"],
        }],
    })
    ns = _fnExecTemplateWithRoot(tmp_path)
    listResult = ns._flistStepOutputFiles(str(tmp_path / "step1"))
    assert listResult == ["step1/out.csv"]


def test_flistStepOutputFiles_ignores_other_steps(tmp_path):
    _fnWriteJson(str(tmp_path), ".vaibify/workflows/main.json", {
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saPlotFiles": ["a.pdf"],
            },
            {
                "sDirectory": "/workspace/step2",
                "saPlotFiles": ["b.pdf"],
            },
        ],
    })
    ns = _fnExecTemplateWithRoot(tmp_path)
    listResult = ns._flistStepOutputFiles(str(tmp_path / "step1"))
    assert "step1/a.pdf" in listResult
    assert "step2/b.pdf" not in listResult


def test_flistStepOutputFiles_tolerates_corrupt_workflow(tmp_path):
    os.makedirs(
        os.path.join(str(tmp_path), ".vaibify", "workflows"),
        exist_ok=True,
    )
    with open(
        os.path.join(str(tmp_path), ".vaibify", "workflows", "bad.json"),
        "w",
    ) as f:
        f.write("{not-json")
    _fnWriteJson(str(tmp_path), ".vaibify/workflows/good.json", {
        "listSteps": [{
            "sDirectory": "/workspace/step1",
            "saPlotFiles": ["fig.pdf"],
        }],
    })
    ns = _fnExecTemplateWithRoot(tmp_path)
    listResult = ns._flistStepOutputFiles(str(tmp_path / "step1"))
    assert "step1/fig.pdf" in listResult


# ----------------------------------------------------------------------
# _fsBlobSha
# ----------------------------------------------------------------------


def test_fsBlobSha_matches_git_hash_object(tmp_path):
    _fsWrite(str(tmp_path), "fixture.txt", "what is up, doc?")
    ns = _fnExecTemplateWithRoot(tmp_path)
    sResult = ns._fsBlobSha(str(tmp_path / "fixture.txt"))
    assert sResult == "bd9dbf5aae1a3862dd1526723246b20206e5fc37"


def test_fsBlobSha_returns_empty_for_missing_file(tmp_path):
    ns = _fnExecTemplateWithRoot(tmp_path)
    assert ns._fsBlobSha(str(tmp_path / "ghost.txt")) == ""


def test_fsBlobSha_matches_host_implementation(tmp_path):
    """Container-side and host-side hashes must agree on format.

    The conftest plugin inlines its own SHA1 computation; the host
    uses overleafMirror.fsComputeBlobSha (via mtimeCache). Both must
    produce identical output for the same bytes, or the round-trip
    breaks silently.
    """
    _fsWrite(str(tmp_path), "fixture.txt", "round-trip content")
    ns = _fnExecTemplateWithRoot(tmp_path)
    sHostSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "fixture.txt", {},
    )
    sContainerSha = ns._fsBlobSha(str(tmp_path / "fixture.txt"))
    assert sHostSha == sContainerSha


# ----------------------------------------------------------------------
# End-to-end round trip: conftest hashes -> marker -> staleness check
# ----------------------------------------------------------------------


def test_marker_round_trip_detects_drift_after_mutation(tmp_path):
    _fsWrite(str(tmp_path), "step1/Plot/fig.pdf", "original")
    _fnWriteJson(str(tmp_path), ".vaibify/workflows/main.json", {
        "listSteps": [{
            "sDirectory": "/workspace/step1",
            "saPlotFiles": ["Plot/fig.pdf"],
        }],
    })
    ns = _fnExecTemplateWithRoot(tmp_path)
    dictHashes = ns._fdictComputeOutputHashes(str(tmp_path / "step1"))
    assert "step1/Plot/fig.pdf" in dictHashes
    dictMarker = {"dictOutputHashes": dictHashes}
    setStale = hashStaleness.fsetStaleOutputsForStep(
        dictMarker, str(tmp_path), {},
    )
    assert setStale == set()
    with open(str(tmp_path / "step1" / "Plot" / "fig.pdf"), "w") as f:
        f.write("drifted")
    os.utime(
        str(tmp_path / "step1" / "Plot" / "fig.pdf"),
        (1_000_000, 1_000_000),
    )
    setStaleAfter = hashStaleness.fsetStaleOutputsForStep(
        dictMarker, str(tmp_path), {},
    )
    assert setStaleAfter == {"step1/Plot/fig.pdf"}


def test_marker_round_trip_clean_on_fresh_clone(tmp_path):
    """A fresh clone sees no mtime cache; staleness must still work."""
    _fsWrite(str(tmp_path), "step1/Plot/fig.pdf", "verified-content")
    _fnWriteJson(str(tmp_path), ".vaibify/workflows/main.json", {
        "listSteps": [{
            "sDirectory": "/workspace/step1",
            "saPlotFiles": ["Plot/fig.pdf"],
        }],
    })
    ns = _fnExecTemplateWithRoot(tmp_path)
    dictHashes = ns._fdictComputeOutputHashes(str(tmp_path / "step1"))
    dictMarker = {"dictOutputHashes": dictHashes}
    dictFreshCache = {}
    setStale = hashStaleness.fsetStaleOutputsForStep(
        dictMarker, str(tmp_path), dictFreshCache,
    )
    assert setStale == set()
    assert dictFreshCache, (
        "Fresh cache should be populated as a side effect of the check"
    )
