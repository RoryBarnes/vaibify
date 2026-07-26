"""Falsification tests for two undefended provenance/credential rules.

``docs/reproducibility.md`` states both as absolutes, and nothing in the
suite could previously falsify either:

- "the URL is inert metadata, never fetched by vaibify" (the
  ``listRemoteData[].sSourceUrl`` provenance field, line 75).
- "Vaibify never stores tokens in configuration files or environment
  variables" (line 327).

Both are security claims about attacker-supplied or secret-bearing
values. ``sSourceUrl`` arrives from ``project.json``, which the
in-container agent can write, so any code path that dereferenced it
onto the network would be a server-side request forgery driven by a
file the agent controls. Environment variables are readable through
``/proc``, ``docker inspect`` and ordinary process inspection, so a
token that lands in one has effectively been published.

Both tests are source scans rather than behavioural probes precisely
because the rules are "no such code exists anywhere" claims — the class
of defect mutation testing cannot reach, since there is no existing
line to mutate.
"""

import ast
import os

import pytest


pytestmark = pytest.mark.falsification


_S_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_S_PACKAGE_ROOT = os.path.join(_S_REPO_ROOT, "vaibify")

_S_INERT_PROVENANCE_FIELD = "sSourceUrl"

# Substrings that mark an environment-variable name as secret-bearing.
_TUPLE_CREDENTIAL_NAME_FRAGMENTS = (
    "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL",
    "APIKEY", "API_KEY", "PRIVATE_KEY", "ACCESS_KEY",
)


def _flistCollectPackageSourceFiles():
    """Return every ``.py`` file shipped under the ``vaibify`` package."""
    listPaths = []
    for sDirectory, listSubdirectories, listFileNames in os.walk(
        _S_PACKAGE_ROOT,
    ):
        listSubdirectories[:] = [
            sName for sName in listSubdirectories
            if sName not in ("__pycache__", ".git")
        ]
        listPaths.extend(
            os.path.join(sDirectory, sFileName)
            for sFileName in listFileNames
            if sFileName.endswith(".py")
        )
    return sorted(listPaths)


def _ftParseSourceFile(sPath):
    """Return ``(relativePath, parsedTree)`` for one package source file."""
    with open(sPath, encoding="utf-8") as fileHandle:
        sSource = fileHandle.read()
    return os.path.relpath(sPath, _S_REPO_ROOT), ast.parse(sSource)


def _fbNodeDereferencesField(nodeAny, sFieldName):
    """Return True when a node reads ``sFieldName`` as a value, not prose.

    A bare string constant equal to the field name catches
    ``dictRemote["sSourceUrl"]`` and ``dictRemote.get("sSourceUrl")``;
    the name and attribute cases catch a local binding or a model
    attribute. Docstrings and comments mentioning the field in prose do
    not match, because their constant value is the whole paragraph.
    """
    if isinstance(nodeAny, ast.Constant):
        return nodeAny.value == sFieldName
    if isinstance(nodeAny, ast.Name):
        return nodeAny.id == sFieldName
    if isinstance(nodeAny, ast.Attribute):
        return nodeAny.attr == sFieldName
    return False


def testRemoteSourceUrlIsNeverDereferencedByVaibifySource():
    """No vaibify module reads the remote-data source URL at all.

    Kills: replacing ``sSha = dictShaByPath.get(dictRemote.get("sPath",
    ""))`` in ``pipelineRunner._fbApplyRemoteDataHashes`` with a body
    that also reads ``dictRemote.get("sSourceUrl", "")`` — the field
    stops being inert metadata the moment any code path dereferences
    it, and the value comes from a project.json the in-container agent
    can write.
    """
    listOffenders = []
    for sPath in _flistCollectPackageSourceFiles():
        sRelative, treeParsed = _ftParseSourceFile(sPath)
        listOffenders.extend(
            f"{sRelative}:{nodeAny.lineno}"
            for nodeAny in ast.walk(treeParsed)
            if _fbNodeDereferencesField(nodeAny, _S_INERT_PROVENANCE_FIELD)
        )
    assert listOffenders == [], (
        "docs/reproducibility.md declares listRemoteData[].sSourceUrl "
        "inert metadata that vaibify never fetches, but these sites "
        "dereference it:\n  " + "\n  ".join(listOffenders)
    )


def _flistCollectEnvironmentAssignmentNames(treeParsed):
    """Return the environment-variable names a module assigns to."""
    listNames = []
    for nodeAny in ast.walk(treeParsed):
        if not isinstance(nodeAny, ast.Subscript):
            continue
        if not isinstance(nodeAny.ctx, ast.Store):
            continue
        if "environ" not in ast.unparse(nodeAny.value):
            continue
        if isinstance(nodeAny.slice, ast.Constant):
            listNames.append((nodeAny.lineno, str(nodeAny.slice.value)))
    return listNames


def testCredentialsAreNeverWrittenIntoEnvironmentVariables():
    """No vaibify module assigns a secret-named environment variable.

    Kills: renaming the assignment in
    ``dockerConnection._fnEnsureDockerHost`` from
    ``os.environ["DOCKER_HOST"]`` to ``os.environ["GITHUB_TOKEN"]`` —
    a secret placed in the process environment is readable through
    /proc and ``docker inspect``, which is exactly the storage
    docs/reproducibility.md promises vaibify never uses.
    """
    listOffenders = []
    for sPath in _flistCollectPackageSourceFiles():
        sRelative, treeParsed = _ftParseSourceFile(sPath)
        for iLineNumber, sName in _flistCollectEnvironmentAssignmentNames(
            treeParsed,
        ):
            sUpper = sName.upper()
            if any(
                sFragment in sUpper
                for sFragment in _TUPLE_CREDENTIAL_NAME_FRAGMENTS
            ):
                listOffenders.append(f"{sRelative}:{iLineNumber}: {sName}")
    assert listOffenders == [], (
        "Vaibify must never place a credential in the process "
        "environment (readable via /proc and docker inspect); delegate "
        "to the credential manager instead:\n  "
        + "\n  ".join(listOffenders)
    )
