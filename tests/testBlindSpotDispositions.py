"""Every site the scan cannot read is ruled on, and the ruling can expire.

The inventory declares its blind spot -- launches whose argv is assembled
where the AST scan cannot follow -- and identifies each site by two
hashes: the call expression and the whole enclosing function. That is
enough to prove "same site". It is NOT enough to preserve "somebody
reviewed this", and the gap is demonstrated rather than argued: for

    def f():
        command = fbuildGitCommand()
        subprocess.run(command)

the call expression is ``subprocess.run(command)`` both before AND after
the builder is swapped from assembling a git invocation to assembling
``docker rm``. Verified in
``testMutationInventory.py::testABlindSpotDispositionDiesWithItsCommandBuilder``:
identical hashes. The scope hash catches that particular swap because the
builder is NAMED inside the function -- but a builder DEFINED elsewhere,
a constant the argv is built from, or a helper that supplies the
executable all sit outside it.

So a manual disposition here must NAME the symbols its review relied on,
and those symbols are fingerprinted too. Change what the review depended
on and the ruling expires, whatever the call site looks like.
**A call-node hash is an identity, never a warrant for a semantic
review.**

The five sites under ``vaibify/gui/`` are ruled on below. None is left
undisposed; one is an exceptional authority with a tested constraint,
and the other four are opaque to the SCANNER while being entirely
determined in the source -- an argv whose executable is a literal, with
only its arguments variable. That distinction is the whole content of
these rulings, and it is why they are written by hand.
"""

import ast
import hashlib
import importlib.util
import pathlib

import pytest


PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"

# The terminal states a blind-spot site may end in. "Mechanically
# traceable" is absent on purpose: a site in this record is by
# construction one the scan could not read, so tracing it resolves it by
# REMOVING it from the blind spot, not by earning a disposition here.
S_DISPOSITION_EXCEPTIONAL_AUTHORITY = "exceptional-authority"
S_DISPOSITION_DETERMINED_IN_SOURCE = "determined-in-source"
S_DISPOSITION_SEPARATE_AUTHORITY = "separate-authority"

SET_DISPOSITIONS = frozenset({
    S_DISPOSITION_EXCEPTIONAL_AUTHORITY,
    S_DISPOSITION_DETERMINED_IN_SOURCE,
    S_DISPOSITION_SEPARATE_AUTHORITY,
})


