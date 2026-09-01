"""Compare what a project DECLARES against what its image was built with.

Dependencies are declared in one place — ``pythonPackages`` in the
project's ``vaibify.yml`` (2026-09-01 ruling). The image is built from
that list, the container's entrypoint mirrors the list into each
repository's ``.vaibify/requirements.txt``, and PROOF Level 3 compiles
``requirements.lock`` from the mirror. So the declaration, the image
and the lock agree by construction.

By construction is not the same as verified. One link still breaks
silently and it is the one a researcher hits: **edit vaibify.yml and
do not rebuild.** The declaration then names a package the image does
not have, every downstream artefact still agrees with the image, and
nothing says so. That is the shape of the failure this module exists
to catch — a project declared ``pytest``, the image predated the
declaration by two days, and the wall it produced took six rounds to
diagnose.

The comparison is by package NAME, deliberately, not by the full
requirement string. A researcher who loosens ``numpy>=1.26`` to
``numpy>=1.24`` has not made their image stale — the installed numpy
still satisfies both — whereas adding or removing a name always has.
Comparing whole lines would fire on every version-bound edit and be
switched off within a week.
"""

import re


__all__ = [
    "fdictComparePackageDeclarations",
    "flistParseRequirementNames",
]


# A requirement line's package name ends at the first version
# specifier, extra, marker, or whitespace. Editable installs and URLs
# have no name to compare, so they are skipped rather than guessed at:
# a wrong name here would report a divergence that does not exist.
_S_NAME_PATTERN = r"^([A-Za-z0-9][A-Za-z0-9._-]*)"
_T_URL_MARKERS = ("://", "@ ", "-e ", "--")


def flistParseRequirementNames(sText):
    """Return the normalized package names a requirements text declares.

    Comments, blank lines and pip flag lines are dropped. Names are
    lowercased with underscores folded to hyphens, which is PyPA's own
    normalization — ``Foo_Bar`` and ``foo-bar`` are one package, and
    treating them as two would report a divergence between a file and
    a faithful copy of itself.
    """
    listNames = []
    for sLine in (sText or "").splitlines():
        sStripped = sLine.split("#", 1)[0].strip()
        if not sStripped:
            continue
        if any(sMarker in sStripped for sMarker in _T_URL_MARKERS):
            continue
        matchName = re.match(_S_NAME_PATTERN, sStripped)
        if not matchName:
            continue
        sName = matchName.group(1).lower().replace("_", "-")
        if sName not in listNames:
            listNames.append(sName)
    return listNames


def fdictComparePackageDeclarations(listDeclared, sImageText):
    """Return how the declared package set differs from the image's.

    ``listDeclared`` is ``vaibify.yml``'s ``pythonPackages``;
    ``sImageText`` is the mirror the image wrote into the repository,
    which IS the image's own list. ``bChecked`` is False when either
    side is unavailable — an unreadable mirror means the question was
    not answered, and reporting that as "they match" is the failure
    mode this whole area keeps producing.
    """
    if not listDeclared or sImageText is None:
        return {
            "bChecked": False, "bMatches": True,
            "listMissingFromImage": [], "listExtraInImage": [],
        }
    listDeclaredNames = flistParseRequirementNames("\n".join(listDeclared))
    listImageNames = flistParseRequirementNames(sImageText)
    setImage = set(listImageNames)
    setDeclared = set(listDeclaredNames)
    listMissing = [s for s in listDeclaredNames if s not in setImage]
    listExtra = [s for s in listImageNames if s not in setDeclared]
    return {
        "bChecked": True,
        "bMatches": not listMissing and not listExtra,
        "listMissingFromImage": listMissing,
        "listExtraInImage": listExtra,
    }
