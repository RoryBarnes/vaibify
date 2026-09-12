"""The documentation is American English, and the checker can prove a hit.

Dialect consistency is the visible half of this; the interesting half is
that a spelling checker is the kind of tool that goes vacuous silently.
Three ways it can report "clean" while checking nothing: the scan set
resolves empty, the vocabulary stops matching, or the code-span mask
swallows the prose it was meant to leave alone. Each has a test here,
because "0 hits" is the same output in all four cases.

The checker itself is tools/checkAmericanSpelling.py; its vocabulary is
closed and deliberately so -- see that module's docstring.
"""

import pytest

from tools.checkAmericanSpelling import (
    DICT_SPELLING_VOCABULARY,
    flistFindBritishSpellings,
    flistResolveDocumentationPaths,
    fsConvertTextToAmericanSpelling,
)


__all__ = [
    "testTheDocumentationIsAmericanEnglish",
    "testTheScanSetIsNotEmpty",
    "testAPlantedBritishSpellingIsDetected",
    "testAnIdentifierInsideCodeSpansIsNeverRewritten",
    "testProseBesideACodeSpanIsStillChecked",
    "testCapitalizationIsPreserved",
    "testTheVocabularyMapsOnlyToAmericanForms",
]


def testTheDocumentationIsAmericanEnglish():
    """Every documentation file is free of known British spellings."""
    listOffenders = []
    for pathFile in flistResolveDocumentationPaths():
        for iLine, sFound, sAmerican in flistFindBritishSpellings(
            pathFile.read_text(encoding="utf-8"),
        ):
            listOffenders.append(
                f"{pathFile.name}:{iLine}: {sFound} -> {sAmerican}",
            )
    assert not listOffenders, (
        "British spellings in the documentation:\n  "
        + "\n  ".join(listOffenders)
        + "\nFix with: python tools/checkAmericanSpelling.py --write"
    )


def testTheScanSetIsNotEmpty():
    """A checker that resolves no files reports clean for having looked
    at nothing. Pin the set so a moved directory fails loudly."""
    listPaths = flistResolveDocumentationPaths()
    assert len(listPaths) > 5, f"scan set collapsed to {listPaths}"
    assert any(pathFile.name == "README.md" for pathFile in listPaths)


@pytest.mark.parametrize("sBritish,sAmerican", [
    ("behaviour", "behavior"),
    ("colour", "color"),
    ("artefact", "artifact"),
    ("organised", "organized"),
    ("whilst", "while"),
    ("judgement", "judgment"),
])
def testAPlantedBritishSpellingIsDetected(sBritish, sAmerican):
    """The clean result above is only meaningful if a hit is findable."""
    sText = f"The {sBritish} of the system is documented."
    listHits = flistFindBritishSpellings(sText)
    assert listHits == [(1, sBritish, sAmerican)]
    assert sAmerican in fsConvertTextToAmericanSpelling(sText)


def testAnIdentifierInsideCodeSpansIsNeverRewritten():
    """LIST_MODELLED_COMMANDS is a real constant. Rewriting a spelling
    inside backticks would turn a doc fix into a broken reference."""
    sText = (
        "Commands are listed in `LIST_MODELLED_COMMANDS`.\n"
        "```python\nsColour = fakeAdapter.LIST_MODELLED_COMMANDS\n```\n"
    )
    assert flistFindBritishSpellings(sText) == []
    assert fsConvertTextToAmericanSpelling(sText) == sText


def testProseBesideACodeSpanIsStillChecked():
    """The mask must blank code spans without swallowing the line."""
    sText = "The `dictColour` map records the colour of each row."
    listHits = flistFindBritishSpellings(sText)
    assert listHits == [(1, "colour", "color")]
    sConverted = fsConvertTextToAmericanSpelling(sText)
    assert "`dictColour`" in sConverted
    assert "the color of each row" in sConverted


def testCapitalizationIsPreserved():
    """A heading and a sentence start must survive the rewrite."""
    assert fsConvertTextToAmericanSpelling(
        "Colour and BEHAVIOUR and grey",
    ) == "Color and BEHAVIOR and gray"


def testTheVocabularyMapsOnlyToAmericanForms():
    """No entry may map a word to itself or to another British form --
    either would make the checker a no-op for that word, or loop it."""
    for sBritish, sAmerican in DICT_SPELLING_VOCABULARY.items():
        assert sBritish != sAmerican, f"{sBritish} maps to itself"
        assert sAmerican not in DICT_SPELLING_VOCABULARY, (
            f"{sBritish} -> {sAmerican}, which is itself British"
        )
