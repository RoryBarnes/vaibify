"""Which blocked Level 3 requirement has to be fixed FIRST.

Most of the ladder is order-free: a researcher can declare their
binaries, answer the determinism questions and pin a Dockerfile in any
sequence, and nothing they do undoes anything else. A handful of
remedies are not like that. Regenerating ``MANIFEST.sha256`` before
``reproduce.sh`` pins the hash of the script that is about to be
replaced; publishing to Zenodo before attesting puts an attestation in
the archive that does not cover the archived manifest, and Zenodo
deposits are immutable, so that mistake costs a whole new version.

Vaibify has always KNOWN this and never said it. The sequencing was
stated ten times, in prose, inside ``_DICT_L3_REMEDIATION_HINTS``
("push the current envelope, THEN click Verify now") -- a concept the
domain keeps naming that the code had no representation for. This
module is that representation.

WHAT AN EDGE MEANS, AND WHAT IT DOES NOT
----------------------------------------

``(A, B)`` means: **doing B first gets UNDONE by doing A.** Not "A is
more logical first", not "A is cheaper first" -- undone. That is the
only claim worth interrupting a researcher with, and it is the reason
there is no edge from the rebuild attestation to the GitHub mirror:
pushing before attesting costs a second push, which is a nuisance, not
a retraction.

WHY IT ANSWERS ONE ROW AND NOT A PLAN
-------------------------------------

A rendered N-step path has N ways to be subtly wrong and reads
authoritatively while being so. One answer can be wrong in one way,
and the researcher finds out in seconds: they do the thing, nothing
improves. So this returns a single row or nothing at all.

"Nothing at all" is the common case and is INFORMATION, not a
failure -- it says the remaining work can be done in any order. Two
situations produce it, and both are correct:

* No live edge: every remaining blocker is independent.
* More than one root: two independent chains remain, so there is no
  single next step, and pointing at one would imply the other is not
  ready. An arrow pointing confidently at the wrong row is worse than
  no arrow, because it carries more authority than the row it points
  at.

The fork at the end of the ladder resolves itself this way. Once the
attestation is current, the GitHub and Zenodo envelope rows are
genuinely independent, no edge has both ends live, and the arrow
disappears exactly when order stops mattering.
"""

__all__ = [
    "T_LEVEL3_ORDERING_EDGES",
    "fdictDescribeNextOrderedStep",
    "fdictJudgeOrderedRequirements",
]

from vaibify.reproducibility import levelGates
from vaibify.reproducibility.l3Attestation import fbL3AttestationCurrent
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


# ``(sEarlierRow, sLaterRow, sReason)``, keyed by the requirement-row
# names the Project block already renders. The reason is shown to the
# researcher verbatim, so it says what gets undone rather than naming
# the criterion that would go red.
T_LEVEL3_ORDERING_EDGES = (
    (
        "reproduceScript", "manifest",
        "MANIFEST.sha256 pins reproduce.sh, so a manifest written "
        "now would pin the script you are about to replace.",
    ),
    (
        "manifest", "rebuildAttestation",
        "The attestation records the manifest digest it was made "
        "against, so regenerating the manifest stales it.",
    ),
    (
        "manifest", "envelopeMirror",
        "The mirror is compared against the envelope on disk, so a "
        "push now publishes files you are about to regenerate.",
    ),
    (
        "manifest", "envelopeArchive",
        "The archive is compared against the envelope on disk, and "
        "Zenodo versions are immutable -- publishing now would cost "
        "a second version to correct.",
    ),
    (
        "rebuildAttestation", "envelopeArchive",
        "Level 3 asks whether the attestation IN the archive covers "
        "the manifest in the archive, and Zenodo versions are "
        "immutable -- publishing before you attest costs a whole new "
        "version.",
    ),
)


def fdictJudgeOrderedRequirements(dictWorkflow, filesRepo):
    """Return ``{sRowKey: bSatisfied}`` for every row an edge names.

    These verdicts MUST agree with what the rows themselves render,
    or the arrow points at a row the researcher sees as green. The
    agreement is pinned by a test rather than by a shared call,
    because the row payload is assembled in a route module this one
    may not import.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    return {
        "manifest": levelGates.fbVerifyManifestComplete(
            filesRepo, dictWorkflow,
        ),
        "reproduceScript": (
            levelGates.fbVerifyReproduceScript(filesRepo, dictWorkflow)
            and levelGates.fbVerifyReproduceScriptCurrent(
                filesRepo, dictWorkflow,
            )
        ),
        "rebuildAttestation": (
            fbL3AttestationCurrent(filesRepo)
            and levelGates.fbNoArchiveIsKnownSandbox(
                dictWorkflow, filesRepo,
            )
        ),
        "envelopeMirror": levelGates.fbEnvelopeMatchesGithubMirror(
            filesRepo,
        ),
        "envelopeArchive": levelGates.fbEnvelopeMatchesZenodoArchive(
            filesRepo,
        ),
    }


def _flistSelectLiveEdges(dictSatisfied):
    """Return the edges whose BOTH endpoints are still unsatisfied.

    An edge whose earlier row is already satisfied has nothing left to
    order, and one whose later row is satisfied names a step the
    researcher has no reason to take. Only an edge that is live at
    both ends can still be got wrong.
    """
    return [
        tEdge for tEdge in T_LEVEL3_ORDERING_EDGES
        if dictSatisfied.get(tEdge[0]) is False
        and dictSatisfied.get(tEdge[1]) is False
    ]


def fdictDescribeNextOrderedStep(dictWorkflow, filesRepo):
    """Return the one requirement to fix first, or ``None``.

    ``None`` means "order does not matter here", which is the answer
    on most projects most of the time -- see the module docstring for
    why that is information rather than a gap.
    """
    dictSatisfied = fdictJudgeOrderedRequirements(dictWorkflow, filesRepo)
    listLive = _flistSelectLiveEdges(dictSatisfied)
    if not listLive:
        return None
    setLater = {tEdge[1] for tEdge in listLive}
    listRoots = sorted(
        {tEdge[0] for tEdge in listLive} - setLater
    )
    if len(listRoots) != 1:
        return None
    return _fdictBuildStep(listRoots[0], listLive)


def _fdictBuildStep(sRowKey, listLive):
    """Return the wire payload naming one row and why it comes first."""
    listReasons = [
        tEdge[2] for tEdge in listLive if tEdge[0] == sRowKey
    ]
    return {
        "sRowKey": sRowKey,
        "sReason": listReasons[0] if listReasons else "",
        "listBlockedRowKeys": sorted(
            tEdge[1] for tEdge in listLive if tEdge[0] == sRowKey
        ),
    }
