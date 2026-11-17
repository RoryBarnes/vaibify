"""The weekly published-project reproduction lane stays what it claims.

It reproduces ONE real published project in its author's environment on
a native runner. It is not required, not event-driven, and the only
place vaibify's own tooling names that project.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest
import yaml

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_WORKFLOW = (
    PATH_REPOSITORY / ".github" / "workflows" / "publishedReproduction.yml"
)
S_PROJECT = "aigreenhouse"


def _fdictLoadWorkflow():
    dictWorkflow = yaml.safe_load(PATH_WORKFLOW.read_text())
    # PyYAML reads the bare key `on` as the boolean True.
    dictWorkflow["on"] = dictWorkflow.pop(True, dictWorkflow.get("on"))
    return dictWorkflow


def test_it_is_scheduled_and_dispatched_and_never_event_driven():
    dictOn = _fdictLoadWorkflow()["on"]
    assert set(dictOn) == {"schedule", "workflow_dispatch"}


def test_it_runs_natively_on_amd64_and_cannot_pose_as_a_required_check():
    dictJob = _fdictLoadWorkflow()["jobs"]["reproduce"]
    assert dictJob["runs-on"] == "ubuntu-24.04"
    assert dictJob["name"].startswith("weekly:")
    assert not dictJob["name"].startswith(("unit:", "falsification:"))


def test_it_reproduces_from_the_published_source_with_a_rerun():
    sText = PATH_WORKFLOW.read_text()
    assert "vaibify reproduce" in sText
    assert "--from https://" in sText and "--rerun" in sText
    assert "--allow-emulation" not in sText


def _fsVerdictCheckScript():
    """Return the Python the verdict-assertion step feeds to stdin."""
    for dictStep in _fdictLoadWorkflow()["jobs"]["reproduce"]["steps"]:
        if dictStep.get("name") == "Assert the report's verdict":
            sRun = dictStep["run"]
            return sRun.split("<<'PYEOF'\n", 1)[1].rsplit("PYEOF", 1)[0]
    raise AssertionError("the workflow has no verdict-assertion step")


def _fiRunVerdictCheck(pathHome, sVerdict):
    if sVerdict is not None:
        pathReports = pathHome / ".vaibify" / "reproductions" / "reports"
        pathReports.mkdir(parents=True)
        (pathReports / "report.json").write_text(
            json.dumps({"sVerdict": sVerdict}))
    dictEnvironment = dict(os.environ, HOME=str(pathHome))
    return subprocess.run(
        [sys.executable, "-"], input=_fsVerdictCheckScript(), text=True,
        capture_output=True, env=dictEnvironment, cwd=str(PATH_REPOSITORY),
    ).returncode


@pytest.mark.falsification
@pytest.mark.parametrize("sVerdict, iExpectedExit", [
    ("byte-identical", 0),
    ("comparisons-passed", 1),
    ("not-reproduced", 1),
    ("reproduced", 1),
    (None, 1),
])
def test_only_a_byte_identical_report_passes_the_weekly_check(
    tmp_path, sVerdict, iExpectedExit,
):
    """The workflow's own step, run against a written report.

    It once compared against a verdict the policy had renamed, so every
    successful weekly reproduction read as a failure for five weeks while
    a test checking the step's TEXT stayed green. The ruling: this lane
    passes on a byte-identical reproduction and nothing weaker.

    Kills: comparing against the retired verdict string.
    """
    assert _fiRunVerdictCheck(tmp_path, sVerdict) == iExpectedExit


def test_this_workflow_is_the_only_shipped_place_naming_the_project():
    """An allow-PATH naming this one file; the guards stay unweakened."""
    listNaming = []
    for sRoot in (".github", "vaibify"):
        for pathFile in (PATH_REPOSITORY / sRoot).rglob("*"):
            if not pathFile.is_file() or pathFile.suffix in (
                ".pyc", ".png", ".ico", ".woff", ".woff2", ".gz", ".zst",
            ):
                continue
            try:
                sBody = pathFile.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if S_PROJECT in sBody.lower():
                listNaming.append(pathFile.relative_to(PATH_REPOSITORY).as_posix())
    assert listNaming == [".github/workflows/publishedReproduction.yml"]
