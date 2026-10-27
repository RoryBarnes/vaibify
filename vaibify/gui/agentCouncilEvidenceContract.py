"""The evidence claim contract a council participant writes against.

Clause 3 of the charter asks every participant to tag its claims; this
module says, field by field, what a tagged claim may look like. It is
stated twice, deliberately, and held to agree: as the validator the
engine runs on every turn (:func:`flistFindEvidenceClaimProblems`),
which is Claude's only check, and as the strict JSON Schema handed to
Codex and Antigravity (:func:`fdictComposeEvidenceOutputSchema`), which
their own CLIs enforce. ``tests/testCouncilEvidenceContract.py`` runs one
table of claims through both.

It lives apart from ``agentCouncilCharter`` because it changes for a
different reason: the charter's text changes when the protocol does,
and this contract changes when evidence policy does (which forms can be
confirmed, what replay needs, what the ledger records). The charter
renders it into the turn schema and validates results with it.

There are four forms. An unconfirmed claim carries only its status. A
baseline claim names a command vaibify re-runs on the sealed snapshot.
A modified-state claim adds the changes it made, which vaibify applies
to a fresh copy before re-running the command. A web source names the
links a provider's search returned and is never confirmed. No form
carries provenance numbers: the ledger records what vaibify observed.
"""

import re

__all__ = [
    "DICT_CHANGE_MANIFEST_ENTRY_FIELDS",
    "DICT_EVIDENCE_CLAIM_TEMPLATE",
    "DICT_EVIDENCE_FIELDS_BY_FORM",
    "I_MAXIMUM_SOURCE_LINKS",
    "S_EVIDENCE_KEY",
    "S_EVIDENCE_STATE_BASELINE",
    "S_EVIDENCE_STATE_MODIFIED",
    "S_EVIDENCE_STATE_WEB_SOURCE",
    "S_EVIDENCE_STATUS_CONFIRMED",
    "S_FILE_MODE_PATTERN",
    "S_NON_BLANK_PATTERN",
    "S_SEARCH_QUERY_PATTERN",
    "S_SOURCE_LINK_PATTERN",
    "TUPLE_EVIDENCE_BASE_FIELDS",
    "TUPLE_EVIDENCE_CLAIM_STATUSES",
    "TUPLE_EVIDENCE_MODIFIED_STATE_FIELDS",
    "TUPLE_EVIDENCE_STATE_FORMS",
    "TUPLE_EVIDENCE_UNCONFIRMED_STATUSES",
    "TUPLE_EVIDENCE_WEB_SOURCE_FIELDS",
    "TUPLE_EVIDENCE_WEB_SOURCE_STATUSES",
    "fdictComposeEvidenceOutputSchema",
    "flistFindEvidenceClaimProblems",
    "fsComposeEvidenceFormsText",
]


# The evidence claim shape the engine actually consumes
# (agentCouncilEvidence.EvidenceDisciplineMixin). A model told
# "array of strings" here returns strings, and every one of them is
# silently skipped by `if not isinstance(dictClaim, dict): continue` —
# the claim never reaches the ledger and nothing reports the loss.
# The fake evidence tests manufacture dictionaries directly, so no
# fake lane could have surfaced it.
S_EVIDENCE_KEY = "listEvidence"
# Spelled here rather than imported from agentCouncilCampaign, which
# imports the charter and through it this module.
# tests/testCouncilRunnerAccess.py::
# testTheEvidenceVocabularyMatchesTheEngine pins them together.
S_EVIDENCE_STATUS_CONFIRMED = "confirmed"
S_EVIDENCE_STATE_BASELINE = "baseline"
S_EVIDENCE_STATE_MODIFIED = "modifiedState"
S_EVIDENCE_STATE_WEB_SOURCE = "webSource"
TUPLE_EVIDENCE_CLAIM_STATUSES = (
    "confirmed", "supportedBySourceInspection", "asserted",
    "blockedForWantOfEvidence",
)
TUPLE_EVIDENCE_UNCONFIRMED_STATUSES = TUPLE_EVIDENCE_CLAIM_STATUSES[1:]
# Nothing from the web is ever confirmed: vaibify cannot re-run a page,
# so a claim resting on one is at best supported by reading it.
TUPLE_EVIDENCE_WEB_SOURCE_STATUSES = (
    "supportedBySourceInspection", "asserted")
