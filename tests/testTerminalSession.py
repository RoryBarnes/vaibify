"""Tests for vaibify.gui.terminalSession."""

from vaibify.gui.terminalSession import fsGenerateSessionId


def test_fsGenerateSessionId_is_uuid():
    sId = fsGenerateSessionId()
    assert len(sId) == 36
    assert sId.count("-") == 4


def test_fsGenerateSessionId_unique():
    sId1 = fsGenerateSessionId()
    sId2 = fsGenerateSessionId()
    assert sId1 != sId2
