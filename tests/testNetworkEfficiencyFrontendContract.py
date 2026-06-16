"""Frontend contract checks for the network wire-efficiency slice.

These are string-presence tests: the repository does not execute the
JavaScript modules, so we assert that the renderer, polling, file
operations, repos panel, and resource monitor scripts carry the
expected scaling-fix surfaces.

Mirrors the pattern used by ``testReposPollingFrontendContract.py``.
"""

import os
import re


_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadStaticFile(sName):
    sPath = os.path.join(_sStaticDir, sName)
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def test_pipeline_runner_handles_output_batch_event():
    sSource = _fsReadStaticFile("scriptPipelineRunner.js")
    assert 'sType === "outputBatch"' in sSource, (
        "Pipeline runner must dispatch the new outputBatch event"
    )
    assert 'sType === "output"' in sSource, (
        "Per-line output events must remain as a back-compat fallback"
    )


def test_polling_drops_unconditional_badge_fetch():
    sSource = _fsReadStaticFile("scriptPolling.js")
    iBody = sSource.find("_fnPollFileChangesBody")
    iEnd = sSource.find("async function _fnPollFileChanges(", iBody)
    sBody = sSource[iBody:iEnd]
    sCodeOnly = re.sub(r"/\*.*?\*/", "", sBody, flags=re.DOTALL)
    assert "VaibifyGitBadges.fnRefresh" not in sCodeOnly, (
        "file-status tick must no longer call the badge refresh "
        "outside of the sync-epoch path"
    )


def test_file_operations_uses_batched_exist_endpoint():
    sSource = _fsReadStaticFile("scriptFileOperations.js")
    assert "/exist" in sSource and "saRelativePaths" in sSource, (
        "Batched existence endpoint must be wired into the renderer"
    )


def test_websocket_module_does_exponential_reconnect():
    sSource = _fsReadStaticFile("scriptWebSocket.js")
    assert "_laReconnectDelaysSeconds" in sSource
    assert "[1, 2, 4, 8, 16]" in sSource
    assert "_fnAttemptReconnect" in sSource


def test_repos_panel_visibility_gates_polling():
    sSource = _fsReadStaticFile("scriptReposPanel.js")
    assert "_fbReposPanelIsVisible" in sSource
    assert "_fnEnsurePollingMatchesVisibility" in sSource
    assert "visibilitychange" in sSource


def test_resource_monitor_has_inflight_guard():
    sSource = _fsReadStaticFile("scriptResourceMonitor.js")
    assert "_bMonitorInFlight" in sSource
    pattern = re.compile(
        r"if\s*\(\s*_bMonitorInFlight\s*\)\s*return", re.MULTILINE,
    )
    assert pattern.search(sSource), (
        "Resource monitor must skip the tick when a request is "
        "already in flight"
    )
