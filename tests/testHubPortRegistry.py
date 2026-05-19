"""Tests for vaibify.config.hubPortRegistry."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest


def _fnPatchHubDir(sDir):
    """Patch the hub-port file path to live inside sDir."""
    return patch(
        "vaibify.config.hubPortRegistry._S_VAIBIFY_DIRECTORY", sDir,
    )


def test_fiReadPersistedHubPort_returns_zero_when_file_missing():
    from vaibify.config.hubPortRegistry import fiReadPersistedHubPort
    with tempfile.TemporaryDirectory() as sTmp:
        with _fnPatchHubDir(sTmp):
            assert fiReadPersistedHubPort() == 0


def test_fiReadPersistedHubPort_returns_persisted_value():
    from vaibify.config.hubPortRegistry import (
        fnPersistHubPort, fiReadPersistedHubPort,
    )
    with tempfile.TemporaryDirectory() as sTmp:
        with _fnPatchHubDir(sTmp):
            fnPersistHubPort(8077)
            assert fiReadPersistedHubPort() == 8077


def test_fiReadPersistedHubPort_returns_zero_on_invalid_json():
    from vaibify.config.hubPortRegistry import (
        fiReadPersistedHubPort, fsHubPortPath,
    )
    with tempfile.TemporaryDirectory() as sTmp:
        with _fnPatchHubDir(sTmp):
            with open(fsHubPortPath(), "w") as fileHandle:
                fileHandle.write("not json")
            assert fiReadPersistedHubPort() == 0


def test_fiReadPersistedHubPort_rejects_out_of_range_port():
    from vaibify.config.hubPortRegistry import (
        fiReadPersistedHubPort, fsHubPortPath,
    )
    with tempfile.TemporaryDirectory() as sTmp:
        with _fnPatchHubDir(sTmp):
            with open(fsHubPortPath(), "w") as fileHandle:
                json.dump({"iPort": 22}, fileHandle)
            assert fiReadPersistedHubPort() == 0


def test_fiReadPersistedHubPort_rejects_non_integer_port():
    from vaibify.config.hubPortRegistry import (
        fiReadPersistedHubPort, fsHubPortPath,
    )
    with tempfile.TemporaryDirectory() as sTmp:
        with _fnPatchHubDir(sTmp):
            with open(fsHubPortPath(), "w") as fileHandle:
                json.dump({"iPort": "8050"}, fileHandle)
            assert fiReadPersistedHubPort() == 0


def test_fnPersistHubPort_writes_payload_with_metadata():
    from vaibify.config.hubPortRegistry import (
        fnPersistHubPort, fsHubPortPath,
    )
    with tempfile.TemporaryDirectory() as sTmp:
        with _fnPatchHubDir(sTmp):
            fnPersistHubPort(8061)
            with open(fsHubPortPath(), "r") as fileHandle:
                dictPayload = json.load(fileHandle)
    assert dictPayload["iPort"] == 8061
    assert dictPayload["iPid"] == os.getpid()
    assert "sStartedIso" in dictPayload


def test_fnPersistHubPort_silently_skips_invalid_ports():
    from vaibify.config.hubPortRegistry import (
        fnPersistHubPort, fsHubPortPath,
    )
    with tempfile.TemporaryDirectory() as sTmp:
        with _fnPatchHubDir(sTmp):
            fnPersistHubPort(0)
            assert not os.path.exists(fsHubPortPath())
            fnPersistHubPort(80)
            assert not os.path.exists(fsHubPortPath())


def test_fnPersistHubPort_swallows_write_failure(capsys):
    from vaibify.config import hubPortRegistry
    with patch.object(
        hubPortRegistry, "_fnAtomicWriteHubPort",
        side_effect=OSError("read-only"),
    ):
        hubPortRegistry.fnPersistHubPort(8050)
    sErr = capsys.readouterr().err
    assert "could not persist hub port" in sErr


def test_fnPersistHubPort_atomic_replace_overwrites_existing():
    from vaibify.config.hubPortRegistry import (
        fnPersistHubPort, fiReadPersistedHubPort,
    )
    with tempfile.TemporaryDirectory() as sTmp:
        with _fnPatchHubDir(sTmp):
            fnPersistHubPort(8050)
            fnPersistHubPort(9090)
            assert fiReadPersistedHubPort() == 9090
