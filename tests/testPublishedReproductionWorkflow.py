"""The weekly published-project reproduction lane stays what it claims.

It reproduces ONE real published project in its author's environment on
a native runner. It is not required, not event-driven, and the only
place vaibify's own tooling names that project.
"""

import pathlib

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
    assert '== "reproduced"' in sText


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
