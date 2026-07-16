"""Provenance recording for remote-pulled data files.

After a successful run of a step carrying ``listRemoteData``, the
runner hashes the declared pulled files in one container exec and
updates each record's ``sSha256``, stamping ``sRetrievedUtc`` only
when content changed or was hashed for the first time. Failures
leave records untouched — provenance never guesses.
"""

import asyncio

from vaibify.gui.pipelineRunner import (
    _fbApplyRemoteDataHashes,
    _fdictHashRemoteDataFiles,
    _fnRecordRemoteDataProvenance,
)


_S_SHA_A = "a" * 64
_S_SHA_B = "b" * 64


class _FakeDocker:
    """Returns a canned sha256sum transcript; records the command."""

    def __init__(self, sOutput):
        self.sOutput = sOutput
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        return (0, self.sOutput)


def _fdictRemoteStep(sSha256="", sRetrievedUtc=""):
    return {
        "sName": "Pull",
        "sDirectory": "pull",
        "listRemoteData": [{
            "sPath": "data/archive_pull.fits",
            "sSourceUrl": "https://archive.example/query",
            "sRetrievedUtc": sRetrievedUtc,
            "sSha256": sSha256,
        }],
    }


def _flistRunRecorder(connectionDocker, dictStep):
    """Drive the async recorder; return the emitted events."""
    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    asyncio.run(
        _fnRecordRemoteDataProvenance(
            connectionDocker, "cid", dictStep,
            {"sRepoRoot": "/workspace/repo"}, 3, fnCallback,
        )
    )
    return listEvents


def test_changed_content_updates_sha_and_stamps_timestamp():
    connectionDocker = _FakeDocker(
        f"{_S_SHA_B}  /workspace/repo/data/archive_pull.fits\n"
    )
    dictStep = _fdictRemoteStep(
        sSha256=_S_SHA_A, sRetrievedUtc="2026-01-01T00:00:00Z",
    )
    listEvents = _flistRunRecorder(connectionDocker, dictStep)
    dictRecord = dictStep["listRemoteData"][0]
    assert dictRecord["sSha256"] == _S_SHA_B
    assert dictRecord["sRetrievedUtc"] != "2026-01-01T00:00:00Z"
    assert len(listEvents) == 1
    assert listEvents[0]["sType"] == "remoteDataRecorded"
    assert listEvents[0]["iStepNumber"] == 3


def test_first_pull_stamps_new_record():
    connectionDocker = _FakeDocker(
        f"{_S_SHA_A}  /workspace/repo/data/archive_pull.fits\n"
    )
    dictStep = _fdictRemoteStep()
    _flistRunRecorder(connectionDocker, dictStep)
    dictRecord = dictStep["listRemoteData"][0]
    assert dictRecord["sSha256"] == _S_SHA_A
    assert dictRecord["sRetrievedUtc"].endswith("Z")


def test_unchanged_content_keeps_original_stamp_and_emits_nothing():
    connectionDocker = _FakeDocker(
        f"{_S_SHA_A}  /workspace/repo/data/archive_pull.fits\n"
    )
    dictStep = _fdictRemoteStep(
        sSha256=_S_SHA_A, sRetrievedUtc="2026-01-01T00:00:00Z",
    )
    listEvents = _flistRunRecorder(connectionDocker, dictStep)
    dictRecord = dictStep["listRemoteData"][0]
    assert dictRecord["sRetrievedUtc"] == "2026-01-01T00:00:00Z"
    assert listEvents == []


def test_missing_file_leaves_record_untouched():
    connectionDocker = _FakeDocker("")
    dictStep = _fdictRemoteStep(
        sSha256=_S_SHA_A, sRetrievedUtc="2026-01-01T00:00:00Z",
    )
    listEvents = _flistRunRecorder(connectionDocker, dictStep)
    dictRecord = dictStep["listRemoteData"][0]
    assert dictRecord["sSha256"] == _S_SHA_A
    assert dictRecord["sRetrievedUtc"] == "2026-01-01T00:00:00Z"
    assert listEvents == []


def test_step_without_remote_data_never_calls_docker():
    connectionDocker = _FakeDocker("")
    dictStep = {"sName": "Plain", "sDirectory": "plain"}
    listEvents = _flistRunRecorder(connectionDocker, dictStep)
    assert connectionDocker.listCommands == []
    assert listEvents == []


def test_hash_command_quotes_paths():
    """Paths are shell-quoted — a hostile filename cannot inject."""
    connectionDocker = _FakeDocker("")
    dictStep = {
        "sName": "Pull", "sDirectory": "pull",
        "listRemoteData": [
            {"sPath": "data/odd name;rm -rf.fits"},
        ],
    }
    _flistRunRecorder(connectionDocker, dictStep)
    assert len(connectionDocker.listCommands) == 1
    sCommand = connectionDocker.listCommands[0]
    sQuotedPath = "'/workspace/repo/data/odd name;rm -rf.fits'"
    assert sQuotedPath in sCommand
    assert ";rm" not in sCommand.replace(sQuotedPath, "")


def test_hash_parser_skips_malformed_lines():
    connectionDocker = _FakeDocker(
        "not-a-hash-line\n"
        "deadbeef  /workspace/repo/data/short-hash.fits\n"
        f"{_S_SHA_A}  /elsewhere/data/outside.fits\n"
        f"{_S_SHA_B}  /workspace/repo/data/good.fits\n"
    )
    dictResult = _fdictHashRemoteDataFiles(
        connectionDocker, "cid", "/workspace/repo",
        ["data/good.fits"],
    )
    assert dictResult == {"data/good.fits": _S_SHA_B}


def test_apply_hashes_reports_no_change_for_empty_map():
    dictStep = _fdictRemoteStep(sSha256=_S_SHA_A)
    assert _fbApplyRemoteDataHashes(dictStep, {}) is False
