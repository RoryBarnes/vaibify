"""Coverage tests for ``fnValidatePathWithinRoot`` containment guard.

These close mutation-testing holes in the host-path traversal guard
cited in CLAUDE.md (file pulls, directory browsing, sync, workspace
mounts). The guard must reject paths that merely embed the root as an
interior substring, normalize the allowed root before comparison, and
return the canonicalized path so callers key status/staleness lookups
on a stable form.
"""

import pytest
from fastapi import HTTPException

from vaibify.gui.pipelineServer import fnValidatePathWithinRoot


@pytest.mark.parametrize(
    "sOutsidePath",
    [
        "/etc/workspace/secret",
        "/var/workspace/x",
    ],
)
def testRejectsRootEmbeddedAsInteriorSubstring(sOutsidePath):
    """Paths containing the root as an interior segment must 403.

    A substring (rather than prefix) containment check would admit
    '/etc/workspace/secret' under root '/workspace', escaping the
    sandbox.
    """
    with pytest.raises(HTTPException) as excinfo:
        fnValidatePathWithinRoot(sOutsidePath, "/workspace")
    assert excinfo.value.status_code == 403


def testNormalizesTrailingSlashRoot():
    """A trailing-slash allowed root must still admit legitimate subpaths.

    Without normpath on the root, the boundary becomes '/workspace//'
    and every real subpath gets a spurious 403.
    """
    sResult = fnValidatePathWithinRoot(
        "/workspace/project/file.txt", "/workspace/"
    )
    assert sResult == "/workspace/project/file.txt"


def testNormalizesDotBearingRoot():
    """A root containing '.' must be canonicalized before comparison."""
    sResult = fnValidatePathWithinRoot(
        "/workspace/logs/run.txt", "/workspace/./logs"
    )
    assert sResult == "/workspace/logs/run.txt"


def testReturnsNormalizedPathNotRawInput():
    """The return value must be the canonicalized path, not raw input.

    Callers key path-based status/staleness lookups on the return, so a
    non-canonical '/workspace/./project//file.txt' would desync them.
    """
    sResult = fnValidatePathWithinRoot(
        "/workspace/./project//file.txt", "/workspace"
    )
    assert sResult == "/workspace/project/file.txt"
