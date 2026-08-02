#!/usr/bin/env python3
"""Enumerate every container-mutation call site in the shipped package.

This is the mandatory checkpoint before any migration onto a single
mutation carrier (design: the alpha-hardening wave-2 inventory). A
207-site surgery without a per-site record is how a large refactor ends
with helpers that agree with each other and miss production paths, so
the inventory comes first and the migration is judged against it.

THE DIVISION OF LABOUR IS THE POINT. The machine proves COMPLETENESS:
it walks the AST, finds every call to a declared primitive, and emits
exactly one row per call site with a stable key and a content
fingerprint. It does NOT judge semantics -- what the operation means,
who owns its lock, what metadata a busy refusal should name, whether it
outlives its request. Those are the reviewer's, and a generator that
guessed them would launder a guess into a record. Every such field ships
as ``UNCLASSIFIED`` until somebody classifies it.

The scope is deliberately wider than the mutating primitives: reads are
listed too, and marked as reads. A boundary that only enumerates what it
already believes to be dangerous cannot discover that it was wrong about
one.

Usage::

    python tools/generateMutationInventory.py            # print JSON
    python tools/generateMutationInventory.py --write    # update the file
    python tools/generateMutationInventory.py --check    # drift only

``tests/testMutationInventory.py`` runs the same scan in CI and fails on
an added, removed, duplicated, or edited call site, so the record cannot
quietly fall behind the code it describes.
"""

import argparse
import ast
import hashlib
import json
import pathlib
import sys

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"
PATH_INVENTORY = PATH_REPOSITORY / "tests" / "mutationInventory.json"

S_UNCLASSIFIED = "UNCLASSIFIED"

# Access kinds. The primitive alone determines this one, which is why it
# is machine-filled: what a caller can do THROUGH a primitive is a
# property of the primitive, not of the call site.
S_ACCESS_ARBITRARY_COMMAND = "arbitrary-command"
S_ACCESS_ROOT_SHELL = "arbitrary-root-shell"
S_ACCESS_ARCHIVE_WRITE = "archive-write"
S_ACCESS_EXEC_CREATE = "exec-create"
S_ACCESS_EXEC_STATE = "exec-state"
S_ACCESS_SIGNAL = "signal"
S_ACCESS_LIFECYCLE = "container-lifecycle"
S_ACCESS_TYPED_READ = "typed-read"

# Every primitive that reaches a container or the Docker daemon, and the
# access each one grants. Adding a primitive to the codebase without
# adding it here is caught by
# testMutationInventory::testEveryDockerPrimitiveIsInTheScopeList.
DICT_PRIMITIVE_ACCESS = {
    # --- vaibify/docker/dockerConnection.py: mutation-capable ---
    "texecRunInContainerStreamed": S_ACCESS_ARBITRARY_COMMAND,
    "texecRunInContainerStreamedWithChunks": S_ACCESS_ARBITRARY_COMMAND,
    "ftResultExecuteCommand": S_ACCESS_ARBITRARY_COMMAND,
    "fnWriteFile": S_ACCESS_ARCHIVE_WRITE,
    "fnWriteFileViaTar": S_ACCESS_ARCHIVE_WRITE,
    "fsExecCreate": S_ACCESS_EXEC_CREATE,
    "fsocketExecStart": S_ACCESS_EXEC_CREATE,
    "fnExecResize": S_ACCESS_EXEC_STATE,
    "ftupleRunRootShellProbe": S_ACCESS_ROOT_SHELL,
    "fdictProbeProcessGroupMembers": S_ACCESS_ARBITRARY_COMMAND,
    "fnSignalProcessGroupMembers": S_ACCESS_SIGNAL,
    # --- vaibify/docker/dockerConnection.py: read / cache ---
    "flistGetRunningContainers": S_ACCESS_TYPED_READ,
    "fcontainerGetById": S_ACCESS_TYPED_READ,
    "fbaFetchFile": S_ACCESS_TYPED_READ,
    "fnIterStreamFile": S_ACCESS_TYPED_READ,
    "fdictInspectExec": S_ACCESS_TYPED_READ,
    "fnEvictAbsentContainers": S_ACCESS_TYPED_READ,
    # --- vaibify/docker/containerManager.py: lifecycle ---
    "fnStartContainer": S_ACCESS_LIFECYCLE,
    "fsStartContainerDetached": S_ACCESS_LIFECYCLE,
    "fsCreateContainerForReservation": S_ACCESS_LIFECYCLE,
    "fnStartCreatedContainer": S_ACCESS_LIFECYCLE,
    "fnStopContainer": S_ACCESS_LIFECYCLE,
    "fnRemoveStopped": S_ACCESS_LIFECYCLE,
    "fdictSettleReservationContainers": S_ACCESS_LIFECYCLE,
    "fdictTerminateDockerProcess": S_ACCESS_SIGNAL,
    "fbStopContainerProvenSettled": S_ACCESS_LIFECYCLE,
    # --- vaibify/docker/containerManager.py: probes ---
    "fbContainerIsRunning": S_ACCESS_TYPED_READ,
    "fdictGetContainerStatus": S_ACCESS_TYPED_READ,
    "fdictProbeContainerPresence": S_ACCESS_TYPED_READ,
    "fdictFindContainersForReservation": S_ACCESS_TYPED_READ,
}

