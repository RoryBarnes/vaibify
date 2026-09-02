"""A carrier refusal must never be mistaken for an unreadable file.

WHY THIS FILE EXISTS
--------------------

``MutationNotAdmittedError`` subclasses ``PermissionError``, so it IS an
``OSError``. Every ``except OSError`` in :mod:`levelGates` catches it by
construction, and every ``except Exception`` did too — and those handlers
wrap ``filesRepo`` calls that reach the container's exec primitive on a
``ContainerRepoFiles`` adapter (``fbIsFile``, ``fdictHashFiles``,
``fdictStatMtimes``; only ``fbaReadBytes`` uses the typed-read
carve-out).

The consequence was a silent one, which is the dangerous kind. A gate
that could not hash a file answers "not verified", so a refused
admission made a workflow report a LOWER reproducibility level than it
had earned — no exception, no log line, a downgraded badge. That is the
misrepresentation AGENTS.md forbids outright, and it also meant the
carrier migration's only proof mechanism ("forget a carrier and the
primitive raises loudly") was FALSE for every route whose path ran
through this module.

Narrowing the handlers does not fix it, and that is worth a test rather
than a comment, because narrowing is the obvious fix and it is wrong:
``except OSError`` swallows the refusal exactly as thoroughly as
``except Exception``. What fixes it is
:func:`levelGates.fnReRaiseControlPlaneRefusal`, called first in each
handler.

WHAT THIS ASSERTS
-----------------

For every gate function that swallows, and for every adapter method it
might reach: if the method raises a refusal, the refusal must come back
out. The adapter raises on ONE named method and answers benignly on the
rest, because an all-raising adapter would be satisfied by the FIRST
unguarded call — ``fbVerifyReproduceScript`` calls ``fbIsFile`` before
its ``try``, so an all-raising adapter would propagate from there,
prove nothing about the handler, and keep passing with the guard
deleted.
"""

import pytest

from vaibify.config.mutationAdmission import MutationNotAdmittedError
from vaibify.reproducibility import levelGates


# Every method a repo-files adapter offers. Iterating the whole surface
# rather than the ones each gate is believed to call is deliberate: the
# belief is what would go stale when a gate starts reaching one more.
LIST_ADAPTER_METHODS = [
    "fbIsFile",
    "fbIsDir",
    "fsReadText",
    "fbaReadBytes",
    "fdictHashFiles",
    "fdictHashAbsolutePaths",
    "fdictStatMtimes",
    "flistListJsonFilenames",
    "fdictReadDirJsonContents",
]


class RefusingRepoFiles:
    """A repo-files adapter that refuses ONE method and obliges the rest.

    ``bRaised`` is what makes the assertion honest: a gate that never
    calls the refusing method proves nothing either way, and must not be
    reported as a pass OR a failure. Only a gate that provoked the
    refusal and then returned a value is swallowing it.
    """

    def __init__(self, sRefusingMethod):
        self.sRefusingMethod = sRefusingMethod
        self.bRaised = False

    def _fnRefuseOrOblige(self, sMethodName, objBenign):
        if sMethodName != self.sRefusingMethod:
            return objBenign
        self.bRaised = True
        raise MutationNotAdmittedError(
            f"{sMethodName} was attempted from a request lane without a "
            "commit-guard admission."
        )

    def fsLocalRootOrNone(self):
        """Container adapters have no host root; the gates branch on it."""
        return None

    def fbIsFile(self, sRelPath):
        return self._fnRefuseOrOblige("fbIsFile", True)

    def fbIsDir(self, sRelPath):
        return self._fnRefuseOrOblige("fbIsDir", True)

    def fsReadText(self, sRelPath):
        return self._fnRefuseOrOblige("fsReadText", "")

    def fbaReadBytes(self, sRelPath):
        return self._fnRefuseOrOblige("fbaReadBytes", b"")

    def fdictHashFiles(self, listRelPaths):
        return self._fnRefuseOrOblige("fdictHashFiles", {})

    def fdictHashAbsolutePaths(self, listAbsPaths):
        return self._fnRefuseOrOblige("fdictHashAbsolutePaths", {})

    def fdictStatMtimes(self, listRelPaths):
        return self._fnRefuseOrOblige("fdictStatMtimes", {})

    def flistListJsonFilenames(self, sRelDir):
        return self._fnRefuseOrOblige("flistListJsonFilenames", [])

    def fdictReadDirJsonContents(self, sRelDir):
        return self._fnRefuseOrOblige("fdictReadDirJsonContents", {})


DICT_WORKFLOW = {
    "sWorkflowName": "Refusal Propagation",
    "sProjectRepoPath": "/workspace/project",
    "saBinaryPaths": ["/usr/local/bin/solver"],
    "listSteps": [
        {
            "sName": "Step A",
            "sDirectory": "stepA",
            "saOutputDataFiles": ["results.json"],
            "saPlotFiles": ["figure.pdf"],
            "saDataCommands": ["python run.py"],
            "dictVerification": {"sUnitTest": "passed"},
        },
    ],
}


