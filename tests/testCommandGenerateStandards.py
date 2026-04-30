"""Tests for the vaibify generate-standards CLI subcommand."""

import json
import os

import numpy as np
import pytest

pytest.importorskip("click")
from click.testing import CliRunner  # noqa: E402

from vaibify.cli.commandGenerateStandards import generate_standards


@pytest.fixture
def fixtureStepDirWithStandards(tmp_path):
    """Build a step directory with a standards file and matching .npy data."""
    sStepDir = os.path.join(tmp_path, "step")
    os.makedirs(os.path.join(sStepDir, "tests"))
    np.save(os.path.join(sStepDir, "samples.npy"),
            np.array([10.0, 20.0, 30.0]))
    sStandardsPath = os.path.join(sStepDir, "tests",
                                  "quantitative_standards.json")
    with open(sStandardsPath, "w") as fileHandle:
        json.dump({
            "fDefaultRtol": 1e-12,
            "listStandards": [{
                "sName": "fmean", "sDataFile": "samples.npy",
                "sAccessPath": "index:mean", "fValue": 0.0,
                "sUnit": "",
            }],
        }, fileHandle)
    return sStepDir, sStandardsPath


def test_generate_standards_refresh_existing(fixtureStepDirWithStandards):
    """--step-dir refreshes fValue when a standards file already exists."""
    sStepDir, sStandardsPath = fixtureStepDirWithStandards
    runner = CliRunner()
    result = runner.invoke(generate_standards, ["--step-dir", sStepDir])
    assert result.exit_code == 0, result.output
    with open(sStandardsPath) as fileHandle:
        dictAfter = json.load(fileHandle)
    assert dictAfter["listStandards"][0]["fValue"] == pytest.approx(20.0)


def test_generate_standards_no_args_exits_nonzero():
    """Missing --step-dir AND --workflow combination is an error."""
    runner = CliRunner()
    result = runner.invoke(generate_standards, [])
    assert result.exit_code == 2


def test_generate_standards_generate_fresh(tmp_path):
    """When no standards file exists, infer data files and generate one."""
    sStepDir = os.path.join(tmp_path, "step")
    os.makedirs(os.path.join(sStepDir, "tests"))
    np.save(os.path.join(sStepDir, "out.npy"),
            np.array([[1.0, 2.0], [3.0, 4.0]]))
    runner = CliRunner()
    result = runner.invoke(generate_standards,
                           ["--step-dir", sStepDir, "--rtol", "1e-9"])
    assert result.exit_code == 0, result.output
    sStandardsPath = os.path.join(
        sStepDir, "tests", "quantitative_standards.json")
    assert os.path.isfile(sStandardsPath)
    with open(sStandardsPath) as fileHandle:
        dictResult = json.load(fileHandle)
    assert dictResult["fDefaultRtol"] == 1e-9
    assert len(dictResult["listStandards"]) > 0
