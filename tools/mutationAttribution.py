#!/usr/bin/env python3
"""Map a runtime container-mutation observation back to an inventory row.

Migration plan R5. The generator merges static declarations with runtime
observations, so before anything is migrated somebody has to establish
that an observation can be tied to ONE inventory row. If it cannot, the
row goes to manual tracing -- never to inference. A generator that
guessed a row's carrier mode from its module's other rows would launder
a guess into the record, and the record is the only thing the migration
will be judged against.

WHAT AN OBSERVATION CAN AND CANNOT SEE
--------------------------------------

The inventory's identity for a call site is
``sFile|sFunction|sPrimitive|sReferenceKind|iOrdinal`` plus a
``sFingerprint`` over the reference's own source expression
(``tools/generateMutationInventory.py``). At the moment a primitive
runs, the process has a STACK: a file, a function name, and a line per
frame. Nothing in the stack carries a fingerprint, so attribution is a
re-derivation: parse the observed file, find the reference the observed
line actually holds, fingerprint it the same way, and match.

That re-derivation succeeds in two shapes and fails in a third, and the
third is declared rather than papered over:

``fingerprintExact``
    Exactly one candidate row's fingerprint equals the fingerprint of a
    node spanning the observed line. Unique by construction.

``aliasSingleHop``
    The observed line calls a LOCAL NAME (``fnWriter(...)``) that the
    scanner never recorded, because the row was recorded at the
    binding (``fnWriter = connection.fnWriteFile``) as a
    ``passed-callable``. Resolved by ONE hop inside the SAME function:
    the name must have exactly one binding there, and that binding's
    right-hand side must fingerprint to exactly one candidate row.
    Two bindings, a binding in an outer scope, or a binding built from
    a conditional are NOT resolved -- they return unattributed. The
    limit is the point: a second hop would be a guess.

``unattributed``
    Everything else, with the reason recorded. The two that matter:

    * a primitive PASSED into an executor
      (``asyncio.to_thread(connection.fnWriteFile, ...)``). Inside the
      worker thread the passing frame is not on the stack at all -- the
      frame above the primitive is executor infrastructure -- so the row
      is unreachable from the observation. Contextvars DO propagate, so
      the carrier's admission mode survives and is recorded; the row
      does not.
    * two references in one function whose source expressions are
      character-identical. They are separate rows, but the inventory
      distinguishes them only by ``iOrdinal``, which is assigned by
      sorting on the fingerprint they share. A stack frame cannot
      recover which one ran.

An unattributed observation is NOT a classification. It routes its
primitive to :func:`flistSelectRowsForManualTracing`, together with
every row the suite never reached at all.

THE GATEWAY FRAMES ARE SKIPPED
------------------------------

``dockerConnection.fnWriteFile`` calls ``fnWriteFileViaTar``, and both
names are primitives, so the gateway's own delegation is itself a row.
Attributing to it would answer "the boundary called the boundary" for
every observation in the codebase. Frames whose candidate rows are all
``bInsideGateway`` are therefore skipped, and the innermost caller-side
frame is the attribution target.

THE OBSERVATION ARTIFACT
------------------------

One record per event, the fields R5 names:
``sInventoryRowKey``, ``sInventoryRowFingerprint`` (inventory key +
fingerprint), ``sEntryPointDeclaration``, ``sCarrierInvocation``,
``sObservedAdmissionMode``, ``sOperationKind``, ``sTarget``,
``sExecutionLane``; plus ``sAttributionEvidence`` and
``sUnattributedReason``, which are what keep an unattributed event from
reading like an attributed one.
"""

