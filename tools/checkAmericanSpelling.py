#!/usr/bin/env python3
"""Enforce American English spelling in vaibify's documentation.

The documentation is the product's public face, and a file that drifts
between "behaviour" and "behavior" reads as careless in exactly the
place a reader is deciding whether to trust the software. Dialect is
not a matter of taste here, it is a matter of consistency, so it is
checked mechanically rather than left to whoever edits a page next.

**This checker is honest about its scope.** It knows a closed
vocabulary and nothing else: a British spelling absent from the list
below passes silently. That is a deliberate trade. The alternative --
a general dialect model, or a stem-matching heuristic -- produces
false positives on identifiers and technical terms ("analyses",
"aria-labelledby", "realistic"), and a checker that cries wolf is one
people learn to bypass. Every entry here is an unambiguous British
form, so a match is never a false alarm; growing the vocabulary is one
line of data.

Run it over the documentation set:

    python tools/checkAmericanSpelling.py            # report, exit 1 on a hit
    python tools/checkAmericanSpelling.py --write    # rewrite in place
    python tools/checkAmericanSpelling.py <path>...  # a chosen set
"""

import argparse
import re
import sys
from pathlib import Path


__all__ = [
    "fdictBuildSpellingVocabulary",
    "flistFindBritishSpellings",
    "fsConvertTextToAmericanSpelling",
    "flistResolveDocumentationPaths",
    "main",
]


REPO_ROOT = Path(__file__).resolve().parent.parent

# The default scan set: everything a reader of the project encounters.
# Source comments and frontend strings are deliberately NOT included --
# renaming an identifier is a refactor with ledger consequences, not a
# spelling fix, and conflating the two would make this check unsafe to
# run with --write.
LIST_DOCUMENTATION_GLOBS = ["docs/*.md", "README.md"]

# Verb stems whose British "-ise" family maps to American "-ize". The
# whole family is generated from the stem, so adding a verb is one
# entry rather than nine.
LIST_ISE_VERB_STEMS = [
    "apolog", "author", "categor", "character", "civil", "critic",
    "custom", "dramat", "emphas", "familiar", "final", "formal",
    "general", "hypothes", "item", "legal", "local", "material",
    "maxim", "memo", "minim", "mobil", "modern", "monet", "moral",
    "normal", "optim", "organ", "oxid", "parameter", "penal", "polar",
    "popular", "pressur", "priorit", "public", "random", "rational",
    "real", "recogn", "revolution", "sanit", "scrutin", "serial",
    "social", "special", "stabil", "standard", "steril", "summar",
    "symbol", "sympath", "synchron", "synthes", "theor", "token",
    "util", "vapor", "verbal", "victim", "visual", "vocal",
]

# The "-ise" family's suffixes, British form to American form.
T_ISE_SUFFIX_PAIRS = (
    ("ise", "ize"), ("ised", "ized"), ("ises", "izes"),
    ("ising", "izing"), ("isation", "ization"), ("isations", "izations"),
    ("isable", "izable"), ("iser", "izer"), ("isers", "izers"),
)

