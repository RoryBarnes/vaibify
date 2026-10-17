"""A reproduction record names the daemon's architecture, or says it cannot.

The platform is three facts kept apart: what the envelope requires, what
the obtained image is, and what the DAEMON is. The Verify Level 3 lane
wrote the third as a literal empty string into every reproduction record
-- a reader whose arm64 daemon ran an amd64 image under emulation got
``sDaemonArchitecture: ""``, which reads like a value and names nothing
(2026-10-12). The reproduce-a-published-project lane asks the daemon in
its acquisition; this lane never asked at all.

These tests drive the real shadow lifecycle (the gateway, the archive
copy-in, the identity-verified destroy) against a daemon whose containers
are real directories, then write the record through the shared writer
and read the JSON back off disk. The daemon's answer is ``arm64``, a
value nothing else in the fixture carries, so a record can only hold it
by having asked.
"""

import json
from unittest.mock import patch

import pytest

from tests.testRerunVerifiesWhatItRan import (
    S_CONTAINER_ID,
    S_OUTPUT_FILENAME,
    FakeDisposableDaemon,
    LocalShellContainer,
    _fdictWorkflowFor,
    _fnEnterAll,
    _fnPatchContainerLane,
    _fnSeedEnvelope,
    _fpathShadowCopyOf,
)
from vaibify.reproducibility import reproductionRecord
from vaibify.reproducibility import shadowRerun
from vaibify.reproducibility.repoFiles import ContainerRepoFiles


class _DaemonThatAnswersItsArchitecture(FakeDisposableDaemon):
    """A fake daemon whose ``version()`` reports the server's architecture."""

    def version(self):
        return {"Arch": "arm64", "Os": "linux"}


class _DaemonThatCannotBeAsked(FakeDisposableDaemon):
    """A fake daemon whose ``version()`` fails, as an unreachable one does."""

    def version(self):
        raise ConnectionError("the daemon did not answer")


@pytest.fixture
def pathContainerRepository(tmp_path, monkeypatch):
    """Return a seeded container repository with the shadow root on this host."""
    monkeypatch.setenv("HOME", str(tmp_path / "isolatedHome"))
    pathShadowRoot = tmp_path / "shadowRoot"
    pathShadowRoot.mkdir(parents=True)
    monkeypatch.setattr(
        shadowRerun, "S_SHADOW_WORKSPACE_ROOT", str(pathShadowRoot))
    return _fnSeedEnvelope(
        tmp_path / "containerVolume" / "workspace" / "ProjectRepo",
    )


def _fdictWriteReproductionRecord(pathContainerRepo, daemonDisposable):
    """Verify through the shadow, write a reproduction record, return it."""
    sWorkflowPath = str(
        pathContainerRepo / ".vaibify" / "workflows" / "project.json")
    dictWorkflow = _fdictWorkflowFor(pathContainerRepo, "Project")
    connectionContainer = LocalShellContainer()
    filesRepo = ContainerRepoFiles(
        connectionContainer, S_CONTAINER_ID, str(pathContainerRepo))

    async def _fiRunAllSteps(*args, **kwargs):
        (_fpathShadowCopyOf(pathContainerRepo) / S_OUTPUT_FILENAME
         ).write_text("answer = 42\n")
        return 0

    listPatches, _listRan = _fnPatchContainerLane(
        connectionContainer, [], {}, lambda: None, daemonDisposable,
    )
    fnStop = _fnEnterAll(listPatches)
    try:
        with patch(
            "vaibify.gui.pipelineRunner.fiRunAllSteps",
            side_effect=_fiRunAllSteps,
        ):
            dictOutcome = shadowRerun.fdictRerunAndVerifyThroughShadow(
                connectionContainer, S_CONTAINER_ID, dictWorkflow,
                sWorkflowPath, filesRepo,
            )
        listWritten = reproductionRecord.flistWriteVerificationOutcome(
            filesRepo, reproductionRecord.S_RECORD_KIND_REPRODUCTION,
            dictOutcome, 1.0, dictWorkflow, None,
        )
    finally:
        fnStop()
    sRecordPath = next(
        sPath for sPath in listWritten
        if sPath.startswith(".vaibify/reproductions/")
    )
    return json.loads((pathContainerRepo / sRecordPath).read_text())


@pytest.mark.falsification
def test_the_record_names_the_architecture_the_daemon_reported(
    pathContainerRepository,
):
    """The daemon that ran the shadow is asked, and its answer is recorded.

    Kills: the reproduction record writing a literal empty string for
    the daemon's architecture instead of the answer the lane obtained.
    """
    dictRecord = _fdictWriteReproductionRecord(
        pathContainerRepository, _DaemonThatAnswersItsArchitecture(),
    )
    assert dictRecord["sVerdict"] == "byte-identical"
    assert dictRecord["dictPlatform"]["sDaemonArchitecture"] == "arm64"
    assert "sDaemonArchitectureUnknownReason" not in dictRecord["dictPlatform"]


@pytest.mark.falsification
def test_an_unanswered_daemon_is_recorded_as_unknown_with_a_reason(
    pathContainerRepository,
):
    """A daemon that cannot be asked leaves a named unknown, never a bare blank.

    Kills: the record carrying an empty architecture with no reason
    beside it, which reads as a value rather than as a question nobody
    could answer.
    """
    dictRecord = _fdictWriteReproductionRecord(
        pathContainerRepository, _DaemonThatCannotBeAsked(),
    )
    dictPlatform = dictRecord["dictPlatform"]
    assert dictPlatform["sDaemonArchitecture"] == ""
    assert dictPlatform.get("sDaemonArchitectureUnknownReason"), (
        "an unknown daemon architecture must say why it is unknown"
    )