__all__ = [
    "S_EVIDENCE_FINGERPRINT_EXACT",
    "S_EVIDENCE_ALIAS_SINGLE_HOP",
    "S_EVIDENCE_UNATTRIBUTED",
    "S_MODE_NO_ADMISSION",
    "TUPLE_OBSERVATION_FIELDS",
    "DICT_ADMISSION_MODE_TO_CARRIER_MODE",
    "flistCaptureCallerFrames",
    "fsDescribeCarrierInvocation",
    "fsFingerprintReferenceNode",
    "fsBuildInventoryRowKey",
    "flistScanModuleForInventoryRows",
    "fdictBuildAttributionIndex",
    "fdictBuildAttributionIndexForPackage",
    "fdictAttributeFramesToInventoryRow",
    "fdictRecordMutationObservation",
    "fdictMergeObservationsByRow",
    "flistSelectRowsForManualTracing",
    "fnWriteObservationArtifact",
]

import ast
import importlib.util
import inspect
import json
import pathlib

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"

S_EVIDENCE_FINGERPRINT_EXACT = "fingerprintExact"
S_EVIDENCE_ALIAS_SINGLE_HOP = "aliasSingleHop"
S_EVIDENCE_UNATTRIBUTED = "unattributed"

# What an observation records when the effect ran in a lane that held no
# carrier admission at all. Distinct from an admission whose mode is
# "request": one is the ambient legacy mint, the other is nothing.
S_MODE_NO_ADMISSION = "noAdmission"

S_DECLARATION_ABSENT = "UNDECLARED"

# The admission modes minted by vaibify/gui/commitCarrier.py, mapped to
# the reviewer vocabulary the inventory's listCarrierModes accepts.
# "request" and "ownerEstablishing" map to NOTHING on purpose: the
# ambient request admission is precisely what the migration removes, and
# owner-establishing is the connect handler's own authority, not a
# carrier mode. Mapping either to a carrier mode would let an
# unmigrated route's observation read as a migrated one's.
DICT_ADMISSION_MODE_TO_CARRIER_MODE = {
    "synchronousCommit": "synchronous",
    "lockHeldAsync": "lock-held",
    "durableTask": "durable",
}

TUPLE_OBSERVATION_FIELDS = (
    "sInventoryRowKey",
    "sInventoryRowFingerprint",
    "sAttributionEvidence",
    "sUnattributedReason",
    "sEntryPointDeclaration",
    "sCarrierInvocation",
    "sObservedAdmissionMode",
    "sOperationKind",
    "sTarget",
    "sExecutionLane",
    "sPrimitive",
)


def _fmoduleLoadInventoryGenerator():
    """Import the inventory generator by path (``tools`` is no package).

    Imported rather than reimplemented because the FINGERPRINT is an
    identity: two derivations of it would be a divergence bug waiting
    for the first change to either. This module is a consumer of the
    scanner's identity scheme, never a second author of it.
    """
    pathTool = PATH_REPOSITORY / "tools" / "generateMutationInventory.py"
    specTool = importlib.util.spec_from_file_location(
        "generateMutationInventoryForAttribution", pathTool,
    )
    moduleTool = importlib.util.module_from_spec(specTool)
    specTool.loader.exec_module(moduleTool)
    return moduleTool


_moduleGenerator = _fmoduleLoadInventoryGenerator()


def fsFingerprintReferenceNode(nodeReference, tupleSourceLines):
    """Return the SCANNER's fingerprint for one reference node.

    Takes the module source because the scanner's fingerprint is SLICED
    from the file rather than regenerated from the tree -- see
    generateMutationInventory._fsFingerprintNode. Attribution must use
    the identical construction or the index it builds cannot be matched
    against the checked-in inventory at all.
    """
    return _moduleGenerator._fsFingerprintNode(
        nodeReference, tupleSourceLines,
    )


def fsBuildInventoryRowKey(dictRow):
    """Return the SCANNER's stable identity for one inventory row."""
    return _moduleGenerator.fsRowKey(dictRow)


# ---------------------------------------------------------------------
# Capturing what the runtime can see.
# ---------------------------------------------------------------------

def _frameWalkBack(iSkipCount):
    """Return the frame ``iSkipCount`` above this helper's caller."""
    frameWalking = inspect.currentframe().f_back.f_back
    for _ in range(iSkipCount):
        if frameWalking is None:
            return None
        frameWalking = frameWalking.f_back
    return frameWalking


