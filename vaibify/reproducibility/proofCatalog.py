"""What Level 2 requires, and what it does not, as data.

PROOF's design rests on each level stating exactly what it requires.
In code that statement is spelled out in several places -- the scalar
gate, the header tuples, the gaps dict, the blocker builders, the PROOF
tab, the glyphs, the docs -- and the copies have drifted apart more
than once. This catalog is the one statement written as data, held to
those copies by ``tests/testProofCatalogAgrees.py``: structural tests
fail when a copy names a criterion, key or row the catalog does not,
and an expected-state matrix drives real fixtures through the gate,
the gaps, the blockers, the header and the receipt together.

The gate does not read the catalog yet; moving it onto the catalog is
recorded debt. What reads it now is the PROOF receipt archived with
every project deposit, through :func:`flistDeriveLevel2Requirements`,
which makes NO new judgment: an entry with an authority takes that
authority's verdict through a fixed table, and every other entry takes
the gate's own gaps key, told apart from "could not tell" by the
criteria the blockers charged.

A leaf module: it imports nothing from vaibify.
"""

__all__ = [
    "DICT_AUTHORITY_TO_STATE",
    "I_PROOF_CATALOG_VERSION",
    "S_APPLIES_ALWAYS",
    "S_APPLIES_ARXIV_RECORDED",
    "S_APPLIES_IMAGE_PINNED",
    "S_APPLIES_OVERLEAF_BOUND",
    "S_AUTHORITY_PUBLIC_ACCESS",
    "S_AUTHORITY_PUBLISHED_COPY",
    "S_STATE_MET",
    "S_STATE_NOT_APPLICABLE",
    "S_STATE_NOT_EVALUATED",
    "S_STATE_NOT_MET",
    "S_STATE_UNKNOWN",
    "S_STATUS_NOT_REQUIRED",
    "S_STATUS_REQUIRED",
    "S_STATUS_REQUIRED_WHEN",
    "T_LEVEL2_CATALOG",
    "fsTranslateAuthorityState",
    "flistDeriveLevel2Requirements",
]


# Bump when an entry's id, status, applicability or criteria change:
# a receipt records the version it was derived under.
I_PROOF_CATALOG_VERSION = 1

S_STATUS_REQUIRED = "required"
S_STATUS_REQUIRED_WHEN = "required-when"
S_STATUS_NOT_REQUIRED = "not-required"

S_APPLIES_ALWAYS = "always"
S_APPLIES_OVERLEAF_BOUND = "overleaf-bound"
S_APPLIES_ARXIV_RECORDED = "arxiv-recorded"
S_APPLIES_IMAGE_PINNED = "image-pinned"

S_AUTHORITY_PUBLIC_ACCESS = "public-access"
S_AUTHORITY_PUBLISHED_COPY = "published-copy"

S_STATE_MET = "met"
S_STATE_NOT_MET = "not-met"
S_STATE_UNKNOWN = "unknown"
S_STATE_NOT_APPLICABLE = "not-applicable"
S_STATE_NOT_EVALUATED = "not-evaluated"

# The ONE translation from an authority's own vocabulary to a
# requirement state. It makes no judgment; an answer outside it raises.
DICT_AUTHORITY_TO_STATE = {
    "public": S_STATE_MET,
    "matched": S_STATE_MET,
    "not-public": S_STATE_NOT_MET,
    "differs": S_STATE_NOT_MET,
    "unknown": S_STATE_UNKNOWN,
}

_DICT_AUTHORITY_GAPS_MAP = {
    S_AUTHORITY_PUBLIC_ACCESS: "dictPublicAccessStates",
    S_AUTHORITY_PUBLISHED_COPY: "dictPublishedCopyStates",
}


