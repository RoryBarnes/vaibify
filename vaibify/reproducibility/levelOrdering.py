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

``(A, B)`` means: **doing B first is work you will do again.**
Almost always because doing A UNDOES it -- regenerating the manifest
stales the attestation keyed to it, and an immutable Zenodo version
published before the attestation cannot be corrected without minting
another. That is the claim worth interrupting a researcher with, and
it is why there is no edge from the rebuild attestation to the GitHub
mirror: pushing before attesting costs a second push, which is a
nuisance, not a retraction.

One edge is weaker and says so in its own reason: generating
``reproduce.sh`` re-pins the manifest in the same action, so a manifest
regenerated first is not undone, merely repeated. It stays because the
researcher still wants the cheaper order (found live, 2026-09-15,
where following it turned two clicks into one) -- but a reason that
claimed an undo there would have been false, and the arrow's whole
value is that its claims hold.

THE TWO ARCHIVES ARE TWO NODES
------------------------------

``environmentArchive`` joined on 2026-09-15, and the reason is an
undo: depositing the environment image re-pins ``MANIFEST.sha256`` in
the same action that writes ``environment.json``, so it orders
everything the manifest does -- deposit after the rerun and the rerun
is wasted.

Judging it required splitting a COMBINED criterion.
``fbNoArchiveIsKnownSandbox`` classifies BOTH deposits, so a node that
read it would light the Environment row over a sandbox PROJECT deposit
-- sending the researcher to a Make Permanent button that promotes the
wrong archive. Each node reads its own per-archive verdict instead,
and the ``envelopeArchive`` node carries the three conjuncts that
share one remedy: publish a Zenodo version holding the envelope and
the attestation together. Only an ASYMMETRIC pair of deposits can tell
that design from the broken one, which is how the tests are written.

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

from vaibify.reproducibility import (
    archivePermanence, levelGates, lockSatisfaction,
)
from vaibify.reproducibility.l3Attestation import fbL3AttestationCurrent
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


