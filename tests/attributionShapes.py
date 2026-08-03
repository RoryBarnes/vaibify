"""The five hard attribution shapes, as real executable source.

Migration plan R5 requires synthetic examples of the shapes runtime
attribution has to survive, and requires them to be REAL: the scanner
must produce rows from this file the same way it produces them from the
package, and the frames must be frames a stack walk actually sees. A
mock stack would prove that the matcher agrees with a hand-written
tuple, which is the thing this file exists not to do.

So the primitive here is a recording stand-in whose METHOD NAMES are
the scanner's own vocabulary (``fnWriteFile``). The scanner is purely
syntactic, so this module yields genuine inventory rows; the recorder
reads the admission mode out of the REAL
:mod:`vaibify.config.mutationAdmission` contextvar, so an observation's
mode is the one a carrier actually minted rather than one a test
declared.

The shapes, one function each:

1. ``fnWriteThroughExecutorThread`` — a bound primitive passed into
   ``asyncio.to_thread``; the passing frame does not exist inside the
   worker.
2. ``fnWriteThroughLocalAlias`` — the row is recorded at the binding,
   the frame reports the call.
3. ``fnWriteTwiceInOneFunction`` / ``fnWriteIdenticalExpressionTwice``
   — two rows, one frame, distinguishable and not.
4. ``fnWriteThroughSharedHelper`` — one row reached under two carrier
   modes.
5. ``fnWriteInsideBackgroundTask`` — work continuing in a context
   copied into a background task.
"""

import asyncio

from tools.mutationAttribution import (
    S_MODE_NO_ADMISSION,
    fdictRecordMutationObservation,
)
from vaibify.config import mutationAdmission


class RecordingPrimitiveHost:
    """A primitive stand-in that records an observation when it runs.

    ``dictInvocationContext`` is set by the driver immediately before a
    shape runs and carries the facts the carrier knows by construction
    (operation kind, target, lane, and the carrier invocation identity
    captured where the carrier was called). Keeping them off the call
    expression is deliberate: the shapes must read like the code they
    stand for, because their source expressions are what the scanner
    fingerprints.
    """

    def __init__(self, dictIndex, sContainerId):
        self.dictIndex = dictIndex
        self.sContainerId = sContainerId
        self.listObservations = []
        self.dictInvocationContext = {}

    def fnWriteFile(self, sContainerId, sPath, baBody):
        """Record the effect the way a guarded primitive would."""
        del baBody
        self.listObservations.append(fdictRecordMutationObservation(
            self.dictIndex, "fnWriteFile",
            _fsReadObservedAdmissionMode(sContainerId),
            self.dictInvocationContext.get("sOperationKind", "file-write"),
            sPath,
            self.dictInvocationContext.get("sExecutionLane", "background"),
            sCarrierInvocation=self.dictInvocationContext.get(
                "sCarrierInvocation", "",
            ),
            sEntryPointDeclaration=self.dictInvocationContext.get(
                "sEntryPointDeclaration", "UNDECLARED",
            ),
        ))


def _fsReadObservedAdmissionMode(sContainerId):
    """Return the live carrier admission's mode, or the no-admission mark."""
    admissionLive = mutationAdmission.fadmissionActiveForContainerId(
        sContainerId,
    )
    if admissionLive is None:
        return S_MODE_NO_ADMISSION
    return admissionLive.sMode


def fnWriteThroughDirectCall(hostPrimitive, sContainerId):
    """Shape 0 — the baseline: one call, one row, one frame."""
    hostPrimitive.fnWriteFile(sContainerId, "/workspace/direct.json", b"{}")


def fnWriteThroughLocalAlias(hostPrimitive, sContainerId):
    """Shape 2 — the row is at the binding; the frame is at the call."""
    fnWriterBound = hostPrimitive.fnWriteFile
    fnWriterBound(sContainerId, "/workspace/aliased.json", b"{}")


def fnWriteTwiceInOneFunction(hostPrimitive, sContainerId, bSecond):
    """Shape 3 — two rows, one frame, distinguishable expressions."""
    if bSecond:
        hostPrimitive.fnWriteFile(sContainerId, "/workspace/beta.json", b"{}")
        return
    hostPrimitive.fnWriteFile(sContainerId, "/workspace/alpha.json", b"{}")


def fnWriteIdenticalExpressionTwice(hostPrimitive, sContainerId, bSecond):
    """Shape 3b — two rows whose source expressions are identical."""
    if bSecond:
        hostPrimitive.fnWriteFile(sContainerId, "/workspace/same.json", b"{}")
        return
    hostPrimitive.fnWriteFile(sContainerId, "/workspace/same.json", b"{}")


def fnWriteThroughSharedHelper(hostPrimitive, sContainerId, sPath):
    """Shape 4 — one row, reached under more than one carrier mode."""
    hostPrimitive.fnWriteFile(sContainerId, sPath, b"{}")


async def fnWriteInsideBackgroundTask(hostPrimitive, sContainerId):
    """Shape 5 — work continuing in a context copied into a task."""
    hostPrimitive.fnWriteFile(
        sContainerId, "/workspace/background.json", b"{}",
    )
    return "backgroundWorkFinished"


async def fnWriteThroughExecutorThread(hostPrimitive, sContainerId):
    """Shape 1 — the primitive is PASSED; the passing frame is lost."""
    await asyncio.to_thread(
        hostPrimitive.fnWriteFile, sContainerId,
        "/workspace/threaded.json", b"{}",
    )
    return "executorWorkFinished"
