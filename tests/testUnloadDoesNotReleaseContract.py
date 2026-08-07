"""Closing or reloading a tab must not release a running container.

pagehide fires on reload and navigation, not only a permanent close, so
sending an authenticated /release from it would drop a running container
on a mere reload — the F5 hazard. Abandonment is decided by the WebSocket
closing without a reconnect and the grace reaper, never by an unload
beacon.

JavaScript is not executed by the Python suite; these are structural
assertions in the established frontend-contract pattern. The behaviour is
additionally verified in the browser lane.
"""

import os
import re

_S_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadApp():
    sPath = os.path.join(_S_STATIC_DIR, "scriptApplication.js")
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def test_no_unload_handler_sends_a_release_beacon():
    """No pagehide/beforeunload/unload path may POST or beacon a release."""
    sSource = _fsReadApp()
    # A sendBeacon to the release route is the specific regression.
    assert not re.search(r"sendBeacon\([^)]*release", sSource), (
        "an unload handler still beacons a container release; pagehide "
        "must not release (it fires on reload/navigation too)"
    )
    # The dedicated release-on-unload helper must be gone.
    assert "fnReleaseActiveContainerOnUnload" not in sSource


def test_pagehide_handler_only_stops_polling():
    """The pagehide handler stops polling and does nothing container-fatal."""
    sSource = _fsReadApp()
    iPageHide = sSource.find('"pagehide"')
    assert iPageHide != -1, "pagehide handler missing"
    # Bound the handler body at the next addEventListener/document call.
    iEnd = sSource.find("addEventListener", iPageHide + 1)
    sBody = sSource[iPageHide:iEnd if iEnd != -1 else len(sSource)]
    assert "fnStopAllHubPolling" in sBody
    assert "/release" not in sBody and "sendBeacon" not in sBody, (
        "the pagehide handler must not call a release path or beacon"
    )