DICT_BLIND_SPOT_DISPOSITIONS = {
    "gui/commitCarrier.py|fdictLaunchGatedHelperProcess|"
    "opaque-subprocess-command|0": {
        "sDisposition": S_DISPOSITION_EXCEPTIONAL_AUTHORITY,
        "sRationale": (
            "The one generic command authority under vaibify/gui/: it "
            "spawns any argv a caller hands it, so no argument about "
            "what the command WILL be can constrain it. Its constraint "
            "is structural instead, and it is two things at once. The "
            "spawn is bracketed by the journal -- fsPrepareOperation "
            "before, fnPromoteOperationToInFlight with the child's real "
            "pid after -- so no phase of it can leave a writer the "
            "journal cannot name. And the promoted identity is then "
            "ASSERTED against the admission, so a helper whose identity "
            "the journal did not accept never reaches its release line. "
            "The child blocks on a stdin gate until that release, and a "
            "parent that dies first closes the pipe, so the helper exits "
            "without acting. Tested by "
            "testTheGatedHelperIsConstrainedByItsHolderIdentity."
        ),
        "listSupportingSymbols": [
            "gui/commitCarrier.py::S_GATED_HELPER_STUB",
            "gui/commitCarrier.py::fdictLaunchGatedHelperProcess",
            "config/mutationAdmission.py::fnAssertOperationAdmittedByIdentity",
        ],
    },
    "gui/gitStatus.py|fsRunGit|opaque-subprocess-command|0": {
        "sDisposition": S_DISPOSITION_DETERMINED_IN_SOURCE,
        "sRationale": (
            "Opaque to the scanner, determined in the source: the "
            "executable is the literal \"git\" and the hardening flags "
            "are a module constant. Only the git SUBCOMMAND varies, so "
            "this can never become a Docker invocation however its "
            "callers change. It runs in a HOST checkout -- container-side "
            "git is containerGit, which goes through the gateway."
        ),
        "listSupportingSymbols": [
            "gui/gitStatus.py::fsRunGit",
            "gui/gitStatus.py::_fdictBaseEnv",
            "reproducibility/gitHardening.py::LIST_GIT_HARDENING_CONFIG",
        ],
    },
    "gui/routes/sessionRoutes.py|_fnLaunchDetachedHub|"
    "opaque-subprocess-command|0": {
        "sDisposition": S_DISPOSITION_DETERMINED_IN_SOURCE,
        "sRationale": (
            "Fully literal but for the port number: it re-launches "
            "vaibify itself as a detached host process. Opaque only "
            "because sys.executable is not a constant the scan can "
            "resolve. It spawns the hub, never a container command, and "
            "the agent lane is refused at the route."
        ),
        "listSupportingSymbols": [
            "gui/routes/sessionRoutes.py::_fnLaunchDetachedHub",
            "gui/routes/sessionRoutes.py::S_SUPPRESS_BROWSER_ENV",
            "gui/routes/sessionRoutes.py::_fnRejectContainerAgentCallers",
        ],
    },
    "gui/setupServer.py|ftResultRunBuild|opaque-subprocess-command|0": {
        "sDisposition": S_DISPOSITION_DETERMINED_IN_SOURCE,
        "sRationale": (
            "`python -m vaibify build` in the project directory, every "
            "element of it a literal but the interpreter path. The "
            "first-run wizard's build lane: it runs before any container "
            "for the project exists, so there is nothing yet for a "
            "carrier to be admitted against."
        ),
        "listSupportingSymbols": [
            "gui/setupServer.py::ftResultRunBuild",
        ],
    },
    "gui/syncDispatcher.py|_ftRunHostLsRemote|opaque-subprocess-command|0": {
        "sDisposition": S_DISPOSITION_DETERMINED_IN_SOURCE,
        "sRationale": (
            "`git ls-remote` against a fixed host, to validate a token "
            "before it is used. Executable is the literal \"git\"; the "
            "only variable part is the project id inside a URL built "
            "from a module constant. Reaches a remote, never a "
            "container, and its stderr is redacted so a credential "
            "cannot ride out in an error message."
        ),
        "listSupportingSymbols": [
            "gui/syncDispatcher.py::_ftRunHostLsRemote",
            "gui/syncDispatcher.py::_S_OVERLEAF_HOST",
            "reproducibility/gitHardening.py::"
            "LIST_GIT_CREDENTIAL_ISOLATION_CONFIG",
        ],
    },
}


