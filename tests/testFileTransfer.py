"""Tests for vaibify.docker.fileTransfer.fsResolveContainerPath."""

from vaibify.docker.fileTransfer import fsResolveContainerPath


def test_fsResolveContainerPath_basic():
    sResult = fsResolveContainerPath("data/output.csv", "/workspace")
    assert sResult == "/workspace/data/output.csv"


def test_fsResolveContainerPath_nested():
    sResult = fsResolveContainerPath("a/b/c.txt", "/workspace")
    assert sResult == "/workspace/a/b/c.txt"


def test_fsResolveContainerPath_single_file():
    sResult = fsResolveContainerPath("file.txt", "/workspace")
    assert sResult == "/workspace/file.txt"


def test_fsResolveContainerPath_custom_root():
    sResult = fsResolveContainerPath("src/main.py", "/home/user")
    assert sResult == "/home/user/src/main.py"