def flistCaptureCallerFrames(iSkipCount=0, iMaximumDepth=60):
    """Return the live stack above this call, innermost frame first."""
    listFrames = []
    frameWalking = _frameWalkBack(iSkipCount)
    while frameWalking is not None and len(listFrames) < iMaximumDepth:
        listFrames.append({
            "sPathSource": frameWalking.f_code.co_filename,
            "sFunction": frameWalking.f_code.co_name,
            "iLine": frameWalking.f_lineno,
        })
        frameWalking = frameWalking.f_back
    return listFrames


def fsDescribeCarrierInvocation(iSkipCount=0):
    """Return ``file:function:line`` for the frame invoking a carrier.

    Called AT the carrier invocation, where the entry point is still on
    the stack. It is the half of the attribution that never fails: a
    carrier is invoked by an ordinary synchronous call, so its caller's
    frame is always available even when the effect it launches later
    runs somewhere the caller cannot be seen from.
    """
    frameCaller = _frameWalkBack(iSkipCount)
    return ":".join((
        pathlib.Path(frameCaller.f_code.co_filename).name,
        frameCaller.f_code.co_name,
        str(frameCaller.f_lineno),
    ))


# ---------------------------------------------------------------------
# The static index the observation is matched against.
# ---------------------------------------------------------------------

def flistScanModuleForInventoryRows(pathModule, sRelativePath):
    """Return the scanner's own rows for one module, ordinal-numbered."""
    sModuleSource = pathlib.Path(pathModule).read_text(encoding="utf-8")
    visitorSites = _moduleGenerator._VisitorCallSites(
        sRelativePath, sModuleSource,
    )
    visitorSites.fnCollect(ast.parse(sModuleSource))
    return _moduleGenerator._flistNumberOrdinals(visitorSites.listRows)


def fdictBuildAttributionIndex(listtSourceModules):
    """Index rows and source models for ``(path, relative path)`` pairs."""
    dictRowsByFrame = {}
    dictSourceModels = {}
    for pathModule, sRelativePath in listtSourceModules:
        sPathResolved = str(pathlib.Path(pathModule).resolve())
        for dictRow in flistScanModuleForInventoryRows(
            pathModule, sRelativePath,
        ):
            dictRowsByFrame.setdefault(
                (sPathResolved, dictRow["sFunction"]), [],
            ).append(dictRow)
        dictSourceModels[sPathResolved] = _fdictBuildSourceModel(pathModule)
    return {
        "dictRowsByFrame": dictRowsByFrame,
        "dictSourceModels": dictSourceModels,
    }


def fdictBuildAttributionIndexForPackage():
    """Build the index over every module of the shipped package."""
    listtSourceModules = [
        (pathModule, str(pathModule.relative_to(PATH_PACKAGE)))
        for pathModule in sorted(PATH_PACKAGE.rglob("*.py"))
        if "__pycache__" not in pathModule.parts
    ]
    return fdictBuildAttributionIndex(listtSourceModules)


def _fdictBuildSourceModel(pathModule):
    """Return the per-file model attribution reads: lines and functions.

    ``dictFingerprintsByLine`` maps every source line to the
    fingerprints of the reference nodes SPANNING it, because a frame
    reports one line for an expression that may occupy several.
    """
    sModuleSource = pathlib.Path(pathModule).read_text(encoding="utf-8")
    tupleSourceLines = _moduleGenerator.ftupleSourceLines(sModuleSource)
    treeModule = ast.parse(sModuleSource)
    dictFingerprintsByLine = {}
    for nodeReference in ast.walk(treeModule):
        if not isinstance(nodeReference, (ast.Call, ast.Attribute, ast.Name)):
            continue
        sFingerprint = fsFingerprintReferenceNode(
            nodeReference, tupleSourceLines,
        )
        iEndLine = getattr(
            nodeReference, "end_lineno", None,
        ) or nodeReference.lineno
        for iLine in range(nodeReference.lineno, iEndLine + 1):
            dictFingerprintsByLine.setdefault(iLine, set()).add(sFingerprint)
    return {
        "treeModule": treeModule,
        # Carried so the alias resolver fingerprints a binding the
        # same way the scanner did -- from the file, not the tree.
        "tupleSourceLines": tupleSourceLines,
        "dictFingerprintsByLine": dictFingerprintsByLine,
    }


