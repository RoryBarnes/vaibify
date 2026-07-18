"""Tests for the step-rename cascade (plan + apply).

Per the repo's epistemics rules these tests are adversarial: each
guarantee is asserted with the condition that would break it — a
destination collision, a custom directory that must NOT move, a
marker that exists under the old name only, step-relative paths that
must survive untouched — never only the happy path.
"""

import json

import pytest

from vaibify.gui import stepRename
from vaibify.reproducibility.manifestWriter import (
    fbRewriteManifestPathPrefix,
)
from vaibify.reproducibility.repoFiles import HostRepoFiles


def _fdictWorkflow(dictStepOverrides=None, **dictWorkflowOverrides):
    dictStep = {
        "sName": "OldStep",
        "sDirectory": "OldStep",
        "sLabel": "A01",
        "saStepScripts": ["OldStep/runModel.py"],
        "saOutputDataFiles": ["OldStep/output.csv", "local.csv"],
        "saPlotFiles": ["OldStep/figure.pdf"],
        "saPlotCommands": ["python makePlot.py"],
        "saDataCommands": ["python runModel.py"],
    }
    dictStep.update(dictStepOverrides or {})
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/projectRepo",
        "listSteps": [dictStep],
    }
    dictWorkflow.update(dictWorkflowOverrides)
    return dictWorkflow


# --------------------------------------------------------------------------
# Plan: name validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("sBadName", [
    "", "   ", "a/b", "a\\b", "..", ".hidden", "x" * 101, "a\x00b",
])
def test_plan_rejects_unsafe_names(sBadName):
    with pytest.raises(ValueError):
        stepRename.fdictPlanStepRename(_fdictWorkflow(), 0, sBadName)


def test_plan_rejects_the_unchanged_name():
    with pytest.raises(ValueError):
        stepRename.fdictPlanStepRename(_fdictWorkflow(), 0, "OldStep")


def test_plan_rejects_a_bad_index():
    with pytest.raises(IndexError):
        stepRename.fdictPlanStepRename(_fdictWorkflow(), 5, "NewStep")


# --------------------------------------------------------------------------
# Plan: directory-follows-name rule
# --------------------------------------------------------------------------


def test_plan_moves_a_convention_directory_and_keeps_the_parent():
    dictWorkflow = _fdictWorkflow(
        {"sDirectory": "analysis/OldStep",
         "saStepScripts": ["analysis/OldStep/runModel.py"],
         "saOutputDataFiles": ["analysis/OldStep/output.csv"]},
    )
    dictPlan = stepRename.fdictPlanStepRename(dictWorkflow, 0, "NewStep")
    assert dictPlan["bDirectoryRenamed"] is True
    assert dictPlan["sNewDirectory"] == "analysis/NewStep"


def test_plan_realigns_a_nonconforming_directory():
    """Slug contract (2026-07-18): the directory's final component IS
    a function of the name, so a rename brings even a legacy custom
    directory into conformance — the old leave-it-alone behavior is
    retired."""
    dictWorkflow = _fdictWorkflow({"sDirectory": "customDirectory"})
    dictPlan = stepRename.fdictPlanStepRename(dictWorkflow, 0, "NewStep")
    assert dictPlan["bDirectoryRenamed"] is True
    assert dictPlan["sNewDirectory"] == "NewStep"


def test_plan_derives_the_slug_from_a_multi_word_name():
    """"Step Name" -> "StepName": spaces vanish, each word's first
    letter is uppercased, interior case is preserved (GJ stays GJ)."""
    dictPlan = stepRename.fdictPlanStepRename(
        _fdictWorkflow(), 0, "GJ 1132 xuv Flux",
    )
    assert dictPlan["sNewDirectory"] == "GJ1132XuvFlux"
    dictPlan = stepRename.fdictPlanStepRename(
        _fdictWorkflow(), 0, "TOI-540 XUV",
    )
    assert dictPlan["sNewDirectory"] == "TOI-540XUV"


def test_plan_leaves_a_templated_directory_alone_and_says_so():
    dictWorkflow = _fdictWorkflow(
        {"sDirectory": "{sPlotDirectory}/OldStep"},
    )
    dictPlan = stepRename.fdictPlanStepRename(dictWorkflow, 0, "NewStep")
    assert dictPlan["bDirectoryRenamed"] is False
    assert "template token" in dictPlan["sDirectoryNote"]


