"""Every guard function carries an anchored mutation, or is seeded debt.

A falsification entry is only worth what it defends. The registry shows
that each recorded mutation is killed; it has never shown that each
GUARD has a recorded mutation at all, so an authorization check could be
deleted and no entry would notice the silence. Completeness ran
test-to-entry and never guard-to-entry.

The rule here runs the other way. Every function whose name says it
guards something (validate, reject, refuse, authorize, permit, admit,
within) must have at least one registry entry whose mutated text sits
inside its body. Today's functions without one are SEEDED in
``guardCoverageSeed.json`` as an exact list: a new unanchored guard
fails the test, and an anchored one must leave the seed in the same
commit, so the debt only falls.

A short list of DECLARED guarantees is held to the stricter standard
with no seed at all: the authentication checks, path validators and
level-gate criteria the documentation promises. Each must be anchored
today and stay so.
"""

import ast
import json
import pathlib
import re

import pytest

from tests.falsificationRegistry import LIST_FALSIFICATIONS

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"
PATH_SEED = pathlib.Path(__file__).resolve().parent / "guardCoverageSeed.json"

RX_GUARD_NAME = re.compile(
    r"(Validate|Reject|Refuse|Authoriz|Permit|Admit|Within)"
)

# Guards the documentation promises. No seed: each is anchored now.
LIST_DECLARED_GUARANTEES = [
    "vaibify/gui/containerOwnership.py::frecordOwnerAuthorizedByAgentToken",
    "vaibify/gui/browserSession.py::fbValidateCredential",
    "vaibify/gui/browserSession.py::ftRedeemCapability",
    "vaibify/gui/serverMiddleware.py::fbIsAllowedHostHeader",
    "vaibify/gui/serverMiddleware.py::_fbBrowserTokenRejected",
    "vaibify/gui/pipelineServer.py::fbHasAgentToken",
    "vaibify/gui/routeScope.py::fnValidateRouteScopesOrRaise",
    "vaibify/gui/agentCouncilContext.py::_fsValidateMemberPath",
    "vaibify/config/bindMountValidator.py::_fnRequireWithinAllowedRoot",
    "vaibify/config/bindMountValidator.py::_fnRejectDeniedPrefix",
    "vaibify/reproducibility/levelGates.py::_flistOffendingUpstream",
    "vaibify/reproducibility/levelGates.py::fbWorkflowFullySyncedWithGithub",
    "vaibify/reproducibility/levelGates.py::fbVerifyManifestComplete",
]


def _flistAllFunctions():
    """Return (identity, path, first line, last line) for every function."""
    listFunctions = []
    for pathModule in sorted(PATH_PACKAGE.rglob("*.py")):
        sRelative = str(pathModule.relative_to(PATH_REPOSITORY))
        treeModule = ast.parse(
            pathModule.read_text(encoding="utf-8", errors="replace"),
        )
        for node in ast.walk(treeModule):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                listFunctions.append((
                    f"{sRelative}::{node.name}", sRelative,
                    node.lineno, node.end_lineno,
                ))
    return listFunctions


def _flistGuardFunctions():
    """Return the functions whose name says they guard something."""
    return [
        tFunction for tFunction in _flistAllFunctions()
        if RX_GUARD_NAME.search(tFunction[0].split("::", 1)[1])
    ]


def _fdictLinesOfMutationsBySource():
    """Return {source file: [(first line, last line), ...]} of mutated text."""
    dictLines = {}
    for entry in LIST_FALSIFICATIONS:
        pathSource = PATH_REPOSITORY / entry.source
        if pathSource.suffix != ".py" or not pathSource.exists():
            continue
        sText = pathSource.read_text(encoding="utf-8")
        iStart = sText.find(entry.old)
        while iStart != -1:
            iFirst = sText.count("\n", 0, iStart) + 1
            iLast = iFirst + entry.old.count("\n")
            dictLines.setdefault(entry.source, []).append((iFirst, iLast))
            iStart = sText.find(entry.old, iStart + 1)
    return dictLines


def _fbMutationOverlapsFunction(tMutationLines, iFirst, iLast):
    return tMutationLines[0] <= iLast and tMutationLines[1] >= iFirst


def _fsetFindAnchoredFunctions():
    dictLines = _fdictLinesOfMutationsBySource()
    setAnchored = set()
    for sIdentity, sPath, iFirst, iLast in _flistAllFunctions():
        for tMutationLines in dictLines.get(sPath, []):
            if _fbMutationOverlapsFunction(tMutationLines, iFirst, iLast):
                setAnchored.add(sIdentity)
                break
    return setAnchored


def _flistFindUnanchoredGuards():
    setAnchored = _fsetFindAnchoredFunctions()
    return sorted({
        sIdentity for sIdentity, _p, _a, _b in _flistGuardFunctions()
        if sIdentity not in setAnchored
    })


@pytest.mark.falsification
def testEveryGuardFunctionIsAnchoredOrSeeded():
    """The set of unanchored guards equals the seed, in both directions.

    Kills: removing the registry entry that anchors a guard (the guard
    reappears unseeded), or anchoring a seeded guard without deleting
    it from the seed (the ratchet would stop falling).
    """
    listUnanchored = _flistFindUnanchoredGuards()
    listSeed = json.loads(PATH_SEED.read_text(encoding="utf-8"))
    assert listSeed == sorted(set(listSeed)), "the seed must be sorted"
    setNew = sorted(set(listUnanchored) - set(listSeed))
    setAnchoredNow = sorted(set(listSeed) - set(listUnanchored))
    assert not setNew, (
        "guard functions with no anchored falsification entry (add a "
        f"mutation to the registry): {setNew}"
    )
    assert not setAnchoredNow, (
        "these seeded guards are anchored now; delete them from "
        f"guardCoverageSeed.json so the debt falls: {setAnchoredNow}"
    )


@pytest.mark.falsification
def testEveryDeclaredGuaranteeIsAnchoredWithNoSeed():
    """The promised authentication, path and level-gate guards are anchored.

    Kills: deleting the registry entries for a declared guard.
    """
    setAnchored = _fsetFindAnchoredFunctions()
    setKnown = {sId for sId, _p, _a, _b in _flistAllFunctions()}
    listMissingFromSource = [
        sId for sId in LIST_DECLARED_GUARANTEES if sId not in setKnown
    ]
    assert not listMissingFromSource, (
        "declared guarantees that no longer exist (renamed? update the "
        f"list): {listMissingFromSource}"
    )
    listUnanchored = [
        sId for sId in LIST_DECLARED_GUARANTEES if sId not in setAnchored
    ]
    assert not listUnanchored, (
        f"declared guarantees with no anchored mutation: {listUnanchored}"
    )


def testTheAnchoringCheckCanFail():
    """A guard whose body holds no mutated text is reported unanchored."""
    sSource = "def fnRejectBadInput(sValue):\n    return sValue\n"
    nodeFunction = ast.parse(sSource).body[0]
    assert RX_GUARD_NAME.search(nodeFunction.name)
    iFirst, iLast = nodeFunction.lineno, nodeFunction.end_lineno
    assert not _fbMutationOverlapsFunction((40, 41), iFirst, iLast)
    assert _fbMutationOverlapsFunction((2, 2), iFirst, iLast)
    assert _fbMutationOverlapsFunction((1, 9), iFirst, iLast)