# ---------------------------------------------------------------------
# Attribution.
# ---------------------------------------------------------------------

def fdictAttributeFramesToInventoryRow(listFrames, dictIndex):
    """Attribute a captured stack to one inventory row, or refuse.

    Stops at the innermost caller-side frame that has candidate rows:
    walking further out on a failure would let a caller's unrelated row
    absorb an effect it did not perform, which is the inference this
    exists to prevent.
    """
    for dictFrame in listFrames:
        sPathResolved = str(pathlib.Path(dictFrame["sPathSource"]).resolve())
        listCandidates = dictIndex["dictRowsByFrame"].get(
            (sPathResolved, dictFrame["sFunction"]),
        )
        if not listCandidates:
            continue
        if all(dictRow["bInsideGateway"] for dictRow in listCandidates):
            continue
        return _fdictMatchFrameToRow(
            dictFrame, listCandidates,
            dictIndex["dictSourceModels"][sPathResolved],
        )
    return _fdictRefuseAttribution(
        "no frame on the stack holds a recorded reference; the effect "
        "was reached from a lane the inventory cannot see from here "
        "(typically a primitive passed into an executor, whose passing "
        "frame is not on the worker thread's stack)"
    )


def _fdictMatchFrameToRow(dictFrame, listCandidates, dictSourceModel):
    """Return the attribution outcome for one candidate-bearing frame."""
    setFingerprintsHere = dictSourceModel["dictFingerprintsByLine"].get(
        dictFrame["iLine"], set(),
    )
    listMatched = [
        dictRow for dictRow in listCandidates
        if dictRow["sFingerprint"] in setFingerprintsHere
    ]
    if len(listMatched) == 1:
        return _fdictAcceptAttribution(
            listMatched[0], S_EVIDENCE_FINGERPRINT_EXACT,
        )
    if len(listMatched) > 1:
        return _fdictRefuseAttribution(
            f"{len(listMatched)} recorded references in "
            f"{dictFrame['sFunction']} share the source expression on "
            "line "
            f"{dictFrame['iLine']}; the inventory separates them only by "
            "an ordinal assigned from that shared fingerprint, which a "
            "stack frame cannot recover"
        )
    return _fdictMatchThroughLocalAlias(
        dictFrame, listCandidates, dictSourceModel,
    )


def _fdictMatchThroughLocalAlias(dictFrame, listCandidates, dictSourceModel):
    """Resolve one hop: a local name bound once to a recorded reference."""
    nodeFunction = _fnodeFindEnclosingFunction(
        dictSourceModel["treeModule"], dictFrame["sFunction"],
        dictFrame["iLine"],
    )
    if nodeFunction is None:
        return _fdictRefuseAttribution(
            f"line {dictFrame['iLine']} holds no recorded reference and "
            f"no single enclosing definition of {dictFrame['sFunction']} "
            "could be located to resolve an alias"
        )
    setAliasNames = _fsetCollectCalledLocalNames(
        nodeFunction, dictFrame["iLine"],
    )
    listMatched = _flistMatchAliasBindings(
        nodeFunction, setAliasNames, listCandidates,
        dictSourceModel["tupleSourceLines"],
    )
    if len(listMatched) == 1:
        return _fdictAcceptAttribution(
            listMatched[0], S_EVIDENCE_ALIAS_SINGLE_HOP,
        )
    return _fdictRefuseAttribution(
        f"line {dictFrame['iLine']} of {dictFrame['sFunction']} holds no "
        f"recorded reference, and {len(listMatched)} of its local names "
        "resolve to one in a single binding; a second hop would be a "
        "guess"
    )


