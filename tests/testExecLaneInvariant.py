"""Every exec call site resolves to a declared lane, and the opt-outs are enumerated.

Source: ``vaibify/docker/execLane.py`` and every caller of the exec
primitives under ``vaibify/``.

The hub-internal lane is the default, so a call site that says nothing
is covered. What this file pins is the other direction: the set of
call sites that opt OUT -- that run the researcher's own command with
the image PATH -- is a frozen enumeration, and a new one fails here
until it is reviewed and added. The scan reads the source, so it sees
a call through ``asyncio.to_thread`` as well as a direct one, and it
refuses a lane spelled as a string literal, because a literal cannot
be enumerated by name.

A second scan holds the spelling of the hub's Python: a hub-internal
module that composes ``python3 -c`` by hand would run the agent's user
site inside a program whose answer the dashboard trusts, so outside
the modules that run the RESEARCHER's interpreter on purpose the only
spelling is the constant.
"""

import ast
import pathlib

import pytest

from vaibify.docker.execLane import (
    S_EXEC_LANE_HUB_INTERNAL, S_EXEC_LANE_INTEGRITY_READ,
    S_EXEC_LANE_RESEARCHER_COMMAND,
)

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"

# The exec primitives whose ``sLane`` keyword this file governs. Other
# functions in the package take a keyword of the same spelling for
# unrelated meanings (the researcher-action lane, the WebSocket lane).
SET_EXEC_PRIMITIVES = {
    "ftRunInContainerStreamed",
    "ftRunInContainerStreamedWithChunks",
    "ftResultExecuteCommand",
    "fsExecCreate",
    "ftRunRootShellProbe",
    "ftRunProgramWithStdin",
    "ftRunCommand",
}

# Modules that forward a caller's ``sLane`` to the next primitive.
SET_FORWARDING_MODULES = {
    "vaibify/docker/dockerConnection.py",
    "vaibify/host/hostConnection.py",
    "vaibify/reproducibility/repoFiles.py",
}

# Every call site that runs the RESEARCHER's command: a step, a test,
# the terminal, an agent CLI, a probe of the researcher's installed
# environment, or a preflight ``which`` for a command the researcher
# wrote. Each is reviewed; adding one is a security decision.
SET_RESEARCHER_COMMAND_SITES = frozenset({
    "vaibify/cli/commandTest.py::_fiRunTestCommandList",
    "vaibify/gui/llmInvoker.py::fbContainerHasClaude",
    "vaibify/gui/llmInvoker.py::ftResultGenerateViaClaude",
    "vaibify/gui/pipelineRunner.py::_ftRunSingleCommand",
    "vaibify/gui/pipelineValidator.py::_fnValidateSingleCommand",
    "vaibify/gui/routes/falsificationRoutes.py::_fsRequireCosmicRay",
    "vaibify/gui/routes/falsificationRoutes.py::_fdictRunMutationSync",
    "vaibify/gui/routes/falsificationRoutes.py::"
    "_fdictSummarizeMutationSession",
    "vaibify/gui/routes/reproducibilityRoutes.py::"
    "fdictCheckLockSatisfiedByContainer",
    "vaibify/gui/routes/testRoutes.py::_fdictRunOneTestCategory",
    "vaibify/gui/routes/testRoutes.py::_ftRunSaveAndRunTest",
    "vaibify/gui/routes/testRoutes.py::_ftRunCategoryCommands",
    "vaibify/gui/terminalSession.py::_fnStartContained",
    "vaibify/gui/terminalSession.py::fnStart",
    "vaibify/reproducibility/dependencyPinning.py::"
    "_fdictProbeInstalledEnvironment",
    "vaibify/reproducibility/shadowRerun.py::"
    "_fdictRefusalIfImageLacksLockedPackages",
})

