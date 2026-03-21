"""Tests for vaibify.docker.volumeManager.fsGetVolumeName."""

from types import SimpleNamespace

from vaibify.docker.volumeManager import fsGetVolumeName


def test_fsGetVolumeName_format():
    config = SimpleNamespace(sProjectName="myproject")
    assert fsGetVolumeName(config) == "myproject-workspace"


def test_fsGetVolumeName_dash_in_name():
    config = SimpleNamespace(sProjectName="hab-zone")
    assert fsGetVolumeName(config) == "hab-zone-workspace"


def test_fsGetVolumeName_short_name():
    config = SimpleNamespace(sProjectName="x")
    assert fsGetVolumeName(config) == "x-workspace"
