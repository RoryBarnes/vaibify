"""Tests for Supervised-mode attribution events and permanent flags.

Falsification focus: flags must be permanent — a later clean pass
must not remove them, and a crafted "clear" (editing or deleting a
record) must break the hash chain detectably.
"""

from vaibify.gui.attributionLog import (
    S_ATTRIBUTION_EVENTS_PATH,
    fbAnyEventWithinWindow,
    fbSupervisionEnabled,
    fbVerifyFlagChain,
    flistLoadFlags,
    fnAppendAttributionEvent,
    fnAppendFlag,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


def _fdictSupervisedWorkflow():
    return {"dictAiProvenance": {"dictSupervision": {"bEnabled": True}}}


def test_supervision_enabled_reads_the_config_block():
    assert fbSupervisionEnabled(_fdictSupervisedWorkflow()) is True
    assert fbSupervisionEnabled({}) is False
    assert fbSupervisionEnabled(None) is False


def test_event_append_is_noop_when_unsupervised(tmp_path):
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendAttributionEvent(
        filesRepo, {}, "pipeline", "hub", "runAll",
    )
    assert not filesRepo.fbIsFile(S_ATTRIBUTION_EVENTS_PATH)


def test_event_append_and_window_check(tmp_path):
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendAttributionEvent(
        filesRepo, _fdictSupervisedWorkflow(), "pipeline", "hub",
        "runAll",
    )
    assert fbAnyEventWithinWindow(filesRepo) is True
    assert fbAnyEventWithinWindow(filesRepo, fWindowSeconds=0.0) is False


def test_flag_chain_survives_appends_and_detects_tampering(tmp_path):
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendFlag(filesRepo, "unattributed-modification", "fileA")
    fnAppendFlag(filesRepo, "unsupervised-gap", "digest changed")
    listFlags = flistLoadFlags(filesRepo)
    assert len(listFlags) == 2
    assert fbVerifyFlagChain(listFlags)
    listEdited = [dict(listFlags[0]), dict(listFlags[1])]
    listEdited[0]["sDetail"] = "innocent"
    assert not fbVerifyFlagChain(listEdited)
    assert not fbVerifyFlagChain(listFlags[1:])


def test_nothing_in_the_module_removes_flags(tmp_path):
    """A clean follow-up append leaves earlier flags in place."""
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    fnAppendFlag(filesRepo, "unattributed-modification", "fileA")
    fnAppendAttributionEvent(
        filesRepo, _fdictSupervisedWorkflow(), "pipeline", "hub",
        "runAll",
    )
    fnAppendFlag(filesRepo, "unattributed-modification", "fileB")
    listFlags = flistLoadFlags(filesRepo)
    assert [dictFlag["sDetail"] for dictFlag in listFlags] == [
        "fileA", "fileB",
    ]
    assert fbVerifyFlagChain(listFlags)