# Every call site that runs a hasher the dashboard trusts through the
# GENERAL exec rather than a typed read: these run as the read-only
# user too. The typed-read seam declares its own lane per operation.
SET_INTEGRITY_READ_SITES = frozenset({
    "vaibify/gui/containerGit.py::_fdictHashOneBatchInContainer",
    "vaibify/gui/pipelineRunner.py::_fdictHashRemoteDataFiles",
    "vaibify/gui/routes/pipelineRoutes.py::_fdictFetchTestMarkers",
    "vaibify/gui/syncDispatcher.py::fdictComputeContainerDigests",
})

# Modules allowed to spell ``python3 -c`` without the hub constant,
# each because the program it composes runs the RESEARCHER's
# interpreter or another container's, never a hub-trusted read.
DICT_PLAIN_PYTHON_ALLOWED = {
    # Enumerates the installed packages the lock is checked against;
    # the researcher's user site is part of that environment.
    "vaibify/reproducibility/shadowRerun.py": "researcher lane",
    "vaibify/reproducibility/dependencyPinning.py": "researcher lane",
    # The council's program runs as the council user in a disposable
    # container built for one run.
    "vaibify/gui/agentCouncilManifest.py": "council container",
    # Stage B of the determinism assessment loads NumPy under exactly
    # the steps' own prefix, to report what the STEPS would see.
    "vaibify/gui/determinismEnvironment.py": "the steps' own environment",
    # Scripts the reproducer runs in the reproduction container, or
    # that the container image runs for itself.
    "vaibify/reproducibility/reproduceScriptGenerator.py": "reproduce.sh",
    "vaibify/containerImage/vaibifyDo.py": "in-container client",
    "vaibify/gui/introspectionScript.py": "program text, not a command",
}


def _flistPythonFiles():
    return sorted(
        pathFile for pathFile in PATH_PACKAGE.rglob("*.py")
        if "__pycache__" not in pathFile.parts
    )


def _fsCalleeName(nodeCall):
    """Return the attribute or name a call invokes, or ``""``."""
    nodeFunction = nodeCall.func
    if isinstance(nodeFunction, ast.Attribute):
        return nodeFunction.attr
    if isinstance(nodeFunction, ast.Name):
        return nodeFunction.id
    return ""


def _fbIsExecPrimitiveCall(nodeCall):
    """True for a direct primitive call or one handed to ``to_thread``."""
    if _fsCalleeName(nodeCall) in SET_EXEC_PRIMITIVES:
        return True
    if _fsCalleeName(nodeCall) == "to_thread" and nodeCall.args:
        return _fsCalleeName(ast.Call(
            func=nodeCall.args[0], args=[], keywords=[],
        )) in SET_EXEC_PRIMITIVES
    return False


def _fdictEnclosingFunctions(treeAst):
    """Map every node to the name of the function that encloses it."""
    dictEnclosing = {}

    def fnWalk(node, sFunction):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sFunction = node.name
        dictEnclosing[node] = sFunction
        for nodeChild in ast.iter_child_nodes(node):
            fnWalk(nodeChild, sFunction)

    fnWalk(treeAst, "<module>")
    return dictEnclosing


def _flistLaneDeclarations():
    """Return (relative path, function, lane expression node) per site."""
    listSites = []
    for pathFile in _flistPythonFiles():
        treeAst = ast.parse(pathFile.read_text(encoding="utf-8"))
        dictEnclosing = _fdictEnclosingFunctions(treeAst)
        sRelative = pathFile.relative_to(PATH_REPOSITORY).as_posix()
        for node in ast.walk(treeAst):
            if not isinstance(node, ast.Call) or not _fbIsExecPrimitiveCall(node):
                continue
            for nodeKeyword in node.keywords:
                if nodeKeyword.arg == "sLane":
                    listSites.append(
                        (sRelative, dictEnclosing[node], nodeKeyword.value),
                    )
    return listSites


def _fsLaneSpelling(nodeValue):
    if isinstance(nodeValue, ast.Name):
        return nodeValue.id
    if isinstance(nodeValue, ast.Attribute):
        return nodeValue.attr
    return ast.dump(nodeValue)