# Everything the stem rule cannot generate. Inflections are listed
# explicitly because English irregularity is not worth a second rule
# engine -- "behaviour" takes "behavioural", "grey" takes "greyish",
# and no suffix table predicts both.
DICT_IRREGULAR_SPELLINGS = {
    "aeon": "eon", "aeons": "eons",
    "ageing": "aging",
    "aluminium": "aluminum",
    "amidst": "amid",
    "amongst": "among",
    "analyse": "analyze", "analysed": "analyzed", "analysing": "analyzing",
    "analyser": "analyzer", "analysers": "analyzers",
    "artefact": "artifact", "artefacts": "artifacts",
    "behaviour": "behavior", "behaviours": "behaviors",
    "behavioural": "behavioral", "behaviourally": "behaviorally",
    "cancelled": "canceled", "cancelling": "canceling",
    "catalogue": "catalog", "catalogues": "catalogs",
    "catalogued": "cataloged", "cataloguing": "cataloging",
    "centre": "center", "centres": "centers",
    "centred": "centered", "centring": "centering",
    "cheque": "check", "cheques": "checks",
    "colour": "color", "colours": "colors",
    "coloured": "colored", "colouring": "coloring",
    "colourful": "colorful", "colourless": "colorless",
    "connexion": "connection",
    "counsellor": "counselor", "counsellors": "counselors",
    "defence": "defense", "defences": "defenses",
    "dependant": "dependent", "dependants": "dependents",
    "draught": "draft", "draughts": "drafts",
    "dreamt": "dreamed",
    "enquire": "inquire", "enquired": "inquired",
    "enquiry": "inquiry", "enquiries": "inquiries",
    "enrol": "enroll", "enrols": "enrolls", "enrolment": "enrollment",
    "favour": "favor", "favours": "favors",
    "favoured": "favored", "favouring": "favoring",
    "favourable": "favorable", "favourite": "favorite",
    "fulfil": "fulfill", "fulfils": "fulfills",
    "fulfilment": "fulfillment",
    "grey": "gray", "greys": "grays", "greyed": "grayed",
    "greying": "graying", "greyer": "grayer", "greyish": "grayish",
    "honour": "honor", "honours": "honors",
    "honoured": "honored", "honouring": "honoring",
    "honourable": "honorable",
    "instalment": "installment", "instalments": "installments",
    "judgement": "judgment", "judgements": "judgments",
    "kerb": "curb",
    "labelled": "labeled", "labelling": "labeling",
    "learnt": "learned",
    "licence": "license", "licences": "licenses",
    "litre": "liter", "litres": "liters",
    "manoeuvre": "maneuver", "manoeuvres": "maneuvers",
    "marshalled": "marshaled", "marshalling": "marshaling",
    "metre": "meter", "metres": "meters",
    "modelled": "modeled", "modelling": "modeling",
    "mould": "mold", "moulded": "molded", "moulding": "molding",
    "neighbour": "neighbor", "neighbours": "neighbors",
    "neighbouring": "neighboring",
    "offence": "offense", "offences": "offenses",
    "orientated": "oriented",
    "plough": "plow", "ploughed": "plowed",
    "practise": "practice", "practised": "practiced",
    "practising": "practicing",
    "pretence": "pretense",
    "programme": "program", "programmes": "programs",
    "pyjamas": "pajamas",
    "sceptic": "skeptic", "sceptics": "skeptics",
    "sceptical": "skeptical", "scepticism": "skepticism",
    "signalled": "signaled", "signalling": "signaling",
    "skilful": "skillful",
    "speciality": "specialty", "specialities": "specialties",
    "spelt": "spelled",
    "storey": "story", "storeys": "stories",
    "sulphur": "sulfur",
    "travelled": "traveled", "travelling": "traveling",
    "traveller": "traveler", "travellers": "travelers",
    "tyre": "tire", "tyres": "tires",
    "whilst": "while",
    "wilful": "willful",
}


def fdictBuildSpellingVocabulary():
    """Return the full British-to-American word map, stems expanded."""
    dictVocabulary = dict(DICT_IRREGULAR_SPELLINGS)
    for sStem in LIST_ISE_VERB_STEMS:
        for sBritishSuffix, sAmericanSuffix in T_ISE_SUFFIX_PAIRS:
            dictVocabulary[sStem + sBritishSuffix] = sStem + sAmericanSuffix
    return dictVocabulary


DICT_SPELLING_VOCABULARY = fdictBuildSpellingVocabulary()

