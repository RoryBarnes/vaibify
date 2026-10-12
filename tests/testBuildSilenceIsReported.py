"""A build that prints nothing for a long while says so in the progress pane.

A reader's Claude Code install once spun under emulation for ninety
minutes while the pane kept showing "this is slow". These tests run the
real streaming loop and the real watcher thread, with the threshold
shortened, against a build that goes quiet between two lines.
"""

import time
from types import SimpleNamespace

import pytest

from vaibify.docker import imageBuilder


def _fiterLinesWithAPause(fPauseSeconds):
    yield "#5 [2/3] RUN ./install.sh\n"
    time.sleep(fPauseSeconds)
    yield "#5 DONE 1.2s\n"


def _flistStreamThroughTheSink(fPauseSeconds):
    listSunk = []
    imageBuilder.fnSetThreadBuildLineSink(listSunk.append)
    try:
        imageBuilder._fsStreamAndCaptureStderr(
            SimpleNamespace(stderr=_fiterLinesWithAPause(fPauseSeconds)),
        )
    finally:
        imageBuilder.fnSetThreadBuildLineSink(None)
    return listSunk


@pytest.mark.falsification
def test_a_quiet_build_is_reported_between_its_lines(monkeypatch):
    """Kills: the watcher never noticing the silence."""
    monkeypatch.setattr(imageBuilder, "_F_BUILD_SILENCE_NOTICE_SECONDS", 0.3)
    listSunk = _flistStreamThroughTheSink(1.0)
    assert listSunk[0] == "#5 [2/3] RUN ./install.sh\n"
    assert listSunk[-1] == "#5 DONE 1.2s\n"
    listNotices = [s for s in listSunk if s.startswith("[vaib] The build")]
    assert listNotices, listSunk
    assert "Ctrl-C in the terminal running the vaibify hub" in listNotices[0]
    assert len(listNotices) <= 3


def test_a_build_that_keeps_printing_is_not_reported(monkeypatch):
    """A line inside the threshold resets it; the watcher ends with the build."""
    monkeypatch.setattr(imageBuilder, "_F_BUILD_SILENCE_NOTICE_SECONDS", 5.0)
    listSunk = _flistStreamThroughTheSink(0.2)
    assert listSunk == ["#5 [2/3] RUN ./install.sh\n", "#5 DONE 1.2s\n"]
