#!/usr/bin/env python3
"""Enumerate every call site that hands command text to the connection.

This is Phase A of the host-mode plan: before vaibify can run pipelines
directly on the host (no Docker), every call site that hands COMMAND
TEXT to the container connection must be enumerated and carry a
reviewed disposition saying whether that command is meaningful on a
host. The pattern is the mutation inventory
(``tools/generateMutationInventory.py``): the machine proves
COMPLETENESS -- one row per call site of the three duck-typed exec
entry points, with a stable fingerprint -- and a human records the
per-site judgement as a disposition. A fingerprint is an identity,
never a warrant: a disposition must name the supporting symbols its
review relied on, because the identity alone cannot say what the
command does on a host.

The three entry points in scope are the arbitrary-command primitives on
the connection object. The typed reads and the file writes are
separately governed and are deliberately NOT in scope here.

A command argument the scan cannot identify or unparse becomes a row
whose expression is ``UNKNOWN`` -- never a site that disappears. The
withdrawn director module is the standing demonstration of why: a
completeness record keyed on decoding an expression somebody else
writes produces zero rows for exactly the most permissive site.

A PASSED CALLABLE is a call site too. ``asyncio.to_thread(
connection.ftResultExecuteCommand, sContainerId, sCommand)`` hands
command text to the connection exactly as a direct call does, but the
attribute is an argument, not a call's function -- the shape that once
hid 21 mutation-capable sites from the mutation inventory's first
scanner. When the attribute is the wrapping call's first positional
argument, the command decodes from the shifted argument slots; any
other reference to the three names is a row with the command UNKNOWN.

Dispositions, keyed by fingerprint in ``dictDispositions``:

* ``portable`` -- identical semantics on a POSIX host.
* ``host-variant`` -- a host implementation is specified in the
  host-mode plan; the site or its route will select by mode.
* ``container-only`` -- must be refused server-side for host resources.

A site with no disposition is UNDISPOSED: present in the rows, absent
from the dispositions. The committed ``iUndisposedSiteBudget`` must
equal the actual undisposed count exactly -- no slack -- and may only
ever fall; ``tests/testHostCapabilityInventory.py`` enforces both.

Usage::

    python tools/generateHostCapabilityInventory.py            # print
    python tools/generateHostCapabilityInventory.py --write    # update
    python tools/generateHostCapabilityInventory.py --check    # drift
"""

import argparse
import ast
import hashlib
import json
import pathlib
import sys

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"
PATH_INVENTORY = PATH_REPOSITORY / "tests" / "hostCapabilityInventory.json"

# tools/ is not a package and this module is also loaded by path, so
# put its own directory on the import path before reaching a sibling.
sys.path.insert(0, str(PATH_REPOSITORY / "tools"))

import ledgerFormat  # noqa: E402

# The keys holding record collections, rendered one record per line.
# See tools/ledgerFormat.py for why the layout is load-bearing.
T_RECORD_COLLECTION_KEYS = ("listRows", "dictDispositions")

S_UNKNOWN_COMMAND = "UNKNOWN"

# The three duck-typed exec entry points on the connection object. All
# three carry the shell text as the second bound-call positional
# argument (after the container id) or as the keyword ``sCommand`` --
# see the signatures in vaibify/docker/dockerConnection.py.
TUPLE_EXEC_METHOD_NAMES = (
    "ftResultExecuteCommand",
    "ftRunInContainerStreamed",
    "ftRunInContainerStreamedWithChunks",
)
I_COMMAND_POSITIONAL_INDEX = 1
S_COMMAND_KEYWORD = "sCommand"

SET_DISPOSITION_VOCABULARY = frozenset({
    "portable", "host-variant", "container-only",
})
TUPLE_DISPOSITION_FIELDS = (
    "sDisposition", "sSupportingSymbols", "sRationale",
)

# Directory names never scanned, matching the mutation inventory's
# scope: the record describes the shipped package, not its tooling.
SET_SKIPPED_DIRECTORY_NAMES = frozenset({"tests", "tools", "docs"})