# Word boundaries on both sides are what make the vocabulary safe:
# "aria-labelledby" does not match "labelled", and "realistic" does not
# match "realise". Longest-first so "behaviourally" cannot be consumed
# by "behaviour".
REGEX_BRITISH_WORD = re.compile(
    r"\b(" + "|".join(
        re.escape(sWord) for sWord in
        sorted(DICT_SPELLING_VOCABULARY, key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)


# Fenced blocks and inline spans hold identifiers, not prose, and an
# identifier's spelling is its author's to choose: `LIST_MODELLED_COMMANDS`
# is a real constant in tests/testBrowserLaneContract.py, and a checker
# that rewrote it inside backticks would turn a documentation fix into a
# broken reference. Masking preserves length and newlines, so offsets and
# line numbers in the masked copy are the original's.
REGEX_CODE_SPAN = re.compile(r"```.*?```|`[^`\n]+`", re.S)


def _fsMaskCodeSpans(sText):
    """Return sText with code spans blanked, length and lines preserved."""
    def _fsBlankOneSpan(matchObject):
        return re.sub(r"[^\n]", " ", matchObject.group(0))

    return REGEX_CODE_SPAN.sub(_fsBlankOneSpan, sText)


def _fsMatchTheCaseOf(sReplacement, sOriginal):
    """Return sReplacement wearing sOriginal's capitalization."""
    if sOriginal.isupper():
        return sReplacement.upper()
    if sOriginal[:1].isupper():
        return sReplacement[:1].upper() + sReplacement[1:]
    return sReplacement


def _flistLocateBritishSpellings(sText):
    """Return (start, end, found, American) for every hit outside code."""
    listLocations = []
    for matchObject in REGEX_BRITISH_WORD.finditer(_fsMaskCodeSpans(sText)):
        sFound = sText[matchObject.start():matchObject.end()]
        listLocations.append((
            matchObject.start(), matchObject.end(), sFound,
            _fsMatchTheCaseOf(
                DICT_SPELLING_VOCABULARY[sFound.lower()], sFound,
            ),
        ))
    return listLocations


def fsConvertTextToAmericanSpelling(sText):
    """Return sText with every known British spelling replaced."""
    listPieces = []
    iCursor = 0
    for iStart, iEnd, _sFound, sAmerican in _flistLocateBritishSpellings(sText):
        listPieces.append(sText[iCursor:iStart])
        listPieces.append(sAmerican)
        iCursor = iEnd
    listPieces.append(sText[iCursor:])
    return "".join(listPieces)


def flistFindBritishSpellings(sText):
    """Return (line number, found word, American form) for every hit."""
    return [
        (sText.count("\n", 0, iStart) + 1, sFound, sAmerican)
        for iStart, _iEnd, sFound, sAmerican
        in _flistLocateBritishSpellings(sText)
    ]


def flistResolveDocumentationPaths(pathRoot=REPO_ROOT):
    """Return the default documentation scan set, sorted."""
    listPaths = []
    for sGlob in LIST_DOCUMENTATION_GLOBS:
        listPaths.extend(pathRoot.glob(sGlob))
    return sorted(pathFile for pathFile in listPaths if pathFile.is_file())


def main():
    parserArguments = argparse.ArgumentParser(
        description="Check documentation for British English spellings.",
    )
    parserArguments.add_argument(
        "paths", nargs="*",
        help="files to check (default: the documentation set)",
    )
    parserArguments.add_argument(
        "--write", action="store_true",
        help="rewrite the files in place instead of reporting",
    )
    namespaceArguments = parserArguments.parse_args()

    listPaths = (
        [Path(sPath) for sPath in namespaceArguments.paths]
        if namespaceArguments.paths else flistResolveDocumentationPaths()
    )
    if not listPaths:
        print("checkAmericanSpelling: no files to check", file=sys.stderr)
        return 2

    iTotalHits = 0
    for pathFile in listPaths:
        try:
            sText = pathFile.read_text(encoding="utf-8")
        except OSError as errorRead:
            print(f"cannot read {pathFile}: {errorRead}", file=sys.stderr)
            return 2
        listHits = flistFindBritishSpellings(sText)
        if not listHits:
            continue
        iTotalHits += len(listHits)
        if namespaceArguments.write:
            pathFile.write_text(
                fsConvertTextToAmericanSpelling(sText), encoding="utf-8",
            )
            print(f"{pathFile}: rewrote {len(listHits)} spelling(s)")
            continue
        for iLineNumber, sFound, sAmerican in listHits:
            print(f"{pathFile}:{iLineNumber}: {sFound} -> {sAmerican}")

    if iTotalHits and not namespaceArguments.write:
        print(
            f"\n{iTotalHits} British spelling(s) in {len(listPaths)} file(s) "
            f"checked against a {len(DICT_SPELLING_VOCABULARY)}-word "
            f"vocabulary.\nFix them with: "
            f"python tools/checkAmericanSpelling.py --write",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
