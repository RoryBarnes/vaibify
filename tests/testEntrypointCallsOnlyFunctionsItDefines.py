"""Every function the entrypoint calls must be one it defines.

``bash -n`` parses; it does not resolve names. So renaming a function
and missing one call site leaves a file that parses perfectly and dies
at container start with "command not found" — after the image is
built, on the researcher's machine, in a script no test executes.

That is not hypothetical. Splitting ``fnInstallRepoRequirements`` into
a mirror path and a legacy path left the single call site pointing at
the old name (2026-09-01). The file parsed, every existing entrypoint
test passed, and the failure would have surfaced as a broken start for
every container built from that image.

The check is deliberately narrow: vaibify's own ``fn``-prefixed
functions, which are the ones this repository renames. Shell builtins,
coreutils and anything the researcher's image adds are out of scope —
this is a rename guard, not a shell linter.
"""

import re

from pathlib import Path


PATH_ENTRYPOINT = (
    Path(__file__).resolve().parents[1]
    / "vaibify" / "containerImage" / "entrypoint.sh"
)

# A definition is "fnName() {" at the start of a line; a call is the
# bare name in command position. Both are anchored so a mention inside
# prose or a comment cannot be mistaken for either.
_S_DEFINITION = r"^\s*(fn[A-Za-z0-9_]*)\s*\(\)\s*\{"
_S_INVOCATION = r"^\s*(?:if\s+!?\s*|&&\s*|\|\|\s*)?(fn[A-Za-z0-9_]*)\b"


def _fsReadEntrypoint():
    return PATH_ENTRYPOINT.read_text(encoding="utf-8")


def _fsetDefinedFunctions(sSource):
    return {
        matchLine.group(1)
        for matchLine in re.finditer(_S_DEFINITION, sSource, re.MULTILINE)
    }


def _fsetInvokedFunctions(sSource, setDefined):
    """Return the fn-names used in command position.

    A definition line matches the invocation pattern too, so
    definitions are excluded by their own shape rather than by
    subtracting the defined set — subtracting it would make the whole
    check vacuous.
    """
    setInvoked = set()
    for sLine in sSource.splitlines():
        if re.match(_S_DEFINITION, sLine):
            continue
        matchCall = re.match(_S_INVOCATION, sLine)
        if matchCall:
            setInvoked.add(matchCall.group(1))
    del setDefined
    return setInvoked


def test_every_called_entrypoint_function_is_defined():
    """A renamed function with a missed call site dies at container start."""
    sSource = _fsReadEntrypoint()
    setDefined = _fsetDefinedFunctions(sSource)
    setInvoked = _fsetInvokedFunctions(sSource, setDefined)
    setMissing = setInvoked - setDefined
    assert not setMissing, (
        "entrypoint.sh calls functions it does not define, which parses "
        f"cleanly and fails at container start: {sorted(setMissing)}"
    )


def test_the_check_can_actually_see_a_missing_definition():
    """Otherwise the assertion above passes on any file at all.

    The regexes are the fragile part: one that matched no invocations
    would make the guard vacuous and silent, which is worse than not
    having it.
    """
    sBroken = _fsReadEntrypoint() + "\nfnNoSuchFunctionDefinedAnywhere\n"
    setDefined = _fsetDefinedFunctions(sBroken)
    setInvoked = _fsetInvokedFunctions(sBroken, setDefined)
    assert "fnNoSuchFunctionDefinedAnywhere" in setInvoked - setDefined


def test_the_mirror_entry_point_is_the_one_wired_in():
    """The rename that prompted this: mirror, not the legacy installer.

    The legacy path is reachable only from inside the mirror function,
    for images built before the package list was baked in.
    """
    sSource = _fsReadEntrypoint()
    setInvoked = _fsetInvokedFunctions(
        sSource, _fsetDefinedFunctions(sSource),
    )
    assert "fnMirrorRepoRequirements" in setInvoked