class _VisitorExecCallSites(ast.NodeVisitor):
    """Collect every reference that hands command text to the connection.

    Two shapes: a direct call whose function is an attribute named one
    of the three entry points, and a PASSED CALLABLE -- the bound
    method handed to a wrapper such as ``asyncio.to_thread``. Any other
    reference to the three names (an alias assignment, an unrecognised
    wrapper shape) still becomes a row, with the command UNKNOWN.
    """

    def __init__(self, sRelativePath):
        self.sRelativePath = sRelativePath
        self.listRows = []
        self._listScopeNames = []
        self._setHandledAttributeNodes = set()

    def visit_FunctionDef(self, nodeFunction):
        self._listScopeNames.append(nodeFunction.name)
        self.generic_visit(nodeFunction)
        self._listScopeNames.pop()

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Call(self, nodeCall):
        if isinstance(nodeCall.func, ast.Attribute) and (
            nodeCall.func.attr in TUPLE_EXEC_METHOD_NAMES
        ):
            self._setHandledAttributeNodes.add(id(nodeCall.func))
            self._fnAppendRow(
                nodeCall.func.attr,
                _fsCommandExpressionForCall(nodeCall, 0),
            )
        else:
            self._fnRecordPassedCallableArguments(nodeCall)
        self.generic_visit(nodeCall)

    def visit_Attribute(self, nodeAttribute):
        if nodeAttribute.attr in TUPLE_EXEC_METHOD_NAMES and (
            id(nodeAttribute) not in self._setHandledAttributeNodes
        ):
            self._fnAppendRow(nodeAttribute.attr, S_UNKNOWN_COMMAND)
        self.generic_visit(nodeAttribute)

    def _fnRecordPassedCallableArguments(self, nodeCall):
        """Record each exec method passed as an argument to this call.

        Only the wrapper's FIRST positional argument slot is decoded --
        the ``asyncio.to_thread(connection.method, sContainerId,
        sCommand, ...)`` shape, where the command sits one slot further
        along than in a direct call. A reference in any other slot is
        recorded with the command UNKNOWN by ``visit_Attribute``.
        """
        if not nodeCall.args:
            return
        nodeFirst = nodeCall.args[0]
        if isinstance(nodeFirst, ast.Attribute) and (
            nodeFirst.attr in TUPLE_EXEC_METHOD_NAMES
        ):
            self._setHandledAttributeNodes.add(id(nodeFirst))
            self._fnAppendRow(
                nodeFirst.attr, _fsCommandExpressionForCall(nodeCall, 1),
            )

    def _fnAppendRow(self, sMethod, sCommandExpression):
        """Append one un-fingerprinted row at the current scope."""
        self.listRows.append({
            "sModule": self.sRelativePath,
            "sEnclosingFunction": (
                ".".join(self._listScopeNames)
                if self._listScopeNames else "<module>"
            ),
            "sMethod": sMethod,
            "sCommandExpression": sCommandExpression,
        })


def _fsCommandExpressionForCall(nodeCall, iSlotShift):
    """Return the unparsed command argument, or ``UNKNOWN``.

    ``iSlotShift`` is 0 for a direct call and 1 for the passed-callable
    wrapper shape, whose first slot holds the callable itself.
    Fail-closed: a call spelled through ``*arguments``, one with too few
    positional arguments and no ``sCommand`` keyword, or one whose
    argument resists unparsing still produces a row -- with the command
    marked UNKNOWN -- never a site that disappears.
    """
    iCommandIndex = I_COMMAND_POSITIONAL_INDEX + iSlotShift
    nodeCommand = None
    for nodeKeyword in nodeCall.keywords:
        if nodeKeyword.arg == S_COMMAND_KEYWORD:
            nodeCommand = nodeKeyword.value
    if nodeCommand is None:
        listPositional = nodeCall.args
        if any(
            isinstance(nodeArgument, ast.Starred)
            for nodeArgument in listPositional[:iCommandIndex + 1]
        ):
            return S_UNKNOWN_COMMAND
        if len(listPositional) <= iCommandIndex:
            return S_UNKNOWN_COMMAND
        nodeCommand = listPositional[iCommandIndex]
    try:
        return ast.unparse(nodeCommand)
    except Exception:
        return S_UNKNOWN_COMMAND