def test_plan_rejects_a_slug_collision_case_insensitively():
    """macOS clones sit on case-insensitive filesystems: 'New step'
    and 'NEW STEP' are the same directory there."""
    dictWorkflow = _fdictWorkflow()
    dictWorkflow["listSteps"].append({
        "sName": "NEW STEP", "sDirectory": "NEWSTEP",
        "sLabel": "A02",
    })
    with pytest.raises(ValueError):
        stepRename.fdictPlanStepRename(dictWorkflow, 0, "New step")


def test_alignment_plan_moves_directory_and_keeps_the_name():
    dictWorkflow = _fdictWorkflow(
        {"sName": "Old Step", "sDirectory": "systems/legacyDir",
         "saStepScripts": ["systems/legacyDir/run.py"],
         "saOutputDataFiles": ["systems/legacyDir/out.csv"]},
    )
    dictPlan = stepRename.fdictPlanDirectoryAlignment(dictWorkflow, 0)
    assert dictPlan["sNewName"] == "Old Step"
    assert dictPlan["sNewDirectory"] == "systems/OldStep"
    assert dictPlan["bDirectoryRenamed"] is True


def test_alignment_refuses_a_name_outside_the_alphabet():
    """A name the contract's alphabet forbids (apostrophe) cannot be
    aligned — the researcher must rename the step first."""
    dictWorkflow = _fdictWorkflow(
        {"sName": "Barnard's Star", "sDirectory": "BarnardsStar"},
    )
    with pytest.raises(ValueError):
        stepRename.fdictPlanDirectoryAlignment(dictWorkflow, 0)


def test_plan_rewrites_only_paths_under_the_old_directory():
    dictPlan = stepRename.fdictPlanStepRename(
        _fdictWorkflow(), 0, "NewStep",
    )
    listOld = [d["sOld"] for d in dictPlan["listFieldRewrites"]]
    assert "OldStep/output.csv" in listOld
    assert "OldStep/runModel.py" in listOld
    # The step-relative entry has no directory prefix and must
    # survive untouched — rewriting it would corrupt the path.
    assert "local.csv" not in listOld


def test_plan_does_not_rewrite_lookalike_prefixes():
    """'OldStepExtra/x.csv' does not live in 'OldStep' — a naive
    startswith rewrite would corrupt it."""
    dictWorkflow = _fdictWorkflow(
        {"saOutputDataFiles": ["OldStepExtra/x.csv"]},
    )
    dictPlan = stepRename.fdictPlanStepRename(dictWorkflow, 0, "NewStep")
    assert dictPlan["listFieldRewrites"] == [] or all(
        d["sOld"] != "OldStepExtra/x.csv"
        for d in dictPlan["listFieldRewrites"]
    )


def test_plan_covers_remote_data_and_declared_binaries():
    dictWorkflow = _fdictWorkflow(
        {"listRemoteData": [
            {"sPath": "OldStep/raw.fits", "sSha256": "abc"},
        ]},
        listDeclaredBinaries=[
            {"sBinaryPath": "OldStep/model", "sPurpose": "forward"},
        ],
    )
    dictPlan = stepRename.fdictPlanStepRename(dictWorkflow, 0, "NewStep")
    assert {"sField": "listRemoteData", "sOld": "OldStep/raw.fits",
            "sNew": "NewStep/raw.fits"} in dictPlan["listFieldRewrites"]
    assert dictPlan["listBinaryRewrites"] == [
        {"sOld": "OldStep/model", "sNew": "NewStep/model"},
    ]


def test_plan_warns_about_commands_mentioning_the_directory():
    dictWorkflow = _fdictWorkflow()
    dictWorkflow["listSteps"].append({
        "sName": "Downstream", "sDirectory": "Downstream",
        "sLabel": "A02",
        "saDataCommands": ["python read.py ../OldStep/output.csv"],
    })
    dictPlan = stepRename.fdictPlanStepRename(dictWorkflow, 0, "NewStep")
    assert any("A02" in sWarning
               for sWarning in dictPlan["listCommandWarnings"])