TUPLE_EVIDENCE_STATE_FORMS = (
    S_EVIDENCE_STATE_BASELINE, S_EVIDENCE_STATE_MODIFIED,
    S_EVIDENCE_STATE_WEB_SOURCE,
)
# The claim fields EVERY entry carries. Deliberately only what the
# evidence engine CONSUMES: an earlier draft of this contract also
# required an 'sClaimText', which production reads nowhere — asking a
# model for a field that goes nowhere spends its tokens and fails its
# turns for nothing. Requiring exactly the consumed set is what makes
# "exact" true in both directions.
TUPLE_EVIDENCE_BASE_FIELDS = ("sStatus",)
# What a participant supplies beyond the command. A BASELINE claim is
# re-run by the engine against the sealed snapshot, so the model gives
# only the command. A MODIFIED-STATE claim adds the changes it made, and
# vaibify re-runs it on a fresh copy with those changes applied. Neither
# carries provenance numbers (snapshot, image, exit code, output
# digest): the ledger records what vaibify observed, never what a
# participant reported, and an entry that offers them is refused.
TUPLE_EVIDENCE_MODIFIED_STATE_FIELDS = ("dictChangeManifest",)
TUPLE_EVIDENCE_WEB_SOURCE_FIELDS = ("listSourceLinks", "sSearchQuery")
# The exact field set of each form, keyed by its 'sStateForm' ("" for an
# unconfirmed entry, which names no form). The validator refuses any
# other field and the strict schema forbids one, so the two agree.
DICT_EVIDENCE_FIELDS_BY_FORM = {
    "": TUPLE_EVIDENCE_BASE_FIELDS,
    S_EVIDENCE_STATE_BASELINE: ("sStatus", "sStateForm", "sCommandText"),
    S_EVIDENCE_STATE_MODIFIED: (
        ("sStatus", "sStateForm", "sCommandText")
        + TUPLE_EVIDENCE_MODIFIED_STATE_FIELDS),
    S_EVIDENCE_STATE_WEB_SOURCE: (
        ("sStatus", "sStateForm") + TUPLE_EVIDENCE_WEB_SOURCE_FIELDS),
}
# A participant's change manifest, in a shape a strict structured-output
# schema can express: arrays of fixed-key objects, never maps keyed by
# path. The ledger keeps its own dictionary form
# (agentCouncilStore.LIST_CHANGE_MANIFEST_KEYS); replay translates.
# None marks the one array of bare paths.
DICT_CHANGE_MANIFEST_ENTRY_FIELDS = {
    "listModifiedFiles": ("sPath", "sContent"),
    "listDeletedPaths": None,
    "listChangedFileModes": ("sPath", "sMode"),
    "listSymlinkTargets": ("sPath", "sTarget"),
}
# Bounds written as patterns the strict schema carries verbatim and the
# validator applies the same way, so neither can pass what the other
# refuses. Two dialect differences are written out of them. Whitespace
# is an explicit ASCII class, never \s or str.strip(), whose sets differ
# between Python and the ECMA-262 dialect a JSON Schema pattern is
# written in. And a schema's `$` is the end of the text, where Python's
# also matches before a final newline, so the validator applies an
# anchored pattern with re.fullmatch.
S_NON_BLANK_PATTERN = r"[^ \t\n\r\f\v]"
I_MAXIMUM_SOURCE_LINKS = 10
S_SOURCE_LINK_PATTERN = r"^https://[^ \t\n\r\f\v]{1,2040}$"
S_SEARCH_QUERY_PATTERN = r"^[^\r\n]{1,500}$"
S_FILE_MODE_PATTERN = "^[0-7]{3,4}$"
DICT_EVIDENCE_CLAIM_TEMPLATE = {
    "sStatus": "<one of " + "|".join(TUPLE_EVIDENCE_CLAIM_STATUSES) + ">",
    "sStateForm": "<absent when unconfirmed; otherwise one of "
                  + "|".join(TUPLE_EVIDENCE_STATE_FORMS) + ">",
    "sCommandText": "<baseline and modifiedState: the exact command>",
    "dictChangeManifest": {
        "listModifiedFiles": [{
            "sPath": "<repository-relative path>",
            "sContent": "<the file's complete new text>"}],
        "listDeletedPaths": ["<repository-relative path>"],
        "listChangedFileModes": [{
            "sPath": "<repository-relative path>",
            "sMode": "<octal mode, such as 755>"}],
        "listSymlinkTargets": [{
            "sPath": "<repository-relative path>",
            "sTarget": "<the link target exactly as written>"}],
    },
    "listSourceLinks": ["<webSource: each https link exactly as your "
                        "search returned it>"],
    "sSearchQuery": "<webSource: the query you searched for>",
}

