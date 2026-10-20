"""The typed hash batch a container double answers from its own files.

The Level 1 gate hashes every script a dependency scan read, a clean
scan included, through the connection's ``fdictHashContainerRepoPaths``
(the real connection and the router both declare it). A double that
serves scripts through ``fbaFetchFile`` but lacks this operation fails
every poll of a scanned workflow, so the doubles share one answer: the
real entry shape, computed from the very bytes the double serves.
"""

import hashlib
import posixpath


__all__ = ["TypedHashFromServedBytes"]


class TypedHashFromServedBytes:
    """Mixin: hash repo paths from the bytes ``fbaFetchFile`` serves."""

    def fdictHashContainerRepoPaths(self, sContainerId, sRootPath,
                                    listRelPaths):
        dictOut = {}
        for sRelPath in listRelPaths:
            dictEntry = {"sSha256": None, "sSymlinkSegment": None,
                         "bEscapesRoot": False}
            try:
                dictEntry["sSha256"] = hashlib.sha256(self.fbaFetchFile(
                    sContainerId, posixpath.join(sRootPath, sRelPath),
                )).hexdigest()
            except FileNotFoundError:
                dictEntry["bMissing"] = True
            dictOut[sRelPath] = dictEntry
        return dictOut
