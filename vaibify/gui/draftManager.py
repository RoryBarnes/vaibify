"""Vaibify draft persistence — file-content backup written to the project repo.

When a researcher is editing a text file in the dashboard, the
in-memory textarea can be destroyed by many events (figure render,
page reload, browser crash, accidental tab close, etc.). To survive
these, the frontend mirrors each edit to ``localStorage`` and, after a
longer debounce, to a JSON blob on disk under
``<sProjectRepoPath>/.vaibify/drafts/<workflowSlug>/``.

This module owns the path layout, JSON shape, and serialization
helpers; the route layer (:mod:`vaibify.gui.routes.draftRoutes`) owns
the HTTP surface and the docker-container plumbing.

A draft JSON document has the shape::

    {
      "sFilePath":    <repo-relative or container-absolute path being edited>,
      "sWorkdir":     <step working directory at edit time, may be empty>,
      "sContent":     <textarea value at last autosave>,
      "iTimestampMs": <wall-clock milliseconds since epoch>,
      "sBaseHash":    <sha256 hex of the file content the user opened>
    }

The filename is the sha256 of ``<sWorkdir>:<sFilePath>`` so paths with
``/`` or unicode produce a flat, collision-free filename.
"""

import hashlib
import json
import posixpath
import time

from .fileStatusManager import fsWorkflowSlugFromPath


__all__ = [
    "I_MAX_DRAFT_CONTENT_BYTES",
    "I_DRAFT_RETENTION_DAYS",
    "fdictParseDraftPayload",
    "fjsonBuildDraftPayload",
    "fsDraftDirectory",
    "fsDraftFilename",
    "fsDraftPath",
    "fsHashContent",
]


I_MAX_DRAFT_CONTENT_BYTES = 16 * 1024 * 1024
I_DRAFT_RETENTION_DAYS = 30


def fsHashContent(sContent):
    """Return the sha256 hex digest of ``sContent`` encoded as UTF-8."""
    return hashlib.sha256(sContent.encode("utf-8")).hexdigest()


def fsDraftFilename(sFilePath, sWorkdir):
    """Return the draft filename for a single (file, workdir) pair.

    Returning the sha256 of ``<sWorkdir>:<sFilePath>`` avoids encoding
    issues with ``/`` in container paths and produces a fixed-length
    name that any filesystem accepts.
    """
    sKey = (sWorkdir or "") + ":" + (sFilePath or "")
    sDigest = hashlib.sha256(sKey.encode("utf-8")).hexdigest()
    return sDigest + ".json"


def fsDraftDirectory(sProjectRepoPath, sWorkflowPath):
    """Return the per-workflow draft directory under the project repo.

    Returns an empty string when either argument is empty so callers
    can short-circuit cleanly — mirrors the convention used by
    :func:`vaibify.gui.fileStatusManager.fnCollectMarkerPathsByStep`.
    """
    sSlug = fsWorkflowSlugFromPath(sWorkflowPath)
    if not sProjectRepoPath or not sSlug:
        return ""
    return posixpath.join(
        sProjectRepoPath, ".vaibify", "drafts", sSlug,
    )


def fsDraftPath(sProjectRepoPath, sWorkflowPath, sFilePath, sWorkdir):
    """Return the absolute draft path for one (file, workdir) pair."""
    sDirectory = fsDraftDirectory(sProjectRepoPath, sWorkflowPath)
    if not sDirectory:
        return ""
    return posixpath.join(
        sDirectory, fsDraftFilename(sFilePath, sWorkdir),
    )


def fjsonBuildDraftPayload(sFilePath, sWorkdir, sContent, sBaseHash):
    """Return the JSON-encoded payload to write to a draft file."""
    dictPayload = {
        "sFilePath": sFilePath,
        "sWorkdir": sWorkdir or "",
        "sContent": sContent,
        "iTimestampMs": int(time.time() * 1000),
        "sBaseHash": sBaseHash or "",
    }
    return json.dumps(dictPayload, ensure_ascii=False)


def fdictParseDraftPayload(sBody):
    """Decode a draft JSON blob and normalize missing fields."""
    dictRaw = json.loads(sBody)
    return {
        "sFilePath": dictRaw.get("sFilePath", ""),
        "sWorkdir": dictRaw.get("sWorkdir", ""),
        "sContent": dictRaw.get("sContent", ""),
        "iTimestampMs": int(dictRaw.get("iTimestampMs", 0)),
        "sBaseHash": dictRaw.get("sBaseHash", ""),
    }
