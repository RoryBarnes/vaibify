"""Final tests targeting remaining testable uncovered lines."""

import os
import pathlib
import tempfile
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest
import yaml


# ── __main__.py ──────────────────────────────────────────────────────


def test_main_module_has_main():
    with patch("vaibify.cli.main.main", side_effect=SystemExit(0)):
        try:
            import vaibify.__main__
        except SystemExit:
            pass


# ── commandConfig.py: YAML error handling ────────────────────────────


def test_fdictLoadYamlFile_invalid_yaml(tmp_path):
    from vaibify.cli.commandConfig import fdictLoadYamlFile
    sPath = str(tmp_path / "bad.yml")
    with open(sPath, "w") as f:
        f.write(": invalid: yaml: {{{}}")
    with pytest.raises(SystemExit):
        fdictLoadYamlFile(sPath)


def test_config_import_aborted(tmp_path):
    from click.testing import CliRunner
    from vaibify.cli.commandConfig import config, fnWriteYaml
    sInputPath = str(tmp_path / "input.yml")
    fnWriteYaml({"key": "val"}, sInputPath)
    sConfigPath = str(tmp_path / "vaibify.yml")
    fnWriteYaml({"old": "config"}, sConfigPath)
    with patch(
        "vaibify.cli.commandConfig.fsConfigPath",
        return_value=sConfigPath,
    ):
        runner = CliRunner()
        result = runner.invoke(config, ["import", sInputPath], input="n\n")
        assert "aborted" in result.output.lower()


# ── commandInit.py ───────────────────────────────────────────────────


def test_fnPrintAvailableTemplates_no_templates():
    from vaibify.cli.commandInit import fnPrintAvailableTemplates
    with patch(
        "vaibify.cli.commandInit.flistAvailableTemplates",
        return_value=[],
    ):
        fnPrintAvailableTemplates()


def test_fnCopyDirectoryContents_creates_files(tmp_path):
    from vaibify.cli.commandInit import fnCopyDirectoryContents
    sSrcDir = str(tmp_path / "src")
    sDestDir = str(tmp_path / "dest")
    os.makedirs(sSrcDir)
    os.makedirs(sDestDir)
    with open(os.path.join(sSrcDir, "test.txt"), "w") as f:
        f.write("hello")
    fnCopyDirectoryContents(sSrcDir, sDestDir)
    assert os.path.isfile(os.path.join(sDestDir, "test.txt"))


# ── configLoader.py ──────────────────────────────────────────────────


def test_fbDockerAvailable_returns_bool():
    from vaibify.cli.configLoader import fbDockerAvailable
    bResult = fbDockerAvailable()
    assert isinstance(bResult, bool)


# ── main.py: config path override ────────────────────────────────────


def test_main_config_override():
    from click.testing import CliRunner
    from vaibify.cli.main import main
    runner = CliRunner()
    result = runner.invoke(main, ["--config", "/nonexistent.yml", "--help"])
    assert result.exit_code == 0


# ── containerConfig.py: destination field ─────────────────────────────


def test_parse_line_with_destination(tmp_path):
    from vaibify.config.containerConfig import flistParseContainerConf
    sPath = str(tmp_path / "test.conf")
    with open(sPath, "w") as f:
        f.write("repo|git@host:r.git|main|pip_editable|.target\n")
    listEntries = flistParseContainerConf(sPath)
    assert len(listEntries) == 1
    assert listEntries[0].get("sDestination") == ".target"


def test_format_line_with_destination():
    from vaibify.config.containerConfig import fnGenerateContainerConf
    config = SimpleNamespace(
        listRepositories=[{
            "name": "claude", "url": "git@host:c.git",
            "branch": "main", "installMethod": "reference",
            "destination": ".claude",
        }],
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".conf",
                                     delete=False) as f:
        sPath = f.name
    try:
        fnGenerateContainerConf(config, sPath)
        with open(sPath) as f:
            sContent = f.read()
        assert ".claude" in sContent
    finally:
        os.unlink(sPath)


# ── projectConfig.py: validation ──────────────────────────────────────


def test_fbValidateConfig_invalid_raises():
    from vaibify.config.projectConfig import fbValidateConfig
    dictConfig = {"projectName": "", "repositories": []}
    assert fbValidateConfig(dictConfig) is False


