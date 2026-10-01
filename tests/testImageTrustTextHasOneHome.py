"""The words about the three answers live in one backend module.

The dashboard modal and the command line must say the same thing, so
neither may carry its own copy of an option's label, summary or detail.
The browser lane proves the modal renders the backend's text; this proves
no frontend file holds a second copy that could drift.
"""

import pathlib

from vaibify.config import imageTrust

PATH_STATIC = pathlib.Path(__file__).resolve().parent.parent / (
    "vaibify/gui/static")


def flistAuthoredPhrases():
    listPhrases = []
    for dictOption in imageTrust.LIST_TRUST_OPTIONS:
        listPhrases.extend(
            [dictOption["sLabel"], dictOption["sSummary"]]
            + dictOption["listDetailLines"])
    listPhrases.append(imageTrust.DICT_CREDENTIAL_OPTION["sLabel"])
    listPhrases.extend(imageTrust.DICT_CREDENTIAL_OPTION["listDetailLines"])
    return listPhrases


def testNoFrontendFileCarriesAnOptionsText():
    listOffenders = []
    for pathFile in sorted(PATH_STATIC.glob("*.js")):
        sSource = pathFile.read_text(encoding="utf-8")
        for sPhrase in flistAuthoredPhrases():
            if sPhrase in sSource:
                listOffenders.append(f"{pathFile.name}: {sPhrase[:50]}")
    assert not listOffenders, listOffenders


def testTheCommandLineAndTheDashboardReadTheSameDescription():
    dictChoices = imageTrust.fdictDescribeTrustChoices()
    assert dictChoices["listOptions"] == imageTrust.LIST_TRUST_OPTIONS
    assert [d["sChoice"] for d in dictChoices["listOptions"]] == list(
        imageTrust.TUPLE_TRUST_CHOICES)
    assert dictChoices["dictCredentials"] == imageTrust.DICT_CREDENTIAL_OPTION