# --------------------------------------------------------------------------
# Apply: workflow-dict rewrites and ordering guarantees
# --------------------------------------------------------------------------


class _FakeDocker:
    """Scripted exec results keyed by command substring."""

    def __init__(self, dictResults):
        self.dictResults = dictResults
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        for sNeedle, tResult in self.dictResults.items():
            if sNeedle in sCommand:
                return tResult
        return (1, "")


class _FakeRepoFiles:
    """Dict-backed repo-relative file store."""

    def __init__(self, dictFiles=None):
        self.dictFiles = dict(dictFiles or {})

    def fbIsFile(self, sRelPath):
        return sRelPath in self.dictFiles

    def fsReadText(self, sRelPath):
        return self.dictFiles[sRelPath]

    def fnWriteTextAtomic(self, sRelPath, sContent):
        self.dictFiles[sRelPath] = sContent

    def fbRemoveFile(self, sRelPath):
        return self.dictFiles.pop(sRelPath, None) is not None


# The workflow file path is NOT a workflow-dict key — the hub keeps it
# in dictCtx["paths"]. The 2026-07-18 marker-orphaning bug shipped
# because this fixture put it on the dict, encoding the route's wrong
# assumption (green-stub trap); keep it separate forever.
S_WORKFLOW_PATH = "/workspace/projectRepo/.vaibify/projects/study.json"


def _fdictApply(dictWorkflow, dictDockerResults, filesRepo=None):
    dictPlan = stepRename.fdictPlanStepRename(dictWorkflow, 0, "NewStep")
    connectionDocker = _FakeDocker(dictDockerResults)
    dictReport = stepRename.fdictApplyStepRename(
        connectionDocker, "cid", filesRepo or _FakeRepoFiles(),
        dictWorkflow, 0, dictPlan, S_WORKFLOW_PATH,
    )
    return dictReport, connectionDocker


def test_apply_rewrites_the_step_and_moves_the_directory():
    dictWorkflow = _fdictWorkflow()
    dictReport, connectionDocker = _fdictApply(dictWorkflow, {
        "test -e": (1, ""),   # destination absent
        "test -d": (0, ""),   # source present
        "git mv": (0, ""),
    })
    dictStep = dictWorkflow["listSteps"][0]
    assert dictStep["sName"] == "NewStep"
    assert dictStep["sDirectory"] == "NewStep"
    assert dictStep["saOutputDataFiles"] == [
        "NewStep/output.csv", "local.csv",
    ]
    assert dictReport["bDirectoryMoved"] is True
    assert any("git mv" in sCommand
               for sCommand in connectionDocker.listCommands)


def test_apply_refuses_a_destination_collision_before_mutating():
    """A name collision must abort with the workflow dict UNTOUCHED —
    a half-applied rename desynchronizes JSON from disk."""
    dictWorkflow = _fdictWorkflow()
    dictPlan = stepRename.fdictPlanStepRename(dictWorkflow, 0, "NewStep")
    connectionDocker = _FakeDocker({"test -e": (0, "")})  # dest EXISTS
    with pytest.raises(ValueError):
        stepRename.fdictApplyStepRename(
            connectionDocker, "cid", _FakeRepoFiles(),
            dictWorkflow, 0, dictPlan, S_WORKFLOW_PATH,
        )
    assert dictWorkflow["listSteps"][0]["sName"] == "OldStep"
    assert dictWorkflow["listSteps"][0]["sDirectory"] == "OldStep"


def test_apply_skips_the_move_when_nothing_is_on_disk_yet():
    dictWorkflow = _fdictWorkflow()
    dictReport, connectionDocker = _fdictApply(dictWorkflow, {
        "test -e": (1, ""),
        "test -d": (1, ""),   # source directory never created
    })
    assert dictReport["bDirectoryMoved"] is False
    assert dictWorkflow["listSteps"][0]["sDirectory"] == "NewStep"
    assert not any("git mv" in sCommand
                   for sCommand in connectionDocker.listCommands)


