"""Refuse to start when ``vaibify`` resolved to a folder instead of the installed package.

An editable install (PEP 660) finds ``vaibify`` through a finder Python
consults only AFTER it has scanned ``sys.path``. When the current
directory is on ``sys.path`` (an empty ``PYTHONPATH`` entry puts it
there) and holds a folder named ``vaibify`` -- a checkout's parent
directory does -- that folder is imported as a namespace package with
no ``__file__``. The submodules still load from the real package, so
the command gets far enough to fail somewhere unrelated, with a
``TypeError`` about ``None``. This check runs first and says what
happened instead. It imports nothing else from vaibify, because
anything it imported would be resolved through the same shadow.
"""

__all__ = ["I_EXIT_SHADOWED", "fsDescribeShadow", "fnRefuseNamespaceShadowedPackage"]

import os
import sys

I_EXIT_SHADOWED = 2


def fsDescribeShadow(modulePackage):
    """Return the one sentence that names the shadowing folder and the fix."""
    listFolders = [str(sFolder) for sFolder in getattr(modulePackage, "__path__", [])]
    sFolder = listFolders[0] if listFolders else "a folder named vaibify"
    sPythonPath = os.environ.get("PYTHONPATH", "")
    bEmptyEntry = "" in sPythonPath.split(os.pathsep) if sPythonPath else False
    sPathNote = (
        f"PYTHONPATH is {sPythonPath!r}, whose empty entry is the current directory"
        if bEmptyEntry else f"PYTHONPATH is {sPythonPath!r}"
    )
    return (
        f"vaibify cannot start: Python imported the folder {sFolder} instead of "
        f"the installed vaibify package, because the current directory "
        f"{os.getcwd()} is on the import path ({sPathNote}); run vaibify from "
        f"another directory, or remove the empty entry from PYTHONPATH."
    )


def fnRefuseNamespaceShadowedPackage(modulePackage=None):
    """Exit with status 2 and one sentence when ``vaibify`` has no ``__file__``.

    With no argument it asks the ``vaibify`` already imported, which is
    whatever a shadowing folder made it.
    """
    if modulePackage is None:
        import vaibify as modulePackage
    if modulePackage is None or getattr(modulePackage, "__file__", None):
        return
    sys.stderr.write(fsDescribeShadow(modulePackage) + "\n")
    sys.exit(I_EXIT_SHADOWED)
