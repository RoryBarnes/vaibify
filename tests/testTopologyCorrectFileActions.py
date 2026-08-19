"""Three locations, three actions, and one of them was a self-copy.

A file can be on the computer the researcher is sitting at, on the
machine running the backend, or in the environment the commands run in.
Until a browser could be somewhere else, the first two were always the
same machine and the distinction did not pay for itself.

"Pull to host" shipped ungated at both its entry points. In host mode
the execution host and the execution environment are ONE filesystem, so
it copied a file from a directory to the home directory of the same
machine and presented that as a transfer. Through a tunnel it is worse:
it reaches the remote machine while the researcher reads "host" as
theirs.

The streaming download route this now uses has existed, hardened and
tested, with no caller at all -- so the fix was wiring, not building.
"""

import pathlib

import pytest

from vaibify.gui.pipelineServer import fdictExecutionTopology

PATH_STATIC = (
    pathlib.Path(__file__).resolve().parent.parent
    / "vaibify" / "gui" / "static"
)


def _fsRead(sName):
    return (PATH_STATIC / sName).read_text(encoding="utf-8")


def test_host_mode_reports_one_filesystem():
    """The fact the gate needs, answered by the server.

    Takes the MODE, not a resource id: the function was extracted so
    it depends on nothing that depends on it, and the caller already
    knows the mode. Passing a resource id here would silently compare
    a container id to the string "host" and answer False forever --
    which is exactly the bug the extraction introduced at one of the
    two call sites and reading caught.
    """
    assert fdictExecutionTopology("host")["bSameFilesystem"] is True


def test_container_mode_reports_two_filesystems():
    """The symmetric half: a container workspace really is elsewhere."""
    assert fdictExecutionTopology("container")["bSameFilesystem"] is False


def test_a_resource_id_passed_by_mistake_does_not_read_as_host():
    """The failure mode the second call site had, pinned.

    It is silent: a container id is not "host", so the topology reports
    two filesystems and the gate simply never fires. Naming it here
    means the next person to change the signature sees the hazard.
    """
    assert fdictExecutionTopology(
        "a1b2c3d4e5f6",
    )["bSameFilesystem"] is False


def test_the_topology_is_structured_not_a_boolean():
    """A bare flag cannot grow a scheduled placement later."""
    dictTopology = fdictExecutionTopology("anything")
    assert isinstance(dictTopology, dict)
    assert "sExecutionPlacement" in dictTopology


def test_the_download_action_exists_and_is_exported():
    """The route had no caller at all; this is the caller."""
    sSource = _fsRead("scriptFilePull.js")
    assert "fnDownloadToThisComputer" in sSource
    assert "fnDownloadToThisComputer: fnDownloadToThisComputer" in sSource
    assert "/download/" in sSource, (
        "the action must use the streaming download route rather than "
        "inventing a second transfer path"
    )


def test_the_download_carries_its_token_in_the_query():
    """A browser navigation sends no headers; the carve-out is for this."""
    sSource = _fsRead("scriptFilePull.js")
    assert "sToken=" in sSource
    assert "encodeURIComponent" in sSource, (
        "a path segment or token that is not encoded breaks on the "
        "first space, which project names may now contain"
    )


def test_the_host_copy_is_renamed_for_the_machine_it_reaches():
    """"Pull to host" named the wrong machine from the observer's seat."""
    sPull = _fsRead("scriptFilePull.js")
    sSync = _fsRead("scriptSyncManager.js")
    assert "Copy to execution-host path" in sPull
    assert "Copy to execution-host path" in sSync
    assert "Pull to host" not in sPull, sPull[:0]
    assert "Pull to host" not in sSync


def test_the_host_copy_is_gated_at_both_entry_points():
    """Two callers, and only one of them was ever going to be checked."""
    sPull = _fsRead("scriptFilePull.js")
    assert "fbCopyToHostIsMeaningful" in sPull
    sSync = _fsRead("scriptSyncManager.js")
    assert "fbCopyToHostIsMeaningful()" in sSync, (
        "the sync menu must not offer a copy that is a self-copy"
    )
    sFiles = _fsRead("scriptFiles.js")
    assert "fnDownloadToThisComputer" in sFiles, (
        "the Files panel's right-click must not still mean 'copy to "
        "the backend's own home directory'"
    )


def test_the_gate_fails_open_when_the_topology_is_unknown():
    """Before a handshake lands, refusing a working action is worse.

    An unknown topology is the state a page is in for its first
    moments. Hiding the action then would be a bug the researcher
    experiences as flicker; offering it is what the code did for its
    whole life before this change.
    """
    sPull = _fsRead("scriptFilePull.js")
    iGate = sPull.index("function fbCopyToHostIsMeaningful")
    sGate = sPull[iGate:iGate + 600]
    assert "return true" in sGate, (
        "the gate must fall back to offering the action when it cannot "
        "tell, not to hiding it"
    )