def testEveryLaneIsNamedByItsConstant():
    """A lane is a Name from execLane, or the forwarded ``sLane`` itself."""
    listOffenders = []
    for sRelative, sFunction, nodeValue in _flistLaneDeclarations():
        sSpelling = _fsLaneSpelling(nodeValue)
        if sSpelling in ("S_EXEC_LANE_HUB_INTERNAL",
                         "S_EXEC_LANE_INTEGRITY_READ",
                         "S_EXEC_LANE_RESEARCHER_COMMAND"):
            continue
        if sSpelling == "sLane" and sRelative in SET_FORWARDING_MODULES:
            continue
        listOffenders.append(f"{sRelative}::{sFunction} -> {sSpelling}")
    assert listOffenders == [], (
        "An exec lane must be one of the two execLane constants (or the "
        "forwarded sLane inside a connection module):\n  "
        + "\n  ".join(listOffenders)
    )


def testTheResearcherCommandSitesAreExactlyTheEnumeratedOnes():
    """The opt-outs are a frozen set: a new one is reviewed here first."""
    setFound = {
        f"{sRelative}::{sFunction}"
        for sRelative, sFunction, nodeValue in _flistLaneDeclarations()
        if _fsLaneSpelling(nodeValue) == "S_EXEC_LANE_RESEARCHER_COMMAND"
    }
    setNew = setFound - SET_RESEARCHER_COMMAND_SITES
    setGone = SET_RESEARCHER_COMMAND_SITES - setFound
    assert not setNew, (
        "New researcher-command exec sites (each keeps the image PATH, "
        "which the agent's ~/.local/bin leads; review, then enumerate):\n  "
        + "\n  ".join(sorted(setNew))
    )
    assert not setGone, (
        "Enumerated researcher-command sites no longer declare the lane "
        "(drop them from the set if the call moved to the hub lane on "
        "purpose):\n  " + "\n  ".join(sorted(setGone))
    )


def testTheIntegrityReadSitesAreExactlyTheEnumeratedOnes():
    """The hashers that run as the read-only user are a frozen set too."""
    setFound = {
        f"{sRelative}::{sFunction}"
        for sRelative, sFunction, nodeValue in _flistLaneDeclarations()
        if _fsLaneSpelling(nodeValue) == "S_EXEC_LANE_INTEGRITY_READ"
    }
    assert setFound == SET_INTEGRITY_READ_SITES, (
        f"integrity-read exec sites changed: new {sorted(setFound - SET_INTEGRITY_READ_SITES)}, "
        f"gone {sorted(SET_INTEGRITY_READ_SITES - setFound)}"
    )


def testTheThreeLanesAreDistinctStrings():
    assert len({
        S_EXEC_LANE_HUB_INTERNAL, S_EXEC_LANE_INTEGRITY_READ,
        S_EXEC_LANE_RESEARCHER_COMMAND,
    }) == 3


@pytest.mark.parametrize("sSpelling", ['"python3 -c', '"python3", "-c"',
                                        "'python3', '-c'", "'python3 -c"])
def testHubModulesSpellTheIsolatedInterpreterThroughTheConstant(sSpelling):
    """No hub module composes ``python3 -c`` beside the constant."""
    listOffenders = []
    for pathFile in _flistPythonFiles():
        sRelative = pathFile.relative_to(PATH_REPOSITORY).as_posix()
        if sRelative in DICT_PLAIN_PYTHON_ALLOWED:
            continue
        for iLine, sLine in enumerate(
            pathFile.read_text(encoding="utf-8").splitlines(), start=1,
        ):
            sCode = sLine.split("#", 1)[0]
            if sSpelling in sCode and "``" not in sLine:
                listOffenders.append(f"{sRelative}:{iLine}: {sLine.strip()}")
    assert listOffenders == [], (
        "A hub-run Python program is spelled through execLane's "
        "S_HUB_PYTHON_COMMAND / LIST_HUB_PYTHON_ARGV (python3 -I), or "
        "its module is allowed in DICT_PLAIN_PYTHON_ALLOWED with the "
        "reason it runs another interpreter:\n  "
        + "\n  ".join(listOffenders)
    )