def _fdictEntry(
    sId, sStatus, sApplicability, sWhy, sGapsKey="", sProofRowKey="",
    sAuthority="", sService="", tCriteria=(), tUnknownCriteria=(),
):
    """Return one catalog entry in its fixed shape."""
    return {
        "sId": sId, "iLevel": 2, "sStatus": sStatus,
        "sApplicability": sApplicability, "sWhy": sWhy,
        "sGapsKey": sGapsKey, "sProofRowKey": sProofRowKey,
        "sAuthority": sAuthority, "sService": sService,
        "tCriteria": tuple(tCriteria),
        "tUnknownCriteria": tuple(tUnknownCriteria),
    }


T_LEVEL2_CATALOG = (
    _fdictEntry(
        "level-one", S_STATUS_REQUIRED, S_APPLIES_ALWAYS,
        "Level 2 builds on Level 1: published work must first agree "
        "with itself.",
        sGapsKey="bAtLeastLevel1"),
    _fdictEntry(
        "github-copy-matches", S_STATUS_REQUIRED, S_APPLIES_ALWAYS,
        "A reader of the repository must be served the files the "
        "results came from, at a recently verified commit.",
        sGapsKey="bGithubFullySynced", sProofRowKey="bGithubFullySynced",
        sAuthority=S_AUTHORITY_PUBLISHED_COPY, sService="github",
        tCriteria=(("github-verify-stale", "workflow"),
                   ("not-in-github-mirror", "step"),
                   ("not-in-github-mirror", "workflow")),
        tUnknownCriteria=("github-verify-stale",)),
    _fdictEntry(
        "github-repository-public", S_STATUS_REQUIRED, S_APPLIES_ALWAYS,
        "Level 2 asks what a reader can retrieve; a private repository "
        "serves no reader.",
        sGapsKey="bGithubRepositoryPublic",
        sProofRowKey="bGithubRepositoryPublic",
        sAuthority=S_AUTHORITY_PUBLIC_ACCESS, sService="github",
        tCriteria=(("github-repository-not-public", "workflow"),)),
    _fdictEntry(
        "zenodo-copy-matches", S_STATUS_REQUIRED, S_APPLIES_ALWAYS,
        "The permanent archive must hold the files the results came "
        "from, under a DOI.",
        sGapsKey="bZenodoFullySynced", sProofRowKey="bZenodoFullySynced",
        sAuthority=S_AUTHORITY_PUBLISHED_COPY, sService="zenodo",
        tCriteria=(("zenodo-verify-stale", "workflow"),
                   ("not-in-zenodo-deposit", "step"),
                   ("not-in-zenodo-deposit", "workflow")),
        tUnknownCriteria=("zenodo-verify-stale",)),
    _fdictEntry(
        "zenodo-archive-public", S_STATUS_REQUIRED, S_APPLIES_ALWAYS,
        "Every declared Zenodo record must serve its files to a reader "
        "with no login; a restricted record publishes nothing.",
        sGapsKey="bZenodoArchivePublic", sProofRowKey="bZenodoArchivePublic",
        sAuthority=S_AUTHORITY_PUBLIC_ACCESS, sService="zenodo",
        tCriteria=(("zenodo-archive-not-public", "workflow"),)),
    _fdictEntry(
        "unique-basenames", S_STATUS_REQUIRED, S_APPLIES_ALWAYS,
        "A Zenodo deposit is flat, so two published files sharing a "
        "name would overwrite each other.",
        sGapsKey="bNoBasenameCollisions",
        tCriteria=(("basename-collision", "step"),
                   ("basename-collision", "workflow"))),
    _fdictEntry(
        "ai-declaration-attested", S_STATUS_REQUIRED, S_APPLIES_ALWAYS,
        "Publication includes a signed declaration of how AI was used, "
        "current with every file it covers.",
        sGapsKey="bAiDeclarationAttested",
        tCriteria=(("missing-ai-declaration-step", "workflow"),
                   ("ai-declaration-unattested", "step"),
                   ("ai-declaration-stale", "step"),
                   ("ai-declaration-uncheckable", "step")),
        tUnknownCriteria=("ai-declaration-uncheckable",)),
    _fdictEntry(
        "ai-models-declared", S_STATUS_REQUIRED, S_APPLIES_ALWAYS,
        "Every AI model used on the project is declared with its vendor, "
        "model ID and dates of use.",
        sGapsKey="bAiModelsDeclared", sProofRowKey="bAiModelsDeclared",
        tCriteria=(("ai-models-undeclared", "workflow"),)),
    _fdictEntry(
        "personal-layer-answered", S_STATUS_REQUIRED, S_APPLIES_ALWAYS,
        "The researcher has answered whether a private agent "
        "configuration governed the work; disclosure is never required.",
        sGapsKey="bPersonalLayerDeclared",
        sProofRowKey="bPersonalLayerDeclared",
        tCriteria=(("personal-layer-unanswered", "workflow"),)),
    _fdictEntry(
        "image-archive-answered", S_STATUS_REQUIRED_WHEN,
        S_APPLIES_IMAGE_PINNED,
        "Once the environment pins a container image, the researcher "
        "decides whether to archive it; any answer meets this.",
        sGapsKey="bImageArchiveAnswered",
        sProofRowKey="bImageArchiveAnswered",
        tCriteria=(("image-archive-unanswered", "workflow"),)),
    _fdictEntry(
        "overleaf-figures-match", S_STATUS_REQUIRED_WHEN,
        S_APPLIES_OVERLEAF_BOUND,
        "Binding an Overleaf project claims its figures are the "
        "project's; unbound, there is no claim to check.",
        sGapsKey="bOverleafFullySynced",
        tCriteria=(("overleaf-verify-stale", "workflow"),
                   ("not-in-overleaf", "workflow"),
                   ("figure-not-frozen", "step")),
        tUnknownCriteria=("overleaf-verify-stale",)),
    _fdictEntry(
        "arxiv-matches", S_STATUS_REQUIRED_WHEN, S_APPLIES_ARXIV_RECORDED,
        "Recording an arXiv submission claims the e-print matches; "
        "without one there is no claim to check.",
        sGapsKey="bArxivFullySynced", sProofRowKey="bArxivFullySynced",
        tCriteria=(("arxiv-mismatch", "workflow"),
                   ("arxiv-version-stale", "workflow")),
        tUnknownCriteria=("arxiv-version-stale",)),
    # Its optional row is drawn in the PROOF tab's Level 1 section,
    # though its key rides in the Level 2 readiness dict.
    _fdictEntry(
        "project-context-file", S_STATUS_NOT_REQUIRED, S_APPLIES_ALWAYS,
        "A project context file helps agents and readers, and never "
        "blocks a level.",
        sGapsKey="bProjectContextFileExists"),
    _fdictEntry(
        "prompt-record", S_STATUS_NOT_REQUIRED, S_APPLIES_ALWAYS,
        "The Prompt Record is the researcher's choice and never blocks "
        "a level.",
        sGapsKey="bPromptRecordCurrent", sProofRowKey="bPromptRecordCurrent"),
    _fdictEntry(
        "supervised-mode", S_STATUS_NOT_REQUIRED, S_APPLIES_ALWAYS,
        "Supervised mode is the researcher's choice and never blocks a "
        "level.",
        sGapsKey="bSupervisionClean", sProofRowKey="bSupervisionClean"),
    _fdictEntry(
        "compared-not-gating-files", S_STATUS_NOT_REQUIRED, S_APPLIES_ALWAYS,
        "l3_attestation.json and ai_provenance.json are compared with "
        "every published copy so their badges are truthful, and gate "
        "nothing at Level 2."),
    _fdictEntry(
        "committed-uncompared-files", S_STATUS_NOT_REQUIRED,
        S_APPLIES_ALWAYS,
        "Test markers, .gitignore, outputProvenance.json and this receipt "
        "are committed and published but never compared."),
    _fdictEntry(
        "reproducibility-envelope", S_STATUS_NOT_REQUIRED, S_APPLIES_ALWAYS,
        "The reproducibility envelope gates Level 3 only; Level 2 asks "
        "what was published, not how to rerun it."),
)