def _fnodeFindEnclosingFunction(treeModule, sFunction, iLine):
    """Return the innermost definition of ``sFunction`` covering a line."""
    listCovering = [
        nodeDefinition for nodeDefinition in ast.walk(treeModule)
        if isinstance(
            nodeDefinition, (ast.FunctionDef, ast.AsyncFunctionDef),
        )
        and nodeDefinition.name == sFunction
        and nodeDefinition.lineno <= iLine <= getattr(
            nodeDefinition, "end_lineno", nodeDefinition.lineno,
        )
    ]
    if not listCovering:
        return None
    return max(listCovering, key=lambda nodeDefinition: nodeDefinition.lineno)


def _fsetCollectCalledLocalNames(nodeFunction, iLine):
    """Return the bare names CALLED on one line of a function body."""
    setNames = set()
    for nodeCall in ast.walk(nodeFunction):
        if not isinstance(nodeCall, ast.Call):
            continue
        iEndLine = getattr(nodeCall, "end_lineno", nodeCall.lineno)
        if not nodeCall.lineno <= iLine <= iEndLine:
            continue
        if isinstance(nodeCall.func, ast.Name):
            setNames.add(nodeCall.func.id)
    return setNames


def _flistMatchAliasBindings(
    nodeFunction, setAliasNames, listCandidates, tupleSourceLines,
):
    """Return candidate rows reached by a name bound exactly once here."""
    dictBindings = {}
    for nodeAssign in ast.walk(nodeFunction):
        if not isinstance(nodeAssign, ast.Assign):
            continue
        for nodeTarget in nodeAssign.targets:
            if not isinstance(nodeTarget, ast.Name):
                continue
            dictBindings.setdefault(nodeTarget.id, []).append(nodeAssign.value)
    listMatched = []
    for sAliasName in sorted(setAliasNames):
        listBound = dictBindings.get(sAliasName, [])
        if len(listBound) != 1:
            continue
        sFingerprint = fsFingerprintReferenceNode(
            listBound[0], tupleSourceLines,
        )
        listMatched.extend(
            dictRow for dictRow in listCandidates
            if dictRow["sFingerprint"] == sFingerprint
        )
    return listMatched


def _fdictAcceptAttribution(dictRow, sEvidence):
    """Return the accepted attribution for one row."""
    return {
        "sInventoryRowKey": fsBuildInventoryRowKey(dictRow),
        "sInventoryRowFingerprint": dictRow["sFingerprint"],
        "sAttributionEvidence": sEvidence,
        "sUnattributedReason": "",
    }


def _fdictRefuseAttribution(sReason):
    """Return the refusal, which is a routing decision, not a verdict."""
    return {
        "sInventoryRowKey": "",
        "sInventoryRowFingerprint": "",
        "sAttributionEvidence": S_EVIDENCE_UNATTRIBUTED,
        "sUnattributedReason": sReason,
    }


# ---------------------------------------------------------------------
# The observation artifact.
# ---------------------------------------------------------------------

def fdictRecordMutationObservation(
    dictIndex, sPrimitive, sObservedAdmissionMode, sOperationKind,
    sTarget, sExecutionLane, sCarrierInvocation="",
    sEntryPointDeclaration=S_DECLARATION_ABSENT, listFrames=None,
):
    """Return one observation record for the effect running right now.

    ``listFrames`` defaults to the live stack above this call, so a
    production observation point passes nothing; a test may supply a
    stack it captured earlier at the real effect site.
    """
    dictAttribution = fdictAttributeFramesToInventoryRow(
        listFrames if listFrames is not None
        else flistCaptureCallerFrames(iSkipCount=1),
        dictIndex,
    )
    dictObservation = dict(dictAttribution)
    dictObservation.update({
        "sEntryPointDeclaration": sEntryPointDeclaration,
        "sCarrierInvocation": sCarrierInvocation,
        "sObservedAdmissionMode": sObservedAdmissionMode,
        "sOperationKind": sOperationKind,
        "sTarget": sTarget,
        "sExecutionLane": sExecutionLane,
        "sPrimitive": sPrimitive,
    })
    return dictObservation


