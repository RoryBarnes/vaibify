"""On-demand structural discovery of Python and JavaScript modules."""

import argparse
import ast
import json
import re
import sys
from pathlib import Path


__all__ = [
    "fdictExtractPythonModule",
    "flistExtractDunderAll",
    "fsFirstDocstringLine",
    "fdictExtractJavaScriptModule",
    "flistCollectEntries",
    "fsRenderMarkdown",
    "fsRenderJson",
    "fnMain",
]


RE_IIFE_MODULE = re.compile(
    r"^\s*var\s+(\w+)\s*=\s*\(function\s*\(\s*\)"
)
RE_BLOCK_COMMENT = re.compile(r"/\*+\s*(.*?)\s*\*/", re.DOTALL)


def _fsRepoRelativePath(sPath):
    """Return a path relative to the repository root if possible."""
    pathInput = Path(sPath).resolve()
    pathRepo = Path(__file__).resolve().parent.parent
    try:
        return str(pathInput.relative_to(pathRepo))
    except ValueError:
        return str(pathInput)


def flistExtractDunderAll(treeAst):
    """Return the list of string names declared in the module __all__."""
    listNames = []
    for nodeAst in treeAst.body:
        if not isinstance(nodeAst, ast.Assign):
            continue
        for nodeTarget in nodeAst.targets:
            if isinstance(nodeTarget, ast.Name) and nodeTarget.id == "__all__":
                listNames = _flistLiteralStrings(nodeAst.value)
    return listNames


def _flistLiteralStrings(nodeValue):
    """Extract string literals from a list or tuple AST node."""
    if not isinstance(nodeValue, (ast.List, ast.Tuple)):
        return []
    listResult = []
    for nodeElement in nodeValue.elts:
        if isinstance(nodeElement, ast.Constant) and isinstance(
            nodeElement.value, str
        ):
            listResult.append(nodeElement.value)
    return listResult


def fsFirstDocstringLine(treeAst):
    """Return the first non-empty line of the module docstring, or ''."""
    sDocstring = ast.get_docstring(treeAst)
    if not sDocstring:
        return ""
    for sLine in sDocstring.splitlines():
        sStripped = sLine.strip()
        if sStripped:
            return sStripped
    return ""


def fdictExtractPythonModule(sPath):
    """AST-parse a Python file and return path, __all__, and purpose."""
    sSource = Path(sPath).read_text(encoding="utf-8", errors="replace")
    try:
        treeAst = ast.parse(sSource)
    except SyntaxError:
        return {
            "sPath": _fsRepoRelativePath(sPath),
            "listSymbols": [],
            "sPurpose": "",
        }
    return {
        "sPath": _fsRepoRelativePath(sPath),
        "listSymbols": flistExtractDunderAll(treeAst),
        "sPurpose": fsFirstDocstringLine(treeAst),
    }


def _tFirstMeaningfulLine(listLines):
    """Return (index, stripped) of first non-blank, non-line-comment line."""
    for iIndex, sLine in enumerate(listLines):
        sStripped = sLine.strip()
        if not sStripped:
            continue
        if sStripped.startswith("//"):
            continue
        if sStripped.startswith("/*") or sStripped.startswith("*"):
            continue
        return iIndex, sStripped
    return -1, ""


def _fsFirstBlockCommentLine(sSource):
    """Return the first non-empty line of the first /* ... */ block."""
    matchComment = RE_BLOCK_COMMENT.search(sSource)
    if not matchComment:
        return ""
    for sLine in matchComment.group(1).splitlines():
        sCleaned = sLine.strip().lstrip("*").strip()
        if sCleaned:
            return sCleaned
    return ""


def fdictExtractJavaScriptModule(sPath):
    """Regex-detect IIFE module name and purpose comment."""
    sSource = Path(sPath).read_text(encoding="utf-8", errors="replace")
    listLines = sSource.splitlines()
    iIndex, sLine = _tFirstMeaningfulLine(listLines)
    sName = ""
    if iIndex >= 0:
        matchModule = RE_IIFE_MODULE.match(sLine)
        if matchModule:
            sName = matchModule.group(1)
    listSymbols = [sName] if sName else []
    return {
        "sPath": _fsRepoRelativePath(sPath),
        "listSymbols": listSymbols,
        "sPurpose": _fsFirstBlockCommentLine(sSource),
    }


def flistCollectEntries(sDirectory):
    """Walk sDirectory and collect module entries for .py and .js files."""
    pathRoot = Path(sDirectory)
    listEntries = []
    for pathFile in sorted(pathRoot.rglob("*.py")):
        if pathFile.is_file():
            listEntries.append(fdictExtractPythonModule(str(pathFile)))
    for pathFile in sorted(pathRoot.rglob("*.js")):
        if pathFile.is_file():
            listEntries.append(fdictExtractJavaScriptModule(str(pathFile)))
    listEntries.sort(key=lambda dictEntry: dictEntry["sPath"])
    return listEntries


def _fsEscapePipe(sText):
    """Escape pipe characters so they do not break a markdown table."""
    return sText.replace("|", "\\|")


def fsRenderMarkdown(listEntries):
    """Format the entries as a markdown table with pipe separators."""
    listLines = [
        "| Path | Module/Symbols | Purpose |",
        "| --- | --- | --- |",
    ]
    for dictEntry in listEntries:
        sSymbols = ", ".join(dictEntry["listSymbols"])
        listLines.append(
            "| {0} | {1} | {2} |".format(
                _fsEscapePipe(dictEntry["sPath"]),
                _fsEscapePipe(sSymbols),
                _fsEscapePipe(dictEntry["sPurpose"]),
            )
        )
    return "\n".join(listLines) + "\n"


def fsRenderJson(listEntries):
    """Serialize entries as a pretty-printed JSON list."""
    return json.dumps(listEntries, indent=2) + "\n"


def _fdictParseArguments(listArguments):
    """Parse CLI arguments into a dict of parameters."""
    parserArgs = argparse.ArgumentParser(
        description="List Python and JavaScript modules under a directory."
    )
    parserArgs.add_argument("sDirectory", help="Directory to scan.")
    parserArgs.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        dest="sFormat",
    )
    namespaceArgs = parserArgs.parse_args(listArguments)
    return {
        "sDirectory": namespaceArgs.sDirectory,
        "sFormat": namespaceArgs.sFormat,
    }


def fnMain(listArguments=None):
    """CLI entry point: scan a directory and print the structural listing."""
    dictArgs = _fdictParseArguments(listArguments)
    pathDirectory = Path(dictArgs["sDirectory"])
    if not pathDirectory.is_dir():
        sys.stderr.write(
            "Error: {0} is not a directory\n".format(dictArgs["sDirectory"])
        )
        return 2
    listEntries = flistCollectEntries(dictArgs["sDirectory"])
    if dictArgs["sFormat"] == "json":
        sys.stdout.write(fsRenderJson(listEntries))
    else:
        sys.stdout.write(fsRenderMarkdown(listEntries))
    return 0


if __name__ == "__main__":
    sys.exit(fnMain())