def test_apply_moves_the_marker_and_rewrites_its_directory():
    """The marker is named from the directory and identity-checked
    against it (_fbMarkerIdentityMatchesStep) — losing either half
    silently demotes a verified step to untested."""
    sMarkerDir = ".vaibify/test_markers/study"
    filesRepo = _FakeRepoFiles({
        sMarkerDir + "/OldStep.json": json.dumps(
            {"sLabel": "A01", "sDirectory": "OldStep",
             "dictHashes": {"OldStep/output.csv": "abc"}},
        ),
    })
    dictWorkflow = _fdictWorkflow()
    dictReport, _ = _fdictApply(dictWorkflow, {
        "test -e": (1, ""), "test -d": (0, ""), "git mv": (0, ""),
    }, filesRepo)
    assert dictReport["bMarkerMoved"] is True
    assert sMarkerDir + "/OldStep.json" not in filesRepo.dictFiles
    dictMarker = json.loads(
        filesRepo.dictFiles[sMarkerDir + "/NewStep.json"],
    )
    assert dictMarker["sDirectory"] == "NewStep"


def test_apply_without_a_marker_reports_no_move():
    dictWorkflow = _fdictWorkflow()
    dictReport, _ = _fdictApply(dictWorkflow, {
        "test -e": (1, ""), "test -d": (0, ""), "git mv": (0, ""),
    }, _FakeRepoFiles())
    assert dictReport["bMarkerMoved"] is False


# --------------------------------------------------------------------------
# Manifest path rewrite (hashes must survive verbatim)
# --------------------------------------------------------------------------


def test_manifest_rewrite_swaps_paths_and_keeps_hashes(tmp_path):
    sHash = "a" * 64
    (tmp_path / "MANIFEST.sha256").write_text(
        "# SHA-256 manifest of workflow artefacts\n"
        + sHash + "  OldStep/output.csv\n"
        + ("b" * 64) + "  Other/kept.csv\n",
    )
    filesRepo = HostRepoFiles(str(tmp_path))
    assert fbRewriteManifestPathPrefix(
        filesRepo, "OldStep", "NewStep",
    ) is True
    sBody = (tmp_path / "MANIFEST.sha256").read_text()
    assert sHash + "  NewStep/output.csv" in sBody
    assert "OldStep" not in sBody
    assert ("b" * 64) + "  Other/kept.csv" in sBody


def test_manifest_rewrite_returns_false_when_nothing_matches(tmp_path):
    (tmp_path / "MANIFEST.sha256").write_text(
        "# SHA-256 manifest of workflow artefacts\n"
        + ("c" * 64) + "  Elsewhere/file.csv\n",
    )
    filesRepo = HostRepoFiles(str(tmp_path))
    assert fbRewriteManifestPathPrefix(
        filesRepo, "OldStep", "NewStep",
    ) is False


def test_manifest_rewrite_ignores_lookalike_prefixes(tmp_path):
    (tmp_path / "MANIFEST.sha256").write_text(
        "# SHA-256 manifest of workflow artefacts\n"
        + ("d" * 64) + "  OldStepExtra/file.csv\n",
    )
    filesRepo = HostRepoFiles(str(tmp_path))
    assert fbRewriteManifestPathPrefix(
        filesRepo, "OldStep", "NewStep",
    ) is False


# --------------------------------------------------------------------------
# Frontend contract: the rename flow previews before it applies
# --------------------------------------------------------------------------


import os

_S_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadStatic(sName):
    with open(os.path.join(_S_STATIC_DIR, sName),
              encoding="utf-8") as fileHandle:
        return fileHandle.read()


def test_context_menu_offers_rename():
    sIndex = _fsReadStatic("index.html")
    assert 'data-action="rename"' in sIndex
    sApplication = _fsReadStatic("scriptApplication.js")
    assert 'sAction === "rename"' in sApplication


def test_rename_flow_previews_the_dry_run_before_applying():
    """The apply may only be reachable through the confirm modal that
    shows the dry-run change-set — a rename that moves directories
    without a preview is a silent bulk mutation."""
    sEditor = _fsReadStatic("scriptStepEditor.js")
    iPreview = sEditor.find("bDryRun: true")
    iApply = sEditor.find("bDryRun: false")
    assert -1 < iPreview < iApply, (
        "the dry-run preview must precede the apply"
    )
    assert "fnShowConfirmModal" in sEditor
    assert "listScriptWarnings" in sEditor, (
        "script warnings from the dry run must reach the preview"
    )