# The fingerprint of each supporting symbol at the time its disposition
# was written. Regenerate ONLY after re-reading the ruling it belongs to:
# a mismatch means the code a review depended on changed, which is the
# review expiring, not a hash needing a refresh.
# Print the current values by running
#   python -m pytest tests/testBlindSpotDispositions.py -q
# and reading testADispositionExpiresWhenItsSupportingSymbolsChange,
# whose failure names every symbol that moved and its new hash.
DICT_SUPPORTING_SYMBOL_FINGERPRINTS = {
    "config/mutationAdmission.py::fnAssertOperationAdmittedByIdentity":
        "712b9ca458bbe5ee",
    "gui/commitCarrier.py::S_GATED_HELPER_STUB": "2bd6936769eb618c",
    "gui/commitCarrier.py::fdictLaunchGatedHelperProcess":
        "58b0217540f74765",
    "gui/gitStatus.py::_fdictBaseEnv": "944845e8671a1808",
    "gui/gitStatus.py::fsRunGit": "fde0aaa94089d1cc",
    "gui/routes/sessionRoutes.py::S_SUPPRESS_BROWSER_ENV":
        "b0122d40b40d22c0",
    "gui/routes/sessionRoutes.py::_fnLaunchDetachedHub":
        "3a7b1ee2a7bf4eec",
    "gui/routes/sessionRoutes.py::_fnRejectContainerAgentCallers":
        "7c0e85b8e21ddc5e",
    "gui/setupServer.py::ftResultRunBuild": "a800be49bf2826a8",
    "gui/syncDispatcher.py::_S_OVERLEAF_HOST": "65054ecdea58cd8f",
    "gui/syncDispatcher.py::_ftRunHostLsRemote": "8d2e498e92cd5b5b",
    "reproducibility/gitHardening.py::LIST_GIT_CREDENTIAL_ISOLATION_CONFIG":
        "f11e8b4053702d61",
    "reproducibility/gitHardening.py::LIST_GIT_HARDENING_CONFIG":
        "bbc7222727695adc",
}


def _fmoduleGenerator():
    """Import the inventory generator by path (tools/ is not a package)."""
    pathTool = PATH_REPOSITORY / "tools" / "generateMutationInventory.py"
    spec = importlib.util.spec_from_file_location(
        "generateMutationInventoryForBlindSpots", pathTool,
    )
    moduleTool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(moduleTool)
    return moduleTool


@pytest.fixture(scope="module")
def moduleGenerator():
    return _fmoduleGenerator()


def fsFingerprintSupportingSymbol(sSymbolReference):
    """Return the hash of one named symbol's own source text.

    ``<package-relative file>::<top-level name>``. The name may be a
    function, a class, or an assignment target -- a constant list of git
    flags is exactly the kind of thing a ruling rests on, and a record
    that could only fingerprint functions would leave it unwatched.

    Raises when the symbol cannot be found, because a disposition
    pointing at a symbol that no longer exists is the loudest possible
    signal that the review has expired.
    """
    sFile, sName = sSymbolReference.split("::")
    treeModule = ast.parse((PATH_PACKAGE / sFile).read_text())
    sSource = _fsSourceOfTopLevelName(treeModule, sName, sFile)
    return hashlib.sha256(sSource.encode("utf-8")).hexdigest()[:16]


def _fsSourceOfTopLevelName(treeModule, sName, sFile):
    """Return the source text defining one top-level name."""
    sModuleSource = (PATH_PACKAGE / sFile).read_text()
    for nodeTop in treeModule.body:
        if isinstance(
            nodeTop, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ) and nodeTop.name == sName:
            return ast.get_source_segment(sModuleSource, nodeTop)
        if isinstance(nodeTop, ast.Assign):
            for nodeTarget in nodeTop.targets:
                if isinstance(nodeTarget, ast.Name) and nodeTarget.id == sName:
                    return ast.get_source_segment(sModuleSource, nodeTop)
    raise LookupError(
        f"{sFile}::{sName} is named by a disposition and no longer "
        f"exists at module level; that ruling has expired"
    )


# ---------------------------------------------------------------------
# Every blind spot is ruled on, and no ruling is left dangling.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testEveryGuiBlindSpotCarriesADisposition(moduleGenerator):
    """No UNDISPOSED opaque site may remain under vaibify/gui/.

    Not "none remains" -- a mechanically opaque site may survive with an
    independent ruling. What may not survive is one nobody ruled on.

    Kills: adding an opaque subprocess launch under vaibify/gui/ without
    a disposition.
    """
    listUndisposed = sorted(
        moduleGenerator.fsBlindSpotKey(dictSite)
        for dictSite in moduleGenerator.flistUnresolvedSites()
        if dictSite["sFile"].startswith("gui/")
        and moduleGenerator.fsBlindSpotKey(dictSite)
        not in DICT_BLIND_SPOT_DISPOSITIONS
    )
    assert listUndisposed == [], (
        f"these launches under vaibify/gui/ have an argv the scan cannot "
        f"read and no ruling on what they run: {listUndisposed}. Trace "
        f"the site, or add a disposition naming the symbols your review "
        f"relied on."
    )


