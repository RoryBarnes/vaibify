"""Tests for vaibify.config.processLiveness."""

import multiprocessing
import os


def _fiSpawnDeadPid():
    """Return the PID of a forked child that has already exited."""
    contextFork = multiprocessing.get_context("fork")
    processChild = contextFork.Process(target=lambda: None)
    processChild.start()
    processChild.join(timeout=5)
    return processChild.pid


def test_fbIsProcessAlive_true_for_current_process():
    from vaibify.config.processLiveness import fbIsProcessAlive
    assert fbIsProcessAlive(os.getpid()) is True


def test_fbIsProcessAlive_false_for_exited_child():
    from vaibify.config.processLiveness import fbIsProcessAlive
    assert fbIsProcessAlive(_fiSpawnDeadPid()) is False


def test_fbIsProcessAlive_false_for_invalid_pids():
    from vaibify.config.processLiveness import fbIsProcessAlive
    assert fbIsProcessAlive(0) is False
    assert fbIsProcessAlive(-1) is False
    assert fbIsProcessAlive(None) is False
    assert fbIsProcessAlive("8050") is False
    assert fbIsProcessAlive(True) is False


def test_fbIsProcessAlive_true_on_permission_error(monkeypatch):
    """EPERM means the PID exists under another user: alive."""
    from vaibify.config import processLiveness

    def _fnRaisePermissionError(iPid, iSignal):
        raise PermissionError("operation not permitted")

    monkeypatch.setattr(processLiveness.os, "kill", _fnRaisePermissionError)
    assert processLiveness.fbIsProcessAlive(12345) is True