# ``(sEarlierRow, sLaterRow, sReason)``, keyed by the requirement-row
# names the Project block already renders. The reason is shown to the
# researcher verbatim, so it says what gets undone rather than naming
# the criterion that would go red.
T_LEVEL3_ORDERING_EDGES = (
    # The one edge that is NOT an undo. Kept, and marked, because the
    # ordering is still real and the researcher still wants it -- but
    # calling it an undo would have been false: regenerating the
    # manifest first costs a wasted click, not lost work.
    (
        "reproduceScript", "manifest",
        "Generating reproduce.sh re-pins MANIFEST.sha256 in the same "
        "action, so doing the script first settles both. Regenerating "
        "the manifest now only means doing it twice.",
    ),
    # The lock comes before everything the rerun touches. A lock the
    # pinned image does not satisfy makes the shadow REFUSE, so an
    # attestation attempted first is a verification spent to be told
    # this -- which is how the gap was found (2026-09-15). Regenerating
    # the envelope also rewrites the manifest, so a push or an archive
    # made first publishes files about to change.
    (
        "dependencyLock", "rebuildAttestation",
        "The container you are working in does not satisfy "
        "requirements.lock, and it is the image your envelope pins, "
        "so the rerun will refuse before it starts. Regenerate the "
        "envelope first \u2014 it rewrites the lock from what the "
        "image actually has. Rebuilding the image instead also "
        "settles it, at the cost of downgrading the image to match "
        "the older lock.",
    ),
    (
        "dependencyLock", "envelopeMirror",
        "Regenerating the envelope rewrites requirements.lock and "
        "the manifest, so a push now publishes files you are about "
        "to change.",
    ),
    (
        "dependencyLock", "envelopeArchive",
        "Regenerating the envelope rewrites requirements.lock and "
        "the manifest, and Zenodo versions are immutable \u2014 "
        "publishing now would cost a second version to correct.",
    ),
    # The environment archive re-pins MANIFEST.sha256 in the same
    # action that writes environment.json, so it sits where the
    # manifest does: a rerun attested before the deposit is a rerun
    # keyed to a digest the deposit is about to change, and a push or
    # a Zenodo version published first publishes files about to move.
    (
        "environmentArchive", "rebuildAttestation",
        "Depositing the environment image re-pins MANIFEST.sha256 in "
        "the same action, so an attestation made first is stale "
        "before you read it.",
    ),
    (
        "environmentArchive", "envelopeMirror",
        "Depositing the environment image rewrites environment.json "
        "and re-pins the manifest, so a push now publishes files you "
        "are about to change.",
    ),
    (
        "environmentArchive", "envelopeArchive",
        "Depositing the environment image rewrites environment.json "
        "and re-pins the manifest, and Zenodo versions are immutable "
        "\u2014 publishing now would cost a second version to "
        "correct.",
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


def fdictJudgeOrderedRequirements(
    dictWorkflow, filesRepo, dictLockSatisfaction=None,
    dictImageCurrency=None,
):
    """Return ``{sRowKey: bSatisfied}`` for every row an edge names.

    These verdicts MUST agree with what the rows themselves render,
    or the arrow points at a row the researcher sees as green. The
    agreement is pinned by a test rather than by a shared call,
    because the row payload is assembled in a route module this one
    may not import.

    The agreement is TOTAL. ``dependencyLock`` was briefly a named
    exception -- the row kept its state on a mismatch and warned in an
    amber note, on the reasoning that the row speaks for the
    repository's envelope while the container is a different question.
    Seen on a live project that read as nonsense: every applicable
    level showing a check, this arrow pointing at that row, and a note
    underneath saying a rerun would refuse. The researcher reversed it
    the same day -- a row nothing can be done about is not green -- so
    the row now carries this conjunct too and resolves to PARTIAL,
    which the artifact vocabulary already had.
    ``test_no_row_diverges_from_the_arrow_at_all`` states the
    invariant with no carve-out left in it.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictPermanence = levelGates.fdictArchivePermanenceState(
        dictWorkflow, filesRepo,
    )
    return {
        # UNKNOWN counts as satisfied, and so does a mismatch the
        # running container is no evidence for. ONE truth table, in
        # lockSatisfaction, shared with the verification pre-flight:
        # two surfaces answering one question from two derivations is
        # the duplication that module was extracted to end.
        "dependencyLock": (
            levelGates.fbVerifyDependencyLock(filesRepo)
            and not lockSatisfaction.fbLockBlocksVerification(
                dictLockSatisfaction, dictImageCurrency,
            )
        ),
        "manifest": levelGates.fbVerifyManifestComplete(
            filesRepo, dictWorkflow,
        ),
        "reproduceScript": (
            levelGates.fbVerifyReproduceScript(filesRepo, dictWorkflow)
            and levelGates.fbVerifyReproduceScriptCurrent(
                filesRepo, dictWorkflow,
            )
        ),
        # PER-ARCHIVE, never the combined sandbox gate. That gate
        # classifies two deposits, so judging this node from it would
        # let a sandbox PROJECT deposit light the ENVIRONMENT row --
        # whose Make Permanent button promotes the wrong archive.
        # Unsatisfied exactly when Make Permanent (or Deposit) on the
        # Environment archive row is the right button.
        "environmentArchive": (
            levelGates.fbImageArchiveDeposited(filesRepo)
            and not _fbIsSandbox(
                dictPermanence["sImageArchivePermanence"],
            )
        ),
        # A CURRENT attestation, and nothing else. The combined
        # sandbox gate used to hang here, where the row carries
        # neither remedy: the buttons that fix a sandbox deposit live
        # on the two archive rows.
        "rebuildAttestation": fbL3AttestationCurrent(filesRepo),
        "envelopeMirror": levelGates.fbEnvelopeMatchesGithubMirror(
            filesRepo,
        ),
        # All three conjuncts have ONE remedy -- publish a Zenodo
        # version carrying the envelope and the attestation together
        # -- which is what makes them one node. Judged on the envelope
        # alone, this node went satisfied while Level 3 failed on the
        # archived attestation, and the arrow fell silent at exactly
        # the moment it was needed.
        "envelopeArchive": (
            levelGates.fbEnvelopeMatchesZenodoArchive(filesRepo)
            and levelGates.fbAttestationIsPubliclyArchived(filesRepo)
            and not _fbIsSandbox(
                dictPermanence["sProjectArchivePermanence"],
            )
        ),
    }


def _fbIsSandbox(sPermanence):
    """Return True only for a deposit KNOWN to be a sandbox one.

    Spelled as "is not permanent"'s opposite on purpose: ``unknown``
    must keep passing. The permanence gate fails open in the
    researcher's favour by design, and an arrow that pointed at every
    deposit vaibify cannot classify would be making the claim the gate
    refuses to make.
    """
    return sPermanence == archivePermanence.S_PERMANENCE_SANDBOX


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


def fdictDescribeNextOrderedStep(
    dictWorkflow, filesRepo, dictLockSatisfaction=None,
    dictImageCurrency=None,
):
    """Return the one requirement to fix first, or ``None``.

    ``None`` means "order does not matter here", which is the answer
    on most projects most of the time -- see the module docstring for
    why that is information rather than a gap.
    """
    dictSatisfied = fdictJudgeOrderedRequirements(
        dictWorkflow, filesRepo, dictLockSatisfaction, dictImageCurrency,
    )
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