def testNoDispositionOutlivesTheSiteItRuledOn(moduleGenerator):
    """A ruling on a site that moved is a review aimed at fiction."""
    setPresent = {
        moduleGenerator.fsBlindSpotKey(dictSite)
        for dictSite in moduleGenerator.flistUnresolvedSites()
    }
    listStale = sorted(set(DICT_BLIND_SPOT_DISPOSITIONS) - setPresent)
    assert listStale == [], (
        f"these dispositions name blind spots that no longer exist -- "
        f"the site was traced, moved or deleted: {listStale}"
    )


def testEveryDispositionNamesSupportingSymbolsAndAReason():
    """A ruling with no named support is an opinion, not a review.

    The supporting symbols ARE the review: they say what a reader had to
    look at to believe the ruling. A disposition without them can only be
    re-checked by redoing the whole investigation, which is the thing
    nobody repeats before shipping.
    """
    listOffenders = []
    for sKey, dictEntry in DICT_BLIND_SPOT_DISPOSITIONS.items():
        if dictEntry["sDisposition"] not in SET_DISPOSITIONS:
            listOffenders.append((sKey, "undeclared disposition"))
        if not dictEntry["sRationale"].strip():
            listOffenders.append((sKey, "no rationale"))
        if not dictEntry["listSupportingSymbols"]:
            listOffenders.append((sKey, "no supporting symbols"))
    assert listOffenders == [], listOffenders


@pytest.mark.falsification
def testADispositionExpiresWhenItsSupportingSymbolsChange():
    """The ruling is bound to the code the review actually read.

    The site fingerprints prove "same call, same enclosing function".
    They say nothing about a constant the argv is assembled from or a
    helper defined in another module, and those are exactly what these
    rulings rest on -- "the executable is the literal git and the flags
    are a module constant" is a claim about a symbol two files away.

    Kills: editing LIST_GIT_HARDENING_CONFIG, or any other named
    supporting symbol, without re-reading the ruling that cites it.
    """
    dictMoved = {}
    for sSymbol, sRecorded in sorted(
        DICT_SUPPORTING_SYMBOL_FINGERPRINTS.items(),
    ):
        sActual = fsFingerprintSupportingSymbol(sSymbol)
        if sActual != sRecorded:
            dictMoved[sSymbol] = sActual
    assert dictMoved == {}, (
        f"the code these rulings depend on changed. Re-READ each "
        f"disposition that cites the symbol, decide whether it still "
        f"holds, and only then record the new fingerprint:\n"
        + "\n".join(
            f'    "{sSymbol}": "{sActual}",'
            for sSymbol, sActual in sorted(dictMoved.items())
        )
    )


def testEverySupportingSymbolIsFingerprinted():
    """The two records cannot drift apart.

    A symbol named by a ruling but absent from the fingerprint table
    would be cited support that nothing watches -- the failure mode this
    whole file exists to close, reintroduced through bookkeeping.
    """
    setNamed = {
        sSymbol
        for dictEntry in DICT_BLIND_SPOT_DISPOSITIONS.values()
        for sSymbol in dictEntry["listSupportingSymbols"]
    }
    assert setNamed == set(DICT_SUPPORTING_SYMBOL_FINGERPRINTS), (
        f"named but unwatched: "
        f"{sorted(setNamed - set(DICT_SUPPORTING_SYMBOL_FINGERPRINTS))}; "
        f"watched but cited by nobody: "
        f"{sorted(set(DICT_SUPPORTING_SYMBOL_FINGERPRINTS) - setNamed)}"
    )