# Every function in levelGates that catches OSError-or-broader around a
# filesRepo call, paired with a way to drive it. Derived by scanning the
# module's except clauses, not from memory; a new swallow site that is
# not listed here is caught by testEverySwallowSiteIsCovered below.
LIST_SWALLOWING_GATES = [
    (
        "_fsSyncStatusFingerprint",
        lambda files: levelGates._fsSyncStatusFingerprint(files),
    ),
    (
        "fbVerifyManifestComplete",
        lambda files: levelGates.fbVerifyManifestComplete(
            files, DICT_WORKFLOW,
        ),
    ),
    (
        "fbVerifyReproduceScript",
        lambda files: levelGates.fbVerifyReproduceScript(
            files, DICT_WORKFLOW,
        ),
    ),
    (
        "_fdictLiveHashesOrNone",
        lambda files: levelGates._fdictLiveHashesOrNone(
            files, ["stepA/run.py"],
        ),
    ),
    (
        "_fdictRecomputeSupervisionEvidence",
        lambda files: levelGates._fdictRecomputeSupervisionEvidence(
            DICT_WORKFLOW, files,
        ),
    ),
    (
        "_fsBinaryStateFingerprint",
        lambda files: levelGates._fsBinaryStateFingerprint(
            DICT_WORKFLOW, files,
        ),
    ),
    (
        "_fbEnvelopeUnchangedSinceVerify",
        lambda files: levelGates._fbEnvelopeUnchangedSinceVerify(
            files, ["reproduce.sh"], {"dictComparedHashes": {}},
        ),
    ),
    (
        "_fsEnvelopeStateFingerprint",
        lambda files: levelGates._fsEnvelopeStateFingerprint(files),
    ),
    (
        "_fsetDriftedBinaryPaths",
        lambda files: levelGates._fsetDriftedBinaryPaths(
            DICT_WORKFLOW, files,
        ),
    ),
    (
        "_fdictCapturedBinaryHashes",
        lambda files: levelGates._fdictCapturedBinaryHashes(files),
    ),
    (
        "_fdictReadManifestPathHashes",
        lambda files: levelGates._fdictReadManifestPathHashes(files),
    ),
]


@pytest.mark.falsification
@pytest.mark.parametrize(
    "tGate", LIST_SWALLOWING_GATES, ids=[t[0] for t in LIST_SWALLOWING_GATES],
)
def testAGateNeverSwallowsAnAdmissionRefusal(tGate):
    """A refusal raised inside a gate must come back out of it.

    Kills: deleting the ``fnReRaiseControlPlaneRefusal`` call from any
    one handler in ``levelGates.py``.
    """
    sGateName, fnCallGate = tGate
    listSwallowed = []
    for sMethod in LIST_ADAPTER_METHODS:
        filesRefusing = RefusingRepoFiles(sMethod)
        try:
            fnCallGate(filesRefusing)
        except MutationNotAdmittedError:
            continue
        except Exception as errorOther:
            if filesRefusing.bRaised:
                listSwallowed.append(
                    f"{sMethod} -> converted into "
                    f"{type(errorOther).__name__}"
                )
            continue
        if filesRefusing.bRaised:
            listSwallowed.append(f"{sMethod} -> returned normally")
    assert listSwallowed == [], (
        f"{sGateName} swallowed a carrier refusal: {listSwallowed}. The "
        "gate then answers as though the file were merely unreadable, "
        "so the dashboard reports a LOWER reproducibility level than "
        "the workflow has, with nothing raised and nothing logged. Call "
        "levelGates.fnReRaiseControlPlaneRefusal(error) first in the "
        "handler that catches it."
    )


def testEverySwallowSiteIsCovered():
    """Every OSError-or-broader handler in levelGates is driven above.

    Without this, adding a tenth swallow site would leave it untested
    and the suite would stay green -- which is how the nine got here.
    Parses the module rather than trusting a list: the check has to be
    able to see a handler nobody remembered to register.
    """
    import ast
    import inspect

    treeModule = ast.parse(inspect.getsource(levelGates))
    dictOwnerByHandler = {}
    for nodeFunction in ast.walk(treeModule):
        if not isinstance(
            nodeFunction, (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        for nodeChild in ast.walk(nodeFunction):
            dictOwnerByHandler[id(nodeChild)] = nodeFunction.name

    setSwallowingNames = {"Exception", "OSError", "PermissionError"}
    setOwners = set()
    for nodeTry in ast.walk(treeModule):
        if not isinstance(nodeTry, ast.Try):
            continue
        for handler in nodeTry.handlers:
            if handler.type is None:
                setOwners.add(dictOwnerByHandler.get(id(handler), ""))
                continue
            listCaught = (
                handler.type.elts
                if isinstance(handler.type, ast.Tuple)
                else [handler.type]
            )
            setCaught = {
                getattr(nodeName, "id", "") for nodeName in listCaught
            }
            if setCaught & setSwallowingNames:
                setOwners.add(dictOwnerByHandler.get(id(handler), ""))

    setOwners.discard("fnReRaiseControlPlaneRefusal")
    setDriven = {sName for sName, _fn in LIST_SWALLOWING_GATES}
    setUncovered = setOwners - setDriven
    assert setUncovered == set(), (
        f"these levelGates functions catch OSError or broader and are "
        f"not driven by testAGateNeverSwallowsAnAdmissionRefusal: "
        f"{sorted(setUncovered)}. A handler that catches OSError also "
        "catches MutationNotAdmittedError, so an untested one is a "
        "silent reproducibility-level downgrade waiting to happen. Add "
        "it to LIST_SWALLOWING_GATES."
    )
