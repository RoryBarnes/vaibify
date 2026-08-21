"""Detect the third-party packages a project's own scripts import.

The conversion wizard asks which packages to install in the container.
Answering that by hand means re-reading your own scripts and
transcribing their imports, which is exactly the kind of clerical step
a researcher gets wrong once and then debugs inside a container.

The scan is a SUGGESTION and says so at every layer: it reads the
imports the selected files actually declare, drops the ones Python
ships and the ones the project itself provides, and hands the rest
back for the researcher to edit. It never installs anything and never
edits the package list on its own.

Three things it deliberately does not attempt. It does not resolve
versions -- a version constraint is a scientific decision, and
guessing one would be inventing provenance. It does not follow imports
into imported modules, because the files the researcher ticked are the
files they said were theirs. And it does not verify that a name exists
on PyPI: a project may legitimately depend on a package that is
installed from a git URL or a private index, and refusing to suggest
it because pypi.org has never heard of it would be a scan asserting
more than it knows.
"""

__all__ = [
    "DICT_MODULE_TO_DISTRIBUTION",
    "flistDetectImportedDistributions",
    "flistReadTopLevelImports",
]

import ast
import os
import sys
import sysconfig


# Import names whose PyPI distribution is spelled differently. Only
# the mismatches: a module whose distribution shares its name needs no
# entry, which is why this table is short and stays short. It is
# general Python-ecosystem knowledge, never a science-specific list --
# a package particular to one research domain does not belong here.
DICT_MODULE_TO_DISTRIBUTION = {
    "attr": "attrs",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "Crypto": "pycryptodome",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "git": "GitPython",
    "mpl_toolkits": "matplotlib",
    "OpenGL": "PyOpenGL",
    "PIL": "pillow",
    "serial": "pyserial",
    "skimage": "scikit-image",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
}


def _fsetReadStandardLibraryNames():
    """Return the module names Python itself ships.

    ``sys.stdlib_module_names`` is the authoritative answer and exists
    from 3.10. Vaibify supports 3.9, where the honest fallback is to
    read the stdlib directory rather than carry a frozen list that
    would rot: a hand-maintained set is wrong the moment a release
    adds or removes a module, and wrong here means suggesting that a
    researcher pip-install part of Python.
    """
    setNames = getattr(sys, "stdlib_module_names", None)
    if setNames:
        return set(setNames)
    setFallback = set(sys.builtin_module_names)
    try:
        listEntries = os.listdir(sysconfig.get_paths()["stdlib"])
    except OSError:
        return setFallback
    for sEntry in listEntries:
        setFallback.add(
            sEntry[:-3] if sEntry.endswith(".py") else sEntry
        )
    return setFallback


def flistReadTopLevelImports(sSource):
    """Return the top-level module names a source file imports.

    Relative imports (``from . import x``) are skipped: they name a
    sibling in the researcher's own project, never a package to
    install. ``import a.b.c`` contributes ``a``, because ``a`` is what
    pip is asked for.
    """
    listNames = []
    for nodeAny in ast.walk(ast.parse(sSource)):
        if isinstance(nodeAny, ast.Import):
            listNames.extend(
                nodeAlias.name.split(".")[0]
                for nodeAlias in nodeAny.names
            )
        elif isinstance(nodeAny, ast.ImportFrom):
            if nodeAny.level == 0 and nodeAny.module:
                listNames.append(nodeAny.module.split(".")[0])
    return listNames


def _fsetReadProjectOwnModuleNames(sProjectDirectory):
    """Return names importable from the project root itself.

    Only the TOP level. A module one directory down is not importable
    as a bare name, and sweeping the whole tree would collect every
    directory in it -- including, in a repository of any size, names
    that collide with real packages and would silently drop a genuine
    dependency from the suggestions.
    """
    setNames = set()
    try:
        listEntries = os.listdir(sProjectDirectory)
    except OSError:
        return setNames
    for sEntry in listEntries:
        if sEntry.startswith("."):
            continue
        if sEntry.endswith(".py"):
            setNames.add(sEntry[:-3])
        elif os.path.isdir(os.path.join(sProjectDirectory, sEntry)):
            setNames.add(sEntry)
    return setNames


def flistDetectImportedDistributions(listSourcePaths, sProjectDirectory):
    """Return the distributions the given Python files appear to need.

    Unreadable and unparseable files are SKIPPED rather than fatal: a
    half-written script is an ordinary state for a directory somebody
    is working in, and refusing to suggest anything because one file
    does not parse would make the feature useless exactly when it is
    most wanted.
    """
    setStandardLibrary = _fsetReadStandardLibraryNames()
    setProjectOwn = _fsetReadProjectOwnModuleNames(sProjectDirectory)
    setDistributions = set()
    for sSourcePath in listSourcePaths:
        for sModule in _flistReadImportsQuietly(sSourcePath):
            if sModule in setStandardLibrary or sModule in setProjectOwn:
                continue
            setDistributions.add(
                DICT_MODULE_TO_DISTRIBUTION.get(sModule, sModule),
            )
    return sorted(setDistributions, key=str.lower)


def _flistReadImportsQuietly(sSourcePath):
    """Return one file's top-level imports, or [] if it cannot be read."""
    try:
        with open(sSourcePath, "r", encoding="utf-8") as fileSource:
            sSource = fileSource.read()
        return flistReadTopLevelImports(sSource)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return []
