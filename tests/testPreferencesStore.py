"""Tests for vaibify.config.preferencesStore."""

import json
import os
from datetime import datetime

import pytest

from vaibify.config import preferencesStore


@pytest.fixture(autouse=True)
def fixtureIsolatePreferences(tmp_path, monkeypatch):
    """Redirect the preferences store to a temp directory for every test."""
    sPreferencesDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        preferencesStore, "_S_PREFERENCES_DIRECTORY",
        sPreferencesDirectory,
    )
    monkeypatch.setattr(
        preferencesStore, "_S_PREFERENCES_PATH",
        os.path.join(sPreferencesDirectory, "preferences.json"),
    )
    monkeypatch.setattr(
        preferencesStore, "_S_LOCK_PATH",
        os.path.join(sPreferencesDirectory, "preferences.lock"),
    )


def testMissingFileReadsAsEmpty():
    assert preferencesStore.fdictLoadPreferences() == {
        "dictHostWarningAcknowledged": {},
    }


def testAcknowledgementRoundTrip(tmp_path):
    sProjectDirectory = str(tmp_path / "myProject")
    os.makedirs(sProjectDirectory)
    assert not preferencesStore.fbHostWarningAcknowledged(
        sProjectDirectory,
    )
    preferencesStore.fnRecordHostWarningAcknowledged(sProjectDirectory)
    assert preferencesStore.fbHostWarningAcknowledged(sProjectDirectory)


def testAcknowledgementTimestampIsIsoUtc(tmp_path):
    sProjectDirectory = str(tmp_path / "myProject")
    os.makedirs(sProjectDirectory)
    preferencesStore.fnRecordHostWarningAcknowledged(sProjectDirectory)
    dictAcknowledged = preferencesStore.fdictLoadPreferences()[
        "dictHostWarningAcknowledged"
    ]
    sTimestampIso = dictAcknowledged[
        os.path.realpath(sProjectDirectory)
    ]
    dtParsed = datetime.fromisoformat(sTimestampIso)
    assert dtParsed.utcoffset() is not None
    assert dtParsed.utcoffset().total_seconds() == 0


def testSymlinkedAliasReadsAsAcknowledged(tmp_path):
    """Keying is by realpath: an alias of an acknowledged directory hits."""
    sRealDirectory = str(tmp_path / "realProject")
    os.makedirs(sRealDirectory)
    sAliasDirectory = str(tmp_path / "aliasProject")
    os.symlink(sRealDirectory, sAliasDirectory)
    preferencesStore.fnRecordHostWarningAcknowledged(sRealDirectory)
    assert preferencesStore.fbHostWarningAcknowledged(sAliasDirectory)


def testRecordingViaAliasAcknowledgesTheRealDirectory(tmp_path):
    sRealDirectory = str(tmp_path / "realProject")
    os.makedirs(sRealDirectory)
    sAliasDirectory = str(tmp_path / "aliasProject")
    os.symlink(sRealDirectory, sAliasDirectory)
    preferencesStore.fnRecordHostWarningAcknowledged(sAliasDirectory)
    assert preferencesStore.fbHostWarningAcknowledged(sRealDirectory)
    dictAcknowledged = preferencesStore.fdictLoadPreferences()[
        "dictHostWarningAcknowledged"
    ]
    assert list(dictAcknowledged) == [os.path.realpath(sRealDirectory)]


def testSameBasenameDifferentDirectoryIsNotAcknowledged(tmp_path):
    """A reused name must not suppress the warning for another directory."""
    sFirstDirectory = str(tmp_path / "alpha" / "myProject")
    sSecondDirectory = str(tmp_path / "beta" / "myProject")
    os.makedirs(sFirstDirectory)
    os.makedirs(sSecondDirectory)
    preferencesStore.fnRecordHostWarningAcknowledged(sFirstDirectory)
    assert preferencesStore.fbHostWarningAcknowledged(sFirstDirectory)
    assert not preferencesStore.fbHostWarningAcknowledged(
        sSecondDirectory,
    )


def testCorruptFileReadsAsEmpty():
    os.makedirs(preferencesStore._S_PREFERENCES_DIRECTORY)
    with open(
        preferencesStore._S_PREFERENCES_PATH, "w",
    ) as fileHandle:
        fileHandle.write("{ not valid json !")
    assert preferencesStore.fdictLoadPreferences() == {
        "dictHostWarningAcknowledged": {},
    }


def testWronglyTypedFileReadsAsEmpty():
    os.makedirs(preferencesStore._S_PREFERENCES_DIRECTORY)
    with open(
        preferencesStore._S_PREFERENCES_PATH, "w",
    ) as fileHandle:
        json.dump(["not", "a", "dict"], fileHandle)
    assert preferencesStore.fdictLoadPreferences() == {
        "dictHostWarningAcknowledged": {},
    }


def testRecordingRecoversFromACorruptFile(tmp_path):
    os.makedirs(preferencesStore._S_PREFERENCES_DIRECTORY)
    with open(
        preferencesStore._S_PREFERENCES_PATH, "w",
    ) as fileHandle:
        fileHandle.write("garbage")
    sProjectDirectory = str(tmp_path / "myProject")
    os.makedirs(sProjectDirectory)
    preferencesStore.fnRecordHostWarningAcknowledged(sProjectDirectory)
    assert preferencesStore.fbHostWarningAcknowledged(sProjectDirectory)


def testSequentialRecordsBothPersist(tmp_path):
    """Read-modify-write under the lock preserves earlier entries."""
    sFirstDirectory = str(tmp_path / "firstProject")
    sSecondDirectory = str(tmp_path / "secondProject")
    os.makedirs(sFirstDirectory)
    os.makedirs(sSecondDirectory)
    preferencesStore.fnRecordHostWarningAcknowledged(sFirstDirectory)
    preferencesStore.fnRecordHostWarningAcknowledged(sSecondDirectory)
    dictAcknowledged = preferencesStore.fdictLoadPreferences()[
        "dictHostWarningAcknowledged"
    ]
    assert os.path.realpath(sFirstDirectory) in dictAcknowledged
    assert os.path.realpath(sSecondDirectory) in dictAcknowledged


def testWriteIsAtomicAndLeavesNoTempFiles(tmp_path):
    sProjectDirectory = str(tmp_path / "myProject")
    os.makedirs(sProjectDirectory)
    preferencesStore.fnRecordHostWarningAcknowledged(sProjectDirectory)
    listLeftovers = [
        sName
        for sName in os.listdir(
            preferencesStore._S_PREFERENCES_DIRECTORY,
        )
        if sName.endswith(".tmp")
    ]
    assert listLeftovers == []
    with open(
        preferencesStore._S_PREFERENCES_PATH, "r",
    ) as fileHandle:
        dictOnDisk = json.load(fileHandle)
    assert os.path.realpath(sProjectDirectory) in (
        dictOnDisk["dictHostWarningAcknowledged"]
    )


def testARaisingMutatorAbandonsTheWrite(tmp_path):
    """The lock pattern lets a mutator raise without touching the file."""
    sProjectDirectory = str(tmp_path / "myProject")
    os.makedirs(sProjectDirectory)
    preferencesStore.fnRecordHostWarningAcknowledged(sProjectDirectory)
    dictBefore = preferencesStore.fdictLoadPreferences()

    def fnRaiseInsteadOfMutating(dictPreferences):
        raise ValueError("abandon this write")

    with pytest.raises(ValueError):
        preferencesStore._fnMutatePreferencesLocked(
            fnRaiseInsteadOfMutating,
        )
    assert preferencesStore.fdictLoadPreferences() == dictBefore
