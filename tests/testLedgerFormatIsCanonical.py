"""A checked-in ledger must be byte-exactly what its generator writes.

Every other check over these three files parses JSON first, so not one
of them can see how the file is laid out. That blind spot has already
cost: ``mutationInventory.json`` was once rewritten by hand with
``json.dumps(..., indent=1)`` while its generator wrote ``indent=2``.
The committed file then disagreed with its own generator, every drift
check stayed green, and the next ``--write`` by anybody reformatted all
ten thousand lines -- turning a two-line change into a guaranteed
conflict with every other open branch.

So this test compares BYTES. It re-renders each ledger from its own
parsed contents and requires the result to equal the file on disk. It
deliberately does not scan the source: whether a ledger still describes
the code is the drift checks' business, and re-scanning here would make
this test fail for reasons that have nothing to do with formatting. A
ledger can be canonically formatted and completely wrong; this says only
that its form is the one form.

The second test states the property the form exists for, so that a
future edit reverting to multi-line records fails with the reason
attached rather than with a diff.
"""

import importlib.util
import json
import pathlib

import pytest

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent


def _fmoduleLoadByPath(sName):
    """Import a tools/ module by path -- tools/ is not a package."""
    pathModule = PATH_REPOSITORY / "tools" / f"{sName}.py"
    specModule = importlib.util.spec_from_file_location(
        f"{sName}UnderLedgerFormatTest", pathModule,
    )
    moduleLoaded = importlib.util.module_from_spec(specModule)
    specModule.loader.exec_module(moduleLoaded)
    return moduleLoaded


# Every generated ledger, with the generator that owns its shape. A new
# ledger belongs here on the day it is added: the cost of forgetting is
# that it silently reacquires the conflict behaviour this suite removed.
T_LEDGERS = (
    ("mutationInventory.json", "generateMutationInventory"),
    ("hostCapabilityInventory.json", "generateHostCapabilityInventory"),
    ("styleInventory.json", "generateStyleInventory"),
)


@pytest.mark.parametrize("sFileName,sGeneratorName", T_LEDGERS)
def testCheckedInLedgerIsCanonicallyFormatted(sFileName, sGeneratorName):
    """The file on disk equals a re-render of its own contents."""
    pathLedger = PATH_REPOSITORY / "tests" / sFileName
    sOnDisk = pathLedger.read_text(encoding="utf-8")
    moduleGenerator = _fmoduleLoadByPath(sGeneratorName)
    sCanonical = moduleGenerator.ledgerFormat.fsRenderLedger(
        json.loads(sOnDisk), moduleGenerator.T_RECORD_COLLECTION_KEYS,
    )
    assert sOnDisk == sCanonical, (
        f"tests/{sFileName} is not in the form "
        f"tools/{sGeneratorName}.py writes, so the next --write will "
        f"reformat the whole file and collide with every open branch. "
        f"Regenerate it rather than editing it by hand: "
        f"python tools/{sGeneratorName}.py --write"
    )


@pytest.mark.parametrize("sFileName,sGeneratorName", T_LEDGERS)
def testEveryRecordOccupiesExactlyOneLine(sFileName, sGeneratorName):
    """The property the canonical form exists for, asserted directly.

    A record spread over many lines puts git's three lines of context
    inside a single record, so two branches adding unrelated rows that
    happen to sort near each other conflict. One record per line is what
    stops that, and it is worth failing loudly rather than as a byte
    diff if somebody reverts it.
    """
    pathLedger = PATH_REPOSITORY / "tests" / sFileName
    sOnDisk = pathLedger.read_text(encoding="utf-8")
    jsonLedger = json.loads(sOnDisk)
    moduleGenerator = _fmoduleLoadByPath(sGeneratorName)
    listLines = sOnDisk.splitlines()
    for sKey in moduleGenerator.T_RECORD_COLLECTION_KEYS:
        listBlock = _flistRecordBlockLines(listLines, sKey)
        assert len(listBlock) == len(jsonLedger[sKey]), (
            f"tests/{sFileName}: {sKey} holds "
            f"{len(jsonLedger[sKey])} records across {len(listBlock)} "
            f"lines. One record per line is what keeps two branches "
            f"touching unrelated rows from conflicting; see "
            f"tools/ledgerFormat.py."
        )


def _flistRecordBlockLines(listLines, sKey):
    """Return the lines strictly between one collection's brackets."""
    sOpening = f"  {json.dumps(sKey)}: "
    for iIndex, sLine in enumerate(listLines):
        if not sLine.startswith(sOpening):
            continue
        if sLine.rstrip(",").endswith(("[]", "{}")):
            return []
        listBlock = []
        for sInner in listLines[iIndex + 1:]:
            if sInner.startswith(("  ]", "  }")):
                return listBlock
            listBlock.append(sInner)
    raise AssertionError(f"no {sKey} collection block in the ledger")


def testDerivedCountsHaveNotComeBack():
    """No ledger may carry a field holding the length of its own list.

    Three such fields sat at the top of mutationInventory.json, so every
    branch that added or removed one row rewrote the same three lines --
    a guaranteed conflict between branches with nothing else in common.
    They proved nothing either: each drift check already compares its
    recorded list against a fresh scan. A count beside the data it
    counts is a second copy, not a check.

    Written as a rule rather than a one-time cleanup because the
    temptation to "record how many there are" recurs.

    A RATCHET IS NOT A DERIVED COUNT and is named here rather than
    inferred. ``iUndisposedSiteBudget`` is a ceiling a human lowered on
    purpose; that it is an integer near a collection is a coincidence,
    and reading intent from the value would fail the day a budget
    happened to equal a row count.
    """
    setRatchetFields = {"iUndisposedSiteBudget"}
    for sFileName, _ in T_LEDGERS:
        jsonLedger = json.loads(
            (PATH_REPOSITORY / "tests" / sFileName).read_text(
                encoding="utf-8",
            ),
        )
        setLengths = {
            len(objValue) for objValue in jsonLedger.values()
            if isinstance(objValue, (list, dict))
        }
        listOffenders = [
            sKey for sKey, objValue in jsonLedger.items()
            if isinstance(objValue, int) and sKey not in setRatchetFields
            and objValue in setLengths
        ]
        assert not listOffenders, (
            f"tests/{sFileName} has {listOffenders}, which hold the "
            f"length of a collection in the same file. Derive it with "
            f"len() where it is needed; a stored count only guarantees "
            f"that every branch rewrites the same line. If it is a "
            f"deliberate ratchet, name it in setRatchetFields."
        )