def _fsFingerprintRow(dictRow, iOccurrence):
    """Return a stable, line-number-free identity for one call site.

    The hash covers (module, enclosing function, method, command
    expression) so a pure move within the function does not churn the
    record. Two identical sites in one function are distinguished by an
    occurrence suffix -- collisions must stay distinguishable, never be
    merged into one row.
    """
    sIdentity = "\n".join((
        dictRow["sModule"],
        dictRow["sEnclosingFunction"],
        dictRow["sMethod"],
        dictRow["sCommandExpression"],
    ))
    sDigest = hashlib.sha256(sIdentity.encode("utf-8")).hexdigest()
    if iOccurrence:
        return f"{sDigest}#{iOccurrence}"
    return sDigest


def flistScanModuleSource(sRelativePath, sSource):
    """Return the fingerprinted rows for one module's source text."""
    visitor = _VisitorExecCallSites(sRelativePath)
    visitor.visit(ast.parse(sSource))
    dictOccurrenceCounts = {}
    for dictRow in visitor.listRows:
        tIdentity = (
            dictRow["sModule"], dictRow["sEnclosingFunction"],
            dictRow["sMethod"], dictRow["sCommandExpression"],
        )
        iOccurrence = dictOccurrenceCounts.get(tIdentity, 0)
        dictOccurrenceCounts[tIdentity] = iOccurrence + 1
        dictRow["sFingerprint"] = _fsFingerprintRow(dictRow, iOccurrence)
    return visitor.listRows


def flistScanPackage():
    """Return the fingerprinted rows for every module in the package."""
    listRows = []
    for pathModule in sorted(PATH_PACKAGE.rglob("*.py")):
        pathRelative = pathModule.relative_to(PATH_REPOSITORY)
        if SET_SKIPPED_DIRECTORY_NAMES & set(pathRelative.parts):
            continue
        listRows.extend(flistScanModuleSource(
            pathRelative.as_posix(),
            pathModule.read_text(encoding="utf-8"),
        ))
    listRows.sort(key=lambda dictRow: (
        dictRow["sModule"], dictRow["sEnclosingFunction"],
        dictRow["sMethod"], dictRow["sCommandExpression"],
        dictRow["sFingerprint"],
    ))
    return listRows


def fdictLoadInventory():
    """Return the committed inventory, or an empty one before first write."""
    if not PATH_INVENTORY.exists():
        return {
            "listRows": [], "dictDispositions": {},
            "iUndisposedSiteBudget": 0,
        }
    return json.loads(PATH_INVENTORY.read_text(encoding="utf-8"))


def flistUndisposedFingerprints(dictInventory):
    """Return the fingerprints of rows that carry no disposition."""
    return sorted(
        dictRow["sFingerprint"] for dictRow in dictInventory["listRows"]
        if dictRow["sFingerprint"] not in dictInventory["dictDispositions"]
    )


def flistDispositionSchemaViolations(dictInventory):
    """Return every disposition that is malformed or orphaned.

    Orphaned means keyed by a fingerprint no row carries: a judgement
    bound to a site that no longer exists is a claim about code nobody
    can re-read. Malformed means a disposition outside the closed
    vocabulary, or one that names no supporting symbols -- the
    fingerprint is an identity, never a warrant, so the judgement must
    say what it read.
    """
    setKnown = {
        dictRow["sFingerprint"] for dictRow in dictInventory["listRows"]
    }
    listViolations = []
    for sFingerprint, dictJudgement in sorted(
        dictInventory["dictDispositions"].items(),
    ):
        if sFingerprint not in setKnown:
            listViolations.append(f"{sFingerprint}: orphaned disposition")
        if dictJudgement.get("sDisposition") not in (
            SET_DISPOSITION_VOCABULARY
        ):
            listViolations.append(
                f"{sFingerprint}: sDisposition is "
                f"{dictJudgement.get('sDisposition')!r}, not one of "
                f"{sorted(SET_DISPOSITION_VOCABULARY)}"
            )
        for sField in ("sSupportingSymbols", "sRationale"):
            if not str(dictJudgement.get(sField) or "").strip():
                listViolations.append(f"{sFingerprint}: {sField} is empty")
    return listViolations