def flistFindEvidenceClaimProblems(iIndex, jsonEntry):
    """Validate ONE evidence claim against its discriminated shape.

    `isinstance(entry, dict)` alone accepted ``{}``, an invented
    status, and a modifiedState confirmation with no provenance — all
    of which the evidence engine then drops or records with empty
    fields, silently. A claim the ledger cannot use is a claim the
    model should be told to fix on its one repair attempt.

    This is the same contract the strict schema states
    (:func:`fdictComposeEvidenceOutputSchema`), field for field. Claude
    has no strict schema, so this is its only check; an extra field the
    schema forbids must be refused here too, or a Claude participant
    could hand the ledger provenance numbers no other provider can.
    """
    sWhere = f"'{S_EVIDENCE_KEY}' entry {iIndex}"
    if not isinstance(jsonEntry, dict):
        return [f"{sWhere} must be an object with "
                f"{list(TUPLE_EVIDENCE_BASE_FIELDS)}, not "
                f"{type(jsonEntry).__name__}"]
    listProblems = [
        f"{sWhere} needs a non-empty '{sField}'"
        for sField in TUPLE_EVIDENCE_BASE_FIELDS
        if not _fbIsNonEmptyString(jsonEntry.get(sField))]
    sStatus = jsonEntry.get("sStatus")
    if isinstance(sStatus, str) and sStatus not in (
            TUPLE_EVIDENCE_CLAIM_STATUSES):
        listProblems.append(
            f"{sWhere} has status {sStatus!r}, which is not one of "
            f"{list(TUPLE_EVIDENCE_CLAIM_STATUSES)}; the engine ignores "
            "a status it does not know, so the claim would vanish")
    sStateForm = jsonEntry.get("sStateForm", "")
    if not isinstance(sStateForm, str) or (
            sStateForm not in DICT_EVIDENCE_FIELDS_BY_FORM):
        listProblems.append(
            f"{sWhere} has 'sStateForm' {sStateForm!r}, which is not one "
            f"of {list(TUPLE_EVIDENCE_STATE_FORMS)}; leave it out for an "
            "unconfirmed claim")
        return listProblems
    listUnexpected = sorted(
        set(jsonEntry) - set(DICT_EVIDENCE_FIELDS_BY_FORM[sStateForm]))
    if listUnexpected:
        listProblems.append(
            f"{sWhere} carries fields its form does not have: "
            f"{listUnexpected}. A claim never reports a snapshot, image, "
            "exit code or output digest; vaibify records what it observed "
            "itself")
    listProblems.extend(_flistFindStatusProblemsForForm(
        sWhere, sStatus, sStateForm))
    if sStateForm in (S_EVIDENCE_STATE_BASELINE, S_EVIDENCE_STATE_MODIFIED):
        if not _fbIsNonEmptyString(jsonEntry.get("sCommandText")):
            listProblems.append(
                f"{sWhere} is confirmed, so it needs the 'sCommandText' "
                "its confirmation rests on")
    if sStateForm == S_EVIDENCE_STATE_MODIFIED:
        listProblems.extend(_flistFindChangeManifestProblems(
            sWhere, jsonEntry.get("dictChangeManifest")))
    if sStateForm == S_EVIDENCE_STATE_WEB_SOURCE:
        listProblems.extend(_flistFindWebSourceProblems(sWhere, jsonEntry))
    return listProblems


def _fbIsNonEmptyString(jsonValue):
    return isinstance(jsonValue, str) and bool(
        re.search(S_NON_BLANK_PATTERN, jsonValue))


def _fbMatchesAnchoredPattern(sPattern, jsonValue):
    return isinstance(jsonValue, str) and bool(
        re.fullmatch(sPattern, jsonValue))


