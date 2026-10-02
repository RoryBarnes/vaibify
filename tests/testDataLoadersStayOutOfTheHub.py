"""The data loaders run in a test or CLI process, never inside the hub.

Reading or writing a VTK file resets the whole process's locale to ``C``,
after which every ``open()`` without an explicit encoding decodes as
ASCII. Restoring the locale around the call is not safe in a
multithreaded server, so the loaders are kept where that cannot happen:
the hub only EMBEDS their source text into generated tests. If a hub
module ever starts calling a loader, this fails and the question has to
be answered properly (run it in a subprocess) rather than left to the
first non-ASCII file.
"""

import ast
import pathlib

import pytest

import vaibify

PATH_PACKAGE = pathlib.Path(vaibify.__file__).resolve().parent
SET_LOADER_NAMES = {
    "ffLoadValue", "_ffLoadValue", "DICT_LOADERS", "_DICT_LOADERS",
}
SET_ALLOWED_FILES = {
    "gui/dataLoaders.py",
    "testing/standards.py",
}


def _flistLoaderReferences(pathSource):
    """Name every use of a loader entry point, or of its module, in a file."""
    treeSource = ast.parse(pathSource.read_text(encoding="utf-8"))
    listReferences = []
    for node in ast.walk(treeSource):
        if isinstance(node, ast.ImportFrom) and (
            (node.module or "").endswith("dataLoaders")
        ):
            listReferences.extend(
                aliasImported.name for aliasImported in node.names
                if aliasImported.name != "fsReadLoaderSource")
        elif isinstance(node, ast.Attribute) and node.attr in SET_LOADER_NAMES:
            listReferences.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in SET_LOADER_NAMES:
            listReferences.append(node.id)
    return listReferences


@pytest.mark.falsification
def testNoHubModuleCallsADataLoader():
    """Kills: a hub module importing a loader, which resets its locale."""
    listOffenders = []
    for pathSource in sorted(PATH_PACKAGE.rglob("*.py")):
        sRelative = pathSource.relative_to(PATH_PACKAGE).as_posix()
        if sRelative in SET_ALLOWED_FILES:
            continue
        listReferences = _flistLoaderReferences(pathSource)
        if listReferences:
            listOffenders.append((sRelative, sorted(set(listReferences))))
    assert listOffenders == [], (
        "these modules reach a data loader, which resets the process "
        f"locale for VTK files: {listOffenders}")


def testTheOnlyUseOfTheLoadersByTheHubIsToEmbedTheirSource():
    sSource = (PATH_PACKAGE / "gui" / "templateManager.py").read_text(
        encoding="utf-8")
    assert "from .dataLoaders import fsReadLoaderSource" in sSource


def testTheScannerSeesALoaderReference(tmp_path):
    pathProbe = tmp_path / "probe.py"
    pathProbe.write_text(
        "from vaibify.gui.dataLoaders import ffLoadValue\n",
        encoding="utf-8")
    assert _flistLoaderReferences(pathProbe) == ["ffLoadValue"]
    pathBenign = tmp_path / "benign.py"
    pathBenign.write_text(
        "from .dataLoaders import fsReadLoaderSource\n", encoding="utf-8")
    assert _flistLoaderReferences(pathBenign) == []
