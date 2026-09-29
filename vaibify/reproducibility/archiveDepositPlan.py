"""Where an environment deposit can go, and which way vaibify recommends.

The Environment archive row used to offer one button, "Deposit this
image in Zenodo", while the deposit route read the project's Zenodo
setting to decide where the bytes went. Nothing on the screen said
sandbox or permanent, nor whether the upload would continue the
record this project had already deposited or start an unrelated one
-- and sandbox and production cannot version each other, so a project
whose earlier deposit was permanent but whose setting said sandbox
would silently start a fresh sandbox record.

This module is the one authority on those questions. The poll ships
its plan, the row renders every offered choice from it, and the route
resolves the researcher's choice through :func:`ftResolveDepositChoice`
-- so the destination the row names is the destination the upload
uses. Whether a choice continues a record is answered by
:func:`imageDeposit.fiParentDepositIdOnService`, the function the
upload itself calls, never re-derived here.
"""

__all__ = [
    "LIST_DEPOSIT_CHOICES",
    "S_CHOICE_NEW_RECORD_PERMANENT",
    "S_CHOICE_NEW_RECORD_SANDBOX",
    "S_CHOICE_NEW_VERSION",
    "fdictBuildDepositPlan",
    "fdictDescribePreviousDeposit",
    "ftResolveDepositChoice",
]

from vaibify.reproducibility import archivePermanence
from vaibify.reproducibility.environmentSnapshot import fdictArchiveLineageOf


S_CHOICE_NEW_VERSION = "new-version"
S_CHOICE_NEW_RECORD_PERMANENT = "new-record-permanent"
S_CHOICE_NEW_RECORD_SANDBOX = "new-record-sandbox"

LIST_DEPOSIT_CHOICES = [
    S_CHOICE_NEW_VERSION,
    S_CHOICE_NEW_RECORD_PERMANENT,
    S_CHOICE_NEW_RECORD_SANDBOX,
]

_DICT_SERVICE_BY_PERMANENCE = {
    archivePermanence.S_PERMANENCE_PERMANENT: "zenodo",
    archivePermanence.S_PERMANENCE_SANDBOX: "sandbox",
}

_DICT_NEW_RECORD_SERVICE = {
    S_CHOICE_NEW_RECORD_PERMANENT: "zenodo",
    S_CHOICE_NEW_RECORD_SANDBOX: "sandbox",
}


def fdictDescribePreviousDeposit(dictContainer):
    """Return the deposit this environment was last archived under, or None.

    The current record when the envelope still carries one, else the
    lineage note a regeneration left behind when the image changed.
    ``sZenodoService`` is empty when vaibify cannot say which Zenodo
    holds it, and ``bCanContinue`` is true only when a new version of
    it can actually be drafted there.
    """
    from vaibify.reproducibility.imageDeposit import (
        fiParentDepositIdOnService,
    )
    dictLineage = fdictArchiveLineageOf(dictContainer)
    if not dictLineage:
        return None
    sPermanence = archivePermanence.fsClassifyDeposit(
        dictLineage["sZenodoService"], dictLineage["sVersionDoi"],
    )
    sService = _DICT_SERVICE_BY_PERMANENCE.get(sPermanence, "")
    return {
        "sVersionDoi": dictLineage["sVersionDoi"],
        "sConceptDoi": dictLineage["sConceptDoi"],
        "sZenodoService": sService,
        "sPermanence": sPermanence,
        "bCanContinue": bool(sService) and fiParentDepositIdOnService(
            dictLineage, sService,
        ) > 0,
    }


def _flistOfferedChoices(dictPrevious):
    """Return the choices a deposit can take, each with its service."""
    listOffered = []
    if dictPrevious and dictPrevious["bCanContinue"]:
        listOffered.append({
            "sChoice": S_CHOICE_NEW_VERSION,
            "sZenodoService": dictPrevious["sZenodoService"],
        })
    for sChoice, sService in _DICT_NEW_RECORD_SERVICE.items():
        listOffered.append({"sChoice": sChoice, "sZenodoService": sService})
    return listOffered


def _fsRecommendChoice(dictPrevious, bCovered):
    """Return the choice vaibify recommends, or "" when none is needed.

    A permanent record is continued, so one project's environments
    stay one citable lineage. Anything else is recommended a new
    permanent record: Level 3 requires a permanent archive, and a
    sandbox record cannot be versioned onto zenodo.org.
    """
    if bCovered:
        return ""
    if dictPrevious and dictPrevious["bCanContinue"] and (
        dictPrevious["sPermanence"]
        == archivePermanence.S_PERMANENCE_PERMANENT
    ):
        return S_CHOICE_NEW_VERSION
    return S_CHOICE_NEW_RECORD_PERMANENT


def fdictBuildDepositPlan(dictContainer, bCovered):
    """Return the row's plan: previous deposit, choices, recommendation.

    ``bCovered`` is true when a deposit on record already covers the
    image the envelope pins; nothing is recommended then, because a
    further deposit would duplicate one that exists.
    """
    dictPrevious = fdictDescribePreviousDeposit(dictContainer)
    return {
        "dictPreviousDeposit": dictPrevious,
        "listChoices": _flistOfferedChoices(dictPrevious),
        "sRecommendedChoice": _fsRecommendChoice(dictPrevious, bCovered),
    }


def ftResolveDepositChoice(dictContainer, sChoice):
    """Return ``(sZenodoService, dictParentArchive)`` for an offered choice.

    Only a new version passes the previous record as the parent; a new
    record passes none, so it starts a fresh record even on the Zenodo
    that holds the previous one. Raises ``ValueError`` naming the
    choices on offer when ``sChoice`` is not one of them.
    """
    dictPrevious = fdictDescribePreviousDeposit(dictContainer)
    listOffered = _flistOfferedChoices(dictPrevious)
    for dictOffered in listOffered:
        if dictOffered["sChoice"] != sChoice:
            continue
        dictParent = (
            fdictArchiveLineageOf(dictContainer)
            if sChoice == S_CHOICE_NEW_VERSION else {}
        )
        return dictOffered["sZenodoService"], dictParent
    raise ValueError(
        f"{sChoice!r} is not a deposit this project can make. Choose "
        "one of: " + ", ".join(
            dictOffered["sChoice"] for dictOffered in listOffered
        ) + "."
    )