def _flistFindStatusProblemsForForm(sWhere, sStatus, sStateForm):
    """Report a status the claim's form cannot carry.

    An unconfirmed claim names no form, a baseline or modified-state
    claim is by definition confirmed, and a web source never is.
    """
    if sStateForm == "" and sStatus == S_EVIDENCE_STATUS_CONFIRMED:
        return [f"{sWhere} is confirmed, so 'sStateForm' must name what "
                f"was run: {S_EVIDENCE_STATE_BASELINE!r} or "
                f"{S_EVIDENCE_STATE_MODIFIED!r}"]
    if sStateForm in (S_EVIDENCE_STATE_BASELINE, S_EVIDENCE_STATE_MODIFIED) \
            and sStatus != S_EVIDENCE_STATUS_CONFIRMED:
        return [f"{sWhere} names the {sStateForm!r} form, which only a "
                "confirmed claim has; leave 'sStateForm' out otherwise"]
    if sStateForm == S_EVIDENCE_STATE_WEB_SOURCE and sStatus not in (
            TUPLE_EVIDENCE_WEB_SOURCE_STATUSES):
        return [f"{sWhere} is a web source, whose status is one of "
                f"{list(TUPLE_EVIDENCE_WEB_SOURCE_STATUSES)}: nothing "
                "from the web is ever confirmed"]
    return []


def _flistFindChangeManifestProblems(sWhere, jsonManifest):
    """Report a change manifest that could not rebuild the tested state.

    Every key is required, an empty array included, because a strict
    schema requires every property: "no deletions" is ``[]``.
    """
    sManifestWhere = f"{sWhere} 'dictChangeManifest'"
    if not isinstance(jsonManifest, dict):
        return [f"{sManifestWhere} must be an object with "
                f"{list(DICT_CHANGE_MANIFEST_ENTRY_FIELDS)}"]
    listProblems = []
    listUnexpected = sorted(
        set(jsonManifest) - set(DICT_CHANGE_MANIFEST_ENTRY_FIELDS))
    if listUnexpected:
        listProblems.append(
            f"{sManifestWhere} has unknown keys: {listUnexpected}")
    for sKey, tEntryFields in DICT_CHANGE_MANIFEST_ENTRY_FIELDS.items():
        jsonEntries = jsonManifest.get(sKey)
        if not isinstance(jsonEntries, list):
            listProblems.append(f"{sManifestWhere} needs an array '{sKey}'")
            continue
        for iEntry, jsonItem in enumerate(jsonEntries):
            listProblems.extend(_flistFindManifestEntryProblems(
                f"{sManifestWhere} '{sKey}' entry {iEntry}",
                jsonItem, tEntryFields))
    return listProblems


def _flistFindManifestEntryProblems(sEntryWhere, jsonItem, tEntryFields):
    """Report one manifest entry whose shape is not exactly its fields."""
    if tEntryFields is None:
        return [] if _fbIsNonEmptyString(jsonItem) else [
            f"{sEntryWhere} must be a non-empty path"]
    if not isinstance(jsonItem, dict) or set(jsonItem) != set(tEntryFields):
        return [f"{sEntryWhere} must be an object with exactly "
                f"{list(tEntryFields)}"]
    listProblems = [
        f"{sEntryWhere} needs a non-empty '{sField}'"
        for sField in tEntryFields
        if sField != "sContent" and not _fbIsNonEmptyString(jsonItem[sField])]
    if not isinstance(jsonItem.get("sContent", ""), str):
        listProblems.append(f"{sEntryWhere} 'sContent' must be text")
    if "sMode" in jsonItem and not _fbMatchesAnchoredPattern(
            S_FILE_MODE_PATTERN, jsonItem["sMode"]):
        listProblems.append(
            f"{sEntryWhere} 'sMode' must be an octal mode such as 755")
    return listProblems


def _flistFindWebSourceProblems(sWhere, jsonEntry):
    """Report a web source whose links or query break their bounds."""
    listProblems = []
    jsonLinks = jsonEntry.get("listSourceLinks")
    if not isinstance(jsonLinks, list) or not (
            1 <= len(jsonLinks) <= I_MAXIMUM_SOURCE_LINKS):
        listProblems.append(
            f"{sWhere} needs 'listSourceLinks': 1 to "
            f"{I_MAXIMUM_SOURCE_LINKS} links")
    else:
        listProblems.extend(
            f"{sWhere} link {iLink} must be one https:// link of at most "
            "2048 characters, exactly as your search returned it"
            for iLink, jsonLink in enumerate(jsonLinks)
            if not _fbMatchesAnchoredPattern(S_SOURCE_LINK_PATTERN, jsonLink))
    if not _fbMatchesAnchoredPattern(
            S_SEARCH_QUERY_PATTERN, jsonEntry.get("sSearchQuery")):
        listProblems.append(
            f"{sWhere} needs 'sSearchQuery': the one-line query you "
            "searched for, at most 500 characters")
    return listProblems