def fdictCompareAgainstSource(dictInventory, listScanned):
    """Return every way the committed inventory drifts from a fresh scan."""
    dictRecorded = {
        dictRow["sFingerprint"]: dictRow
        for dictRow in dictInventory["listRows"]
    }
    dictScanned = {
        dictRow["sFingerprint"]: dictRow for dictRow in listScanned
    }
    listAltered = []
    for sFingerprint in sorted(set(dictRecorded) & set(dictScanned)):
        if dictRecorded[sFingerprint] != dictScanned[sFingerprint]:
            listAltered.append(sFingerprint)
    iUndisposed = len(flistUndisposedFingerprints(dictInventory))
    return {
        "listAdded": sorted(set(dictScanned) - set(dictRecorded)),
        "listRemoved": sorted(set(dictRecorded) - set(dictScanned)),
        "listAltered": listAltered,
        "listDuplicated": _flistDuplicatedFingerprints(
            dictInventory["listRows"],
        ),
        "listDispositionViolations": flistDispositionSchemaViolations(
            dictInventory,
        ),
        "listCountMismatch": (
            [] if len(dictInventory["listRows"]) == len(listScanned)
            else [
                f"recorded {len(dictInventory['listRows'])}, "
                f"scanned {len(listScanned)}"
            ]
        ),
        "listBudgetMismatch": (
            [] if dictInventory.get("iUndisposedSiteBudget") == iUndisposed
            else [
                f"budget says {dictInventory.get('iUndisposedSiteBudget')}, "
                f"{iUndisposed} rows are undisposed"
            ]
        ),
    }


def _flistDuplicatedFingerprints(listRows):
    """Return fingerprints appearing more than once in the record."""
    dictSeen = {}
    for dictRow in listRows:
        sFingerprint = dictRow["sFingerprint"]
        dictSeen[sFingerprint] = dictSeen.get(sFingerprint, 0) + 1
    return sorted(
        sFingerprint for sFingerprint, iCount in dictSeen.items()
        if iCount > 1
    )


def fdictBuildInventory(listScanned, dictExisting):
    """Merge a fresh scan with the dispositions already recorded.

    A disposition survives only while its fingerprint still names a
    scanned row -- the fingerprint covers the command expression, so an
    edited command sends the judgement back for re-reading rather than
    carrying it onto text nobody reviewed. The budget is recomputed to
    the exact undisposed count; the drift test holds it there, and the
    review of any commit that raises it is where the fall-only ratchet
    is enforced.
    """
    setScanned = {dictRow["sFingerprint"] for dictRow in listScanned}
    dictDispositions = {
        sFingerprint: dictJudgement
        for sFingerprint, dictJudgement in (
            dictExisting.get("dictDispositions", {}).items()
        )
        if sFingerprint in setScanned
    }
    dictInventory = {
        "sPurpose": (
            "Every call site handing command text to the container "
            "connection, one row each: machine-derived identity, "
            "reviewer-recorded host-mode disposition. See "
            "tools/generateHostCapabilityInventory.py."
        ),
        "listRows": listScanned,
        "dictDispositions": dictDispositions,
        "iUndisposedSiteBudget": 0,
    }
    dictInventory["iUndisposedSiteBudget"] = len(
        flistUndisposedFingerprints(dictInventory),
    )
    return dictInventory


def main():
    """Print, write, or check the inventory; return a process exit code."""
    parserOptions = argparse.ArgumentParser(description=__doc__)
    parserOptions.add_argument(
        "--write", action="store_true",
        help="Rewrite tests/hostCapabilityInventory.json from a fresh scan.",
    )
    parserOptions.add_argument(
        "--check", action="store_true",
        help="Report drift against the checked-in inventory.",
    )
    namespaceOptions = parserOptions.parse_args()

    listScanned = flistScanPackage()
    if namespaceOptions.check:
        dictDrift = fdictCompareAgainstSource(
            fdictLoadInventory(), listScanned,
        )
        print(json.dumps(dictDrift, indent=2))
        return 1 if any(dictDrift.values()) else 0
    dictInventory = fdictBuildInventory(listScanned, fdictLoadInventory())
    sRendered = ledgerFormat.fsRenderLedger(
        dictInventory, T_RECORD_COLLECTION_KEYS,
    )
    if namespaceOptions.write:
        PATH_INVENTORY.write_text(sRendered, encoding="utf-8")
        print(
            f"Wrote {len(dictInventory['listRows'])} rows to "
            f"{PATH_INVENTORY.relative_to(PATH_REPOSITORY)}; "
            f"{dictInventory['iUndisposedSiteBudget']} still undisposed."
        )
        return 0
    print(sRendered, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
