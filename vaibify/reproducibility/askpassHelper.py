"""Shared temp-file writer for askpass helper scripts.

``vaibify.reproducibility.githubAuth`` and
``vaibify.reproducibility.overleafAuth`` both need to stream a
generated Python source string into a mode-700 temp file that
``GIT_ASKPASS`` can execute. The surrounding service-specific logic
(where the token comes from, what the script prints, which keyring
slot it targets) diverges, but the on-disk shape of the file does
not. This module owns that shared shape.

Callers own the returned path: delete it once the subprocess that
reads it has exited.
"""

import os
import stat
import tempfile


__all__ = [
    "fsWriteExecutableScript",
]


def fsWriteExecutableScript(sSource, sPrefix):
    """Write ``sSource`` to a mode-700 temp file and return the path.

    ``sPrefix`` is the ``tempfile.mkstemp`` prefix — choose a
    service-specific value (``vc_askpass_``, ``vc_gh_askpass_``) so
    any leftover file on crash is easy to attribute.
    """
    iFileDescriptor, sPath = tempfile.mkstemp(
        prefix=sPrefix, suffix=".py",
    )
    try:
        os.write(iFileDescriptor, sSource.encode("utf-8"))
    finally:
        os.close(iFileDescriptor)
    os.chmod(sPath, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return sPath