def fdictMergeObservationsByRow(listObservations):
    """Merge attributed observations per row, UNIONING every dimension.

    A shared helper legitimately runs under more than one carrier mode
    and in more than one lane, which is why the inventory's
    corresponding fields are lists. A merge that overwrote would make
    the record depend on which observation happened to arrive last.
    """
    dictMerged = {}
    for dictObservation in listObservations:
        sRowKey = dictObservation["sInventoryRowKey"]
        if not sRowKey:
            continue
        dictEntry = dictMerged.setdefault(sRowKey, {
            "setObservedAdmissionModes": set(),
            "setCarrierModes": set(),
            "setExecutionLanes": set(),
            "setEntryPointDeclarations": set(),
            "setCarrierInvocations": set(),
            "iObservationCount": 0,
        })
        dictEntry["setObservedAdmissionModes"].add(
            dictObservation["sObservedAdmissionMode"],
        )
        sCarrierMode = DICT_ADMISSION_MODE_TO_CARRIER_MODE.get(
            dictObservation["sObservedAdmissionMode"], "",
        )
        if sCarrierMode:
            dictEntry["setCarrierModes"].add(sCarrierMode)
        dictEntry["setExecutionLanes"].add(dictObservation["sExecutionLane"])
        dictEntry["setEntryPointDeclarations"].add(
            dictObservation["sEntryPointDeclaration"],
        )
        dictEntry["setCarrierInvocations"].add(
            dictObservation["sCarrierInvocation"],
        )
        dictEntry["iObservationCount"] += 1
    return dictMerged


def flistSelectRowsForManualTracing(listRows, listObservations):
    """Return the rows no observation attributed, with the reason each.

    The hand-off R5 demands. A row lands here for one of two reasons and
    both are stated: the suite never reached it, or it was reached only
    through observations that could not be tied to it. NEITHER is a
    classification, and nothing here consults a row's neighbours -- a
    module whose other rows were all observed contributes exactly
    nothing to the standing of the one that was not.
    """
    setAttributed = {
        dictObservation["sInventoryRowKey"]
        for dictObservation in listObservations
        if dictObservation["sInventoryRowKey"]
    }
    setPrimitivesSeenUnattributed = {
        dictObservation["sPrimitive"]
        for dictObservation in listObservations
        if not dictObservation["sInventoryRowKey"]
    }
    listPending = []
    for dictRow in listRows:
        sRowKey = fsBuildInventoryRowKey(dictRow)
        if sRowKey in setAttributed:
            continue
        listPending.append({
            "sInventoryRowKey": sRowKey,
            "sInventoryRowFingerprint": dictRow["sFingerprint"],
            "sReason": (
                "observed but unattributable"
                if dictRow["sPrimitive"] in setPrimitivesSeenUnattributed
                else "no observation reached this row"
            ),
        })
    return listPending


def fnWriteObservationArtifact(listObservations, pathArtifact):
    """Write the observation artifact, sorted so a diff means something."""
    pathArtifact = pathlib.Path(pathArtifact)
    pathArtifact.write_text(json.dumps({
        "sPurpose": (
            "One record per observed container-mutation effect, tied to "
            "an inventory row where attribution is provable and marked "
            "unattributed where it is not. See "
            "tools/mutationAttribution.py."
        ),
        "iObservationCount": len(listObservations),
        "listObservations": sorted(
            listObservations,
            key=lambda dictObservation: tuple(
                str(dictObservation[sField])
                for sField in TUPLE_OBSERVATION_FIELDS
            ),
        ),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