def fsTranslateAuthorityState(sAnswer):
    """Return the requirement state for one authority answer, or raise."""
    if sAnswer not in DICT_AUTHORITY_TO_STATE:
        raise ValueError(
            "an authority answered " + repr(sAnswer) + ", which the "
            "Level 2 catalog has no requirement state for")
    return DICT_AUTHORITY_TO_STATE[sAnswer]


def flistDeriveLevel2Requirements(dictGaps, listBlockers, dictApplicability):
    """Return every catalog entry's state, read from the gate's own answers.

    ``dictGaps`` is ``levelGates.fdictLevel2Gaps``'s dict, carrying the
    authorities' states; ``listBlockers`` is ``flistLevel2Blockers``'s
    list from the same observation; ``dictApplicability`` maps each
    applicability to whether it holds for this project.
    """
    return [
        _fdictDeriveOneRequirement(
            dictEntry, dictGaps, listBlockers, dictApplicability)
        for dictEntry in T_LEVEL2_CATALOG
    ]


def _fdictDeriveOneRequirement(
    dictEntry, dictGaps, listBlockers, dictApplicability,
):
    """Return one entry's archived row: its state, evidence and reason."""
    bApplies = bool(dictApplicability.get(dictEntry["sApplicability"]))
    sState, sEvidence = _ftStateAndEvidence(
        dictEntry, bApplies, dictGaps, listBlockers)
    return {
        "iLevel": dictEntry["iLevel"], "sId": dictEntry["sId"],
        "sStatus": dictEntry["sStatus"],
        "sApplicability": dictEntry["sApplicability"],
        "bApplies": bApplies, "sState": sState, "sEvidence": sEvidence,
        "sWhy": dictEntry["sWhy"],
    }