SET_MUTATION_CAPABLE_ACCESS = frozenset({
    S_ACCESS_ARBITRARY_COMMAND,
    S_ACCESS_ROOT_SHELL,
    S_ACCESS_ARCHIVE_WRITE,
    S_ACCESS_EXEC_CREATE,
    S_ACCESS_EXEC_STATE,
    S_ACCESS_SIGNAL,
    S_ACCESS_LIFECYCLE,
})

# The modules that DEFINE the primitives. Their own definitions and
# internal helpers are the boundary itself, not callers of it.
SET_GATEWAY_MODULES = frozenset({
    "docker/dockerConnection.py",
    "docker/containerManager.py",
})

# The reviewer's fields: everything that needs judgement rather than
# parsing. They are emitted UNCLASSIFIED so the record never implies a
# review that did not happen.
TUPLE_REVIEWER_FIELDS = (
    "sLogicalOperation",
    "sExecutionLane",
    "sLockOwner",
    "sOperationMetadata",
    "sCarrierMode",
    "sJournalKind",
    "sLifetime",
)


class _VisitorCallSites(ast.NodeVisitor):
    """Collect primitive call sites with their enclosing function."""

    def __init__(self, sRelativePath):
        self.sRelativePath = sRelativePath
        self.listRows = []
        self._listFunctionStack = []

    def visit_FunctionDef(self, nodeFunction):
        self._listFunctionStack.append(nodeFunction.name)
        self.generic_visit(nodeFunction)
        self._listFunctionStack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, nodeCall):
        sPrimitive = _fsCalledName(nodeCall)
        if sPrimitive in DICT_PRIMITIVE_ACCESS:
            self.listRows.append(self._fdictBuildRow(nodeCall, sPrimitive))
        self.generic_visit(nodeCall)

    def _fdictBuildRow(self, nodeCall, sPrimitive):
        """Return one row: machine-derived identity, unclassified meaning."""
        sAccess = DICT_PRIMITIVE_ACCESS[sPrimitive]
        dictRow = {
            "sFile": self.sRelativePath,
            "sFunction": (
                self._listFunctionStack[-1]
                if self._listFunctionStack else "<module>"
            ),
            "sPrimitive": sPrimitive,
            "iOrdinal": 0,
            "sFingerprint": _fsFingerprintCall(nodeCall),
            "sAccess": sAccess,
            "bMutationCapable": sAccess in SET_MUTATION_CAPABLE_ACCESS,
            "bInsideGateway": (
                self.sRelativePath in SET_GATEWAY_MODULES
            ),
        }
        for sField in TUPLE_REVIEWER_FIELDS:
            dictRow[sField] = S_UNCLASSIFIED
        return dictRow


def _fsCalledName(nodeCall):
    """Return the called name for a Name or Attribute call, else ''."""
    nodeFunction = nodeCall.func
    if isinstance(nodeFunction, ast.Name):
        return nodeFunction.id
    if isinstance(nodeFunction, ast.Attribute):
        return nodeFunction.attr
    return ""


def _fsFingerprintCall(nodeCall):
    """Return a stable digest of the call expression's own source.

    Unparsed from the AST rather than sliced out of the file, so
    reformatting and comment changes do not move it while a change to
    the call ITSELF -- a new argument, a different target -- does. A row
    whose fingerprint moved has to be re-read; that is what the
    fingerprint is for.
    """
    try:
        sSource = ast.unparse(nodeCall)
    except AttributeError:  # pragma: no cover - Python < 3.9
        sSource = ast.dump(nodeCall)
    return hashlib.sha256(sSource.encode("utf-8")).hexdigest()[:16]


def flistScanPackage():
    """Return every primitive call site in the package, ordinal-numbered."""
    listRows = []
    for pathModule in sorted(PATH_PACKAGE.rglob("*.py")):
        if "__pycache__" in pathModule.parts:
            continue
        sRelativePath = str(pathModule.relative_to(PATH_PACKAGE))
        visitor = _VisitorCallSites(sRelativePath)
        visitor.visit(ast.parse(pathModule.read_text(encoding="utf-8")))
        listRows.extend(visitor.listRows)
    return _flistNumberOrdinals(listRows)


