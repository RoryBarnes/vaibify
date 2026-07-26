"""The JS slug mirror must agree with the backend, not merely resemble it.

The slug contract (c3a6cdd, 2026-07-18) says a step's directory
basename is a function of its name: split on whitespace, uppercase each
word's first letter, preserve the rest, concatenate. AGENTS.md adds
"never write a second derivation" -- yet there are two, because the
dashboard paints the red warning and names the expected directory
without a server round trip.

The pre-existing guard (``testStepSlugContract::
test_javascript_mirror_exists_and_is_exported``) asserts only that the
JS *source text* contains ``toUpperCase`` and ``slice(1)``. It never
compares outputs, so a mirror rewritten to lowercase the tail, drop
hyphens, or collapse repeated spaces would still pass while the
dashboard told the researcher a directory name the backend would never
create.

Three layers here, weakest to strongest:

1. the shared vector table pins the BACKEND's behaviour, including the
   contract's stated edge cases;
2. a normalised pin on the mirror's body fails on ANY edit to it, so a
   change cannot be made silently -- change-detection, not equivalence;
3. when a JavaScript runtime is present, the mirror is EXECUTED against
   the same vectors and compared to Python. That is real equivalence,
   and it is skipped rather than faked when no runtime exists.

Layer 3 is the only one that proves agreement. It is skipped on hosts
(and in CI) without node/deno/bun, which is precisely why layers 1 and
2 exist.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

from vaibify.gui.pipelineUtils import fsSlugFromStepName


_S_STATIC_DIRECTORY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


# (step name, expected directory basename). Cases marked [contract]
# are quoted directly from the c3a6cdd commit message.
LIST_SLUG_VECTORS = [
    ("Step Name", "StepName"),                    # [contract]
    ("Spectral Line Fit", "SpectralLineFit"),
    ("TOI-540 XUV", "TOI-540XUV"),                # [contract] hyphens
    ("Plot", "Plot"),
    ("plot results", "PlotResults"),
    ("MCMC Chain", "MCMCChain"),                  # interior case kept
    ("aB cD", "ABCD"),                            # only word-initial
    ("Run  Two   Spaces", "RunTwoSpaces"),        # runs collapse
    ("  Leading And Trailing  ", "LeadingAndTrailing"),
    ("Step 1", "Step1"),
    ("1st Pass", "1stPass"),                      # digit start is inert
    ("A", "A"),
    ("", ""),
]


def _fsReadMirrorSource():
    """Return the JS mirror module's source text."""
    sPath = os.path.join(_S_STATIC_DIRECTORY, "scriptUtilities.js")
    with open(sPath, "r") as fileHandle:
        return fileHandle.read()


def _fsExtractMirrorBody():
    """Return the mirror function's source, whitespace-normalised."""
    sSource = _fsReadMirrorSource()
    iStart = sSource.find("function fsSlugFromStepName")
    assert iStart != -1, "the JS mirror function is missing entirely"
    iEnd = sSource.find("\n    function ", iStart + 1)
    sBody = sSource[iStart:iEnd if iEnd != -1 else iStart + 500]
    return re.sub(r"\s+", " ", sBody).strip()


# The mirror as it stands. Any edit trips the pin, which is the point:
# the mirror may only change deliberately, alongside the backend.
_S_PINNED_MIRROR_BODY = (
    'function fsSlugFromStepName(sName) { return (sName || '
    '"").split(/\\s+/).filter(Boolean).map( function (sWord) { '
    'return sWord.charAt(0).toUpperCase() + sWord.slice(1); '
    '}).join(""); }'
)


def _fsFindJavascriptRuntime():
    """Return a usable JS runtime executable name, or '' when none."""
    for sCandidate in ("node", "deno", "bun"):
        if shutil.which(sCandidate):
            return sCandidate
    return ""


@pytest.mark.parametrize("sName,sExpected", LIST_SLUG_VECTORS)
def testBackendSlugMatchesTheContractVectors(sName, sExpected):
    """The backend is the authority; pin its behaviour explicitly."""
    assert fsSlugFromStepName(sName) == sExpected


@pytest.mark.falsification
def testJavascriptMirrorBodyMatchesItsPin():
    """Any edit to the mirror must be deliberate and visible.

    This does not prove the mirror agrees with Python -- only
    ``testJavascriptMirrorProducesIdenticalSlugs`` does that, and it
    needs a runtime. What this does guarantee is that the mirror
    cannot drift *silently*: whoever edits it must update this pin,
    and updating the pin is the moment to check the backend too.

    Kills: in vaibify/gui/static/scriptUtilities.js, change the
    mirror's ``sWord.slice(1)`` to ``sWord.slice(1).toLowerCase()`` --
    the drift that would make the dashboard name a directory the
    backend never creates.
    """
    assert _fsExtractMirrorBody() == _S_PINNED_MIRROR_BODY, (
        "the JS slug mirror changed. Confirm it still agrees with "
        "pipelineUtils.fsSlugFromStepName for every vector in "
        "LIST_SLUG_VECTORS, then update _S_PINNED_MIRROR_BODY."
    )


@pytest.mark.skipif(
    not _fsFindJavascriptRuntime(),
    reason="no node/deno/bun on this host; the pin above is the "
           "fallback guard and proves change-detection, not agreement",
)
def testJavascriptMirrorProducesIdenticalSlugs():
    """Execute the mirror and compare its output to the backend's.

    The only layer that proves the two derivations agree. Runs the
    real mirror source, so a rewrite that keeps the shape but changes
    the result is caught.
    """
    sRuntime = _fsFindJavascriptRuntime()
    sMirror = _fsExtractMirrorBody().replace(
        "function fsSlugFromStepName", "function fsSlug", 1,
    )
    sProgram = (
        sMirror
        + "\nconst listNames = "
        + json.dumps([sName for sName, _ in LIST_SLUG_VECTORS])
        + ";\nconsole.log(JSON.stringify(listNames.map(fsSlug)));"
    )
    resultProcess = subprocess.run(
        [sRuntime, "-e", sProgram] if sRuntime == "node"
        else [sRuntime, "eval", sProgram],
        capture_output=True, text=True, timeout=30,
    )
    assert resultProcess.returncode == 0, (
        f"{sRuntime} failed to run the mirror: {resultProcess.stderr}"
    )
    listActual = json.loads(resultProcess.stdout.strip())
    listExpected = [
        fsSlugFromStepName(sName) for sName, _ in LIST_SLUG_VECTORS
    ]
    listDisagreements = [
        (sName, sFromJs, sFromPython)
        for (sName, _), sFromJs, sFromPython
        in zip(LIST_SLUG_VECTORS, listActual, listExpected)
        if sFromJs != sFromPython
    ]
    assert listDisagreements == [], (
        "the JS mirror and the backend disagree: "
        + "; ".join(
            f"{sName!r} -> js={sJs!r} python={sPy!r}"
            for sName, sJs, sPy in listDisagreements
        )
    )