def _ftStateAndEvidence(dictEntry, bApplies, dictGaps, listBlockers):
    """Return ``(sState, sEvidence)`` for one entry, by the fixed rules."""
    if dictEntry["sStatus"] == S_STATUS_NOT_REQUIRED:
        return S_STATE_NOT_EVALUATED, ""
    if not bApplies:
        return S_STATE_NOT_APPLICABLE, ""
    if dictEntry["sAuthority"]:
        dictAnswer = dictGaps[_DICT_AUTHORITY_GAPS_MAP[dictEntry[
            "sAuthority"]]][dictEntry["sService"]]
        return (fsTranslateAuthorityState(dictAnswer.get("sState")),
                str(dictAnswer.get("sReason") or ""))
    listCharged = _flistChargedBlockers(dictEntry, listBlockers)
    if dictGaps[dictEntry["sGapsKey"]]:
        return S_STATE_MET, ""
    sEvidence = " ".join(_flistUniqueHints(listCharged))
    setCharged = {dictBlocker.get("sCriterion") for dictBlocker in listCharged}
    if setCharged & set(dictEntry["tUnknownCriteria"]):
        return S_STATE_UNKNOWN, sEvidence
    return S_STATE_NOT_MET, sEvidence


def _flistChargedBlockers(dictEntry, listBlockers):
    """Return the blockers that charge this entry, criterion and scope both."""
    setCharging = set(dictEntry["tCriteria"])
    return [
        dictBlocker for dictBlocker in listBlockers or []
        if (dictBlocker.get("sCriterion"), dictBlocker.get("sScope"))
        in setCharging
    ]


def _flistUniqueHints(listBlockers):
    """Return each charged blocker's remediation hint once, in order."""
    listHints = []
    for dictBlocker in listBlockers:
        sHint = str(dictBlocker.get("sRemediationHint") or "")
        if sHint and sHint not in listHints:
            listHints.append(sHint)
    return listHints