def _flistNumberOrdinals(listRows):
    """Disambiguate repeated (file, function, primitive) triples.

    One function may call the same primitive several times, and each
    call is its own row with its own classification. The ordinal makes
    the key unique without depending on a line number, which every
    unrelated edit above it would change.
    """
    dictCounts = {}
    for dictRow in listRows:
        tKey = (dictRow["sFile"], dictRow["sFunction"], dictRow["sPrimitive"])
        dictRow["iOrdinal"] = dictCounts.get(tKey, 0)
        dictCounts[tKey] = dictRow["iOrdinal"] + 1
    return listRows


def fsRowKey(dictRow):
    """Return the stable identity of one call site."""
    return "|".join((
        dictRow["sFile"], dictRow["sFunction"], dictRow["sPrimitive"],
        str(dictRow["iOrdinal"]),
    ))


def fdictLoadInventory():
    """Return the checked-in inventory, or an empty one."""
    if not PATH_INVENTORY.is_file():
        return {"listRows": []}
    return json.loads(PATH_INVENTORY.read_text(encoding="utf-8"))


def fdictCompareAgainstSource(dictInventory, listScanned):
    """Return the drift between the checked-in record and a fresh scan."""
    dictRecorded = {fsRowKey(row): row for row in dictInventory["listRows"]}
    dictScanned = {fsRowKey(row): row for row in listScanned}
    listDuplicated = _flistDuplicatedKeys(dictInventory["listRows"])
    listEdited = [
        sKey for sKey in sorted(set(dictRecorded) & set(dictScanned))
        if dictRecorded[sKey]["sFingerprint"] != (
            dictScanned[sKey]["sFingerprint"]
        )
    ]
    return {
        "listAdded": sorted(set(dictScanned) - set(dictRecorded)),
        "listRemoved": sorted(set(dictRecorded) - set(dictScanned)),
        "listDuplicated": listDuplicated,
        "listEdited": listEdited,
    }


def _flistDuplicatedKeys(listRows):
    """Return keys appearing more than once in the checked-in record."""
    dictSeen = {}
    for dictRow in listRows:
        sKey = fsRowKey(dictRow)
        dictSeen[sKey] = dictSeen.get(sKey, 0) + 1
    return sorted(sKey for sKey, iCount in dictSeen.items() if iCount > 1)


def flistUnclassifiedKeys(dictInventory):
    """Return the keys still awaiting a reviewer's semantic judgement."""
    return sorted(
        fsRowKey(dictRow) for dictRow in dictInventory["listRows"]
        if any(
            dictRow.get(sField) == S_UNCLASSIFIED
            for sField in TUPLE_REVIEWER_FIELDS
        )
    )


def _fdictBuildInventory(listScanned, dictExisting):
    """Merge a fresh scan with any classifications already recorded."""
    dictRecorded = {fsRowKey(row): row for row in dictExisting["listRows"]}
    listMerged = []
    for dictRow in listScanned:
        dictPrevious = dictRecorded.get(fsRowKey(dictRow))
        if dictPrevious is not None and dictPrevious.get(
            "sFingerprint",
        ) == dictRow["sFingerprint"]:
            for sField in TUPLE_REVIEWER_FIELDS:
                dictRow[sField] = dictPrevious.get(sField, S_UNCLASSIFIED)
        listMerged.append(dictRow)
    return {
        "sPurpose": (
            "Every container-mutation call site in vaibify/, one row "
            "each. Machine-derived identity; reviewer-classified "
            "meaning. See tools/generateMutationInventory.py."
        ),
        "iRowCount": len(listMerged),
        "listRows": listMerged,
    }


def main():
    """Print, write, or check the inventory; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true",
        help="Rewrite tests/mutationInventory.json from a fresh scan.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Report drift against the checked-in inventory.",
    )
    args = parser.parse_args()

    listScanned = flistScanPackage()
    if args.check:
        dictDrift = fdictCompareAgainstSource(
            fdictLoadInventory(), listScanned,
        )
        print(json.dumps(dictDrift, indent=2))
        return 1 if any(dictDrift.values()) else 0
    dictInventory = _fdictBuildInventory(listScanned, fdictLoadInventory())
    if args.write:
        PATH_INVENTORY.write_text(
            json.dumps(dictInventory, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        iUnclassified = len(flistUnclassifiedKeys(dictInventory))
        print(
            f"Wrote {dictInventory['iRowCount']} rows to "
            f"{PATH_INVENTORY.relative_to(PATH_REPOSITORY)}; "
            f"{iUnclassified} still need a reviewer."
        )
        return 0
    print(json.dumps(dictInventory, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