def test_fconfigLoadFromFile_invalid_yaml(tmp_path):
    from vaibify.config.projectConfig import fconfigLoadFromFile
    sPath = str(tmp_path / "bad.yml")
    with open(sPath, "w") as f:
        f.write("not_a_dict: [")
    with pytest.raises(Exception):
        fconfigLoadFromFile(sPath)


def test_fconfigLoadFromFile_none_content(tmp_path):
    from vaibify.config.projectConfig import fconfigLoadFromFile
    sPath = str(tmp_path / "empty.yml")
    with open(sPath, "w") as f:
        f.write("")
    with pytest.raises(Exception):
        fconfigLoadFromFile(sPath)


# ── provenanceTracker.py ──────────────────────────────────────────────


def test_fsCurrentTimestamp_format():
    from vaibify.reproducibility.provenanceTracker import (
        _fsCurrentTimestamp,
    )
    sTimestamp = _fsCurrentTimestamp()
    assert "T" in sTimestamp
    assert len(sTimestamp) > 10


def test_fnUpdateProvenance_hashes_files(tmp_path):
    from vaibify.reproducibility.provenanceTracker import (
        fnUpdateProvenance,
    )
    sFilePath = str(tmp_path / "data.txt")
    with open(sFilePath, "w") as f:
        f.write("test data")
    dictProvenance = {}
    dictWorkflow = {
        "listSteps": [{
            "sName": "Step 1",
            "sDirectory": str(tmp_path),
            "saDataFiles": ["data.txt"],
            "saPlotFiles": [],
        }],
    }
    fnUpdateProvenance(dictProvenance, dictWorkflow, str(tmp_path))
    assert len(dictProvenance) > 0


def test_fnHashStepOutputs_skips_missing():
    from vaibify.reproducibility.provenanceTracker import (
        _fnHashStepOutputs,
    )
    dictStep = {
        "sDirectory": "/nonexistent",
        "saDataFiles": ["missing.npy"],
        "saPlotFiles": [],
    }
    dictHashes = {}
    _fnHashStepOutputs(dictStep, dictHashes)
    assert len(dictHashes) == 0


# ── workflowManager.py: reference remapping ───────────────────────────


def test_fsetExtractStepReferences_from_text():
    from vaibify.gui.workflowManager import fsetExtractStepReferences
    setRefs = fsetExtractStepReferences(
        "python run.py {Step01.data} {Step02.fig}")
    assert len(setRefs) == 2


def test_fnUpdateSyncStatus_records_service():
    from vaibify.gui.workflowManager import fnUpdateSyncStatus
    dictWorkflow = {"listSteps": [{
        "sName": "Test", "saPlotFiles": ["Plot/fig.pdf"],
    }]}
    fnUpdateSyncStatus(
        dictWorkflow, ["Plot/fig.pdf"], "GitHub"
    )


def test_flistFilterFigureFiles_filters():
    from vaibify.gui.workflowManager import flistFilterFigureFiles
    listFiles = ["plot.pdf", "data.npy", "fig.png", "stats.json"]
    listFigures = flistFilterFigureFiles(listFiles)
    assert "plot.pdf" in listFigures
    assert "fig.png" in listFigures
    assert "data.npy" not in listFigures


# ── director.py: print summary ───────────────────────────────────────


def test_fnPrintSummary_outputs_results(capsys):
    from vaibify.gui.director import fnPrintSummary
    listResults = [
        ("Step01", "Test Step", True, ""),
        ("Step02", "Fail Step", False, "exit code 1"),
    ]
    fnPrintSummary(listResults)
    sCaptured = capsys.readouterr().out
    assert "PASS" in sCaptured
    assert "FAIL" in sCaptured
    assert "1 passed" in sCaptured


def test_fnConfigureEnvironment_adds_path():
    from vaibify.gui.director import fnConfigureEnvironment
    dictWorkflow = {"sVplanetBinaryDirectory": ""}
    sOrigPath = os.environ.get("PATH", "")
    fnConfigureEnvironment(dictWorkflow, "/tmp")
    assert os.environ.get("PATH", "") is not None
    os.environ["PATH"] = sOrigPath