def fsComposeEvidenceFormsText():
    """Name the fields each evidence form carries, and no others.

    The charter's schema template shows every field once; this says
    which belong together, generated from the table the validator
    enforces.
    """
    listLines = [
        f"EVIDENCE FORMS. Each '{S_EVIDENCE_KEY}' entry is exactly one "
        "of these and carries no other field:"]
    for sStateForm, tFields in DICT_EVIDENCE_FIELDS_BY_FORM.items():
        sName = sStateForm or "unconfirmed (no sStateForm)"
        listLines.append(f"- {sName}: {', '.join(tFields)}")
    listLines.append(
        "An unconfirmed entry's status is one of "
        f"{'|'.join(TUPLE_EVIDENCE_UNCONFIRMED_STATUSES)}; baseline and "
        "modifiedState entries are confirmed; a webSource entry is "
        f"{'|'.join(TUPLE_EVIDENCE_WEB_SOURCE_STATUSES)}, never "
        "confirmed. Every dictChangeManifest key is present, [] when "
        "empty.")
    return "\n".join(listLines)


def fdictComposeEvidenceOutputSchema():
    """Constrain evidence to the four forms the engine can settle.

    The strict-schema twin of :func:`flistFindEvidenceClaimProblems`,
    built from the same field table and patterns. Every object lists
    every property as required and forbids any other, the shape strict
    structured output demands, so a map keyed by path never appears.
    """
    dictCommand = {"type": "string", "pattern": S_NON_BLANK_PATTERN}
    dictUnconfirmed = _fdictComposeStrictObject({"sStatus": {
        "type": "string", "enum": list(TUPLE_EVIDENCE_UNCONFIRMED_STATUSES)}})
    dictBaseline = _fdictComposeStrictObject({
        "sStatus": {"type": "string", "enum": [S_EVIDENCE_STATUS_CONFIRMED]},
        "sStateForm": {"type": "string", "enum": [S_EVIDENCE_STATE_BASELINE]},
        "sCommandText": dictCommand,
    })
    dictModified = _fdictComposeStrictObject({
        "sStatus": {"type": "string", "enum": [S_EVIDENCE_STATUS_CONFIRMED]},
        "sStateForm": {"type": "string", "enum": [S_EVIDENCE_STATE_MODIFIED]},
        "sCommandText": dictCommand,
        "dictChangeManifest": _fdictComposeChangeManifestSchema(),
    })
    dictWebSource = _fdictComposeStrictObject({
        "sStatus": {"type": "string",
                    "enum": list(TUPLE_EVIDENCE_WEB_SOURCE_STATUSES)},
        "sStateForm": {"type": "string",
                       "enum": [S_EVIDENCE_STATE_WEB_SOURCE]},
        "listSourceLinks": {
            "type": "array", "minItems": 1,
            "maxItems": I_MAXIMUM_SOURCE_LINKS,
            "items": {"type": "string", "pattern": S_SOURCE_LINK_PATTERN}},
        "sSearchQuery": {"type": "string", "pattern": S_SEARCH_QUERY_PATTERN},
    })
    return {"anyOf": [dictUnconfirmed, dictBaseline, dictModified,
                      dictWebSource]}


def _fdictComposeStrictObject(dictProperties):
    """Return an object schema requiring exactly these properties."""
    return {
        "type": "object",
        "properties": dictProperties,
        "required": list(dictProperties),
        "additionalProperties": False,
    }


def _fdictComposeChangeManifestSchema():
    """Return the manifest schema, one fixed-shape array per change kind."""
    dictPath = {"type": "string", "pattern": S_NON_BLANK_PATTERN}
    dictEntryFieldSchemas = {
        "sPath": dictPath,
        "sContent": {"type": "string"},
        "sMode": {"type": "string", "pattern": S_FILE_MODE_PATTERN},
        "sTarget": {"type": "string", "pattern": S_NON_BLANK_PATTERN},
    }
    dictProperties = {}
    for sKey, tEntryFields in DICT_CHANGE_MANIFEST_ENTRY_FIELDS.items():
        dictItems = dictPath if tEntryFields is None else (
            _fdictComposeStrictObject({
                sField: dictEntryFieldSchemas[sField]
                for sField in tEntryFields}))
        dictProperties[sKey] = {"type": "array", "items": dictItems}
    return _fdictComposeStrictObject(dictProperties)
