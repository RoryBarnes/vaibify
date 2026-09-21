"""The residue sweep runs on a destructive path, so it is tested adversarially.

Deleting an environment now also removes the build contexts and the
build-argument hash it left on the host. The researcher's own
requirement for that change was the whole specification: *make sure
the extra wiping never extends beyond container X* (2026-09-21).

So these tests are written to BREAK the scoping, not to confirm it.
Every one of them sets up a neighbour that a careless implementation
would take: a project whose name is a prefix of another, a name
carrying regex metacharacters, a name that is a path traversal, a
symlink pointing outside the tree. The positive case is asserted too,
because a sweep that removed nothing would pass every negative test
here and be useless.

Nothing in this file touches the real ``~/.vaibify``: both roots are
redirected at the module's own constants.
"""

import os
import pathlib

import pytest

from vaibify.config import hostResidue


S_SUFFIX_A = "2it2ks98"
S_SUFFIX_B = "gprx__cw"


@pytest.fixture
def fnBuildResidueTree(tmp_path, monkeypatch):
    """Redirect both residue roots into tmp_path; return a maker."""
    pathBuild = tmp_path / "build"
    pathCache = tmp_path / "cache"
    pathBuild.mkdir()
    pathCache.mkdir()
    monkeypatch.setattr(
        hostResidue, "S_BUILD_CONTEXT_ROOT", str(pathBuild))
    monkeypatch.setattr(
        hostResidue, "S_BUILD_HASH_ROOT", str(pathCache))

    def fnMake(listContextNames=(), listHashNames=()):
        for sName in listContextNames:
            pathContext = pathBuild / sName
            pathContext.mkdir()
            (pathContext / "Dockerfile").write_text("FROM scratch\n")
        for sName in listHashNames:
            (pathCache / sName).write_text("hash\n")
        return pathBuild, pathCache

    return fnMake


@pytest.mark.falsification
def testAProjectWhoseNameIsAPrefixOfAnotherIsNeverTouched(
    fnBuildResidueTree,
):
    """``fillet`` must not sweep ``fillet-extra``.

    The independent oracle is the staging function's own naming:
    ``fsStageBuildContext`` appends ``-`` plus exactly eight mkdtemp
    characters, so ``fillet-extra-2it2ks98`` is ``fillet-extra``'s
    context and not ``fillet``'s -- the remainder after ``fillet-`` is
    fourteen characters, not eight. A prefix match cannot tell those
    apart, and it is the obvious implementation.

    This is the failure the researcher named when authorising the
    change, and it destroys another environment's build state.

    Kills: matching a staged context by prefix instead of by the exact
    mkdtemp suffix (relaxing ``_REGEX_MKDTEMP_SUFFIX``).
    """
    pathBuild, pathCache = fnBuildResidueTree(
        listContextNames=[
            f"fillet-{S_SUFFIX_A}",
            f"fillet-extra-{S_SUFFIX_A}",
            f"fillet-extra-{S_SUFFIX_B}",
        ],
        listHashNames=["fillet-arg-hash", "fillet-extra-arg-hash"],
    )
    # THE NEIGHBOUR IS NOT REGISTERED, and that is the point. Rule 2
    # (the registry cross-check) would rescue a registered neighbour
    # and hide a broken rule 1 -- the defence-in-depth trap this
    # repository records: disabling either copy alone changes nothing
    # a caller can observe. It is also the real case: "Remove from
    # list" un-registers a project and KEEPS its bytes, so an
    # un-registered fillet-extra still has contexts on disk, and
    # deleting fillet must not take them.
    listRemoved = hostResidue.flistRemoveResidueForProject(
        "fillet", ["fillet"],
    )
    assert sorted(os.path.basename(s) for s in listRemoved) == [
        f"fillet-{S_SUFFIX_A}", "fillet-arg-hash",
    ]
    assert (pathBuild / f"fillet-extra-{S_SUFFIX_A}").is_dir()
    assert (pathBuild / f"fillet-extra-{S_SUFFIX_B}").is_dir()
    assert (pathCache / "fillet-extra-arg-hash").is_file()


@pytest.mark.falsification
def testARegisteredNeighbourWinsEvenIfTheNamingRuleWouldNot(
    fnBuildResidueTree,
):
    """The registry cross-check is the second line, and it must hold alone.

    Rule 1 (the exact mkdtemp suffix) and rule 2 (no path a registered
    neighbour could own) are deliberately redundant. The oracle for
    keeping both is the cost of being wrong: a mistake here deletes
    somebody else's environment, so the check is made from the
    registry rather than from reasoning about names.

    Here the neighbour's own name ends in something the suffix rule
    would accept for the shorter project, so only the cross-check can
    save it.

    Kills: dropping the registered-neighbour check from
    ``flistDescribeResidueForProject``.
    """
    pathBuild, _ = fnBuildResidueTree(
        listContextNames=[f"demo-{S_SUFFIX_A}"],
    )
    # "demo-2it2ks98" is a legal staged-context name for "demo" -- and
    # also a registered project in its own right.
    listRemoved = hostResidue.flistRemoveResidueForProject(
        "demo", ["demo", f"demo-{S_SUFFIX_A}"],
    )
    assert listRemoved == [], (
        "a directory a registered project could own must be left alone"
    )
    assert (pathBuild / f"demo-{S_SUFFIX_A}").is_dir()


@pytest.mark.falsification
def testAHostileProjectNameReachesNothing(fnBuildResidueTree):
    """A name is never joined blindly onto a root.

    The oracle is this repository's standing path rule: any path that
    originated from a user-facing source is validated against its
    intended root before being opened. A project name comes from a
    registry entry the researcher wrote, so it is such a source.

    The realistic error is not exotic: the obvious implementation
    builds the hash path straight from the name
    (``join(root, name + "-arg-hash")``) instead of matching it
    against a listing of the directory. Do that and a project named
    ``../../../etc`` addresses a path outside the tree. The root check
    is what stands between the two.

    Kills: composing a residue path by string join from the project
    name rather than selecting it from a listing of the root.
    """
    pathBuild, pathCache = fnBuildResidueTree(
        listContextNames=[f"fillet-{S_SUFFIX_A}"],
        listHashNames=["fillet-arg-hash"],
    )
    for sHostileName in (
        "../../../etc", "..", ".", "", "fillet/../vaibify-vplanet",
    ):
        assert hostResidue.flistRemoveResidueForProject(
            sHostileName, ["fillet"],
        ) == [], f"{sHostileName!r} reached something"
    assert (pathBuild / f"fillet-{S_SUFFIX_A}").is_dir()
    assert (pathCache / "fillet-arg-hash").is_file()


def testANameCarryingRegexMetacharactersMatchesLiterally(
    fnBuildResidueTree,
):
    """``a.b`` must not match ``axb``; the prefix is compared, never compiled."""
    pathBuild, _ = fnBuildResidueTree(
        listContextNames=[f"axb-{S_SUFFIX_A}", f"a.b-{S_SUFFIX_A}"],
    )
    listRemoved = hostResidue.flistRemoveResidueForProject("a.b", ["a.b"])
    assert [os.path.basename(s) for s in listRemoved] == [
        f"a.b-{S_SUFFIX_A}",
    ]
    assert (pathBuild / f"axb-{S_SUFFIX_A}").is_dir()


@pytest.mark.falsification
def testASymlinkIsUnlinkedRatherThanFollowed(fnBuildResidueTree, tmp_path):
    """Removing a link must not remove what it points at.

    The oracle is what ``shutil.rmtree`` does to a symlinked directory
    versus what ``os.remove`` does: one would descend into the
    researcher's own project directory and delete its contents, the
    other unlinks the pointer. A residue sweep that followed a link
    could empty a git repository that has nothing to do with Docker.

    Kills: removing every candidate with ``shutil.rmtree`` without
    first asking whether it is a link.
    """
    pathBuild, _ = fnBuildResidueTree()
    pathPrecious = tmp_path / "researcherRepository"
    pathPrecious.mkdir()
    (pathPrecious / "results.csv").write_text("do not delete\n")
    pathLink = pathBuild / f"fillet-{S_SUFFIX_A}"
    pathLink.symlink_to(pathPrecious, target_is_directory=True)

    listRemoved = hostResidue.flistRemoveResidueForProject(
        "fillet", ["fillet"],
    )
    assert [os.path.basename(s) for s in listRemoved] == [
        f"fillet-{S_SUFFIX_A}",
    ]
    assert not pathLink.exists()
    assert (pathPrecious / "results.csv").read_text() == "do not delete\n", (
        "the sweep followed a symlink out of its own tree"
    )


def testAnEntryThatIsNotAStagedContextIsIgnored(fnBuildResidueTree):
    """Only mkdtemp's own shape counts; anything else is somebody's file."""
    pathBuild, _ = fnBuildResidueTree(
        listContextNames=[
            "fillet",                    # no suffix at all
            "fillet-short",              # too short
            "fillet-waytoolongsuffix",   # too long
            "fillet-has-dash",           # not mkdtemp's alphabet
        ],
    )
    assert hostResidue.flistRemoveResidueForProject(
        "fillet", ["fillet"],
    ) == []
    assert len(list(pathBuild.iterdir())) == 4


def testTheDescriptionAndTheRemovalAgree(fnBuildResidueTree):
    """What a surface reports and what the delete removes are one list."""
    fnBuildResidueTree(
        listContextNames=[f"fillet-{S_SUFFIX_A}"],
        listHashNames=["fillet-arg-hash"],
    )
    listDescribed = hostResidue.flistDescribeResidueForProject(
        "fillet", ["fillet"],
    )
    listRemoved = hostResidue.flistRemoveResidueForProject(
        "fillet", ["fillet"],
    )
    assert listDescribed == listRemoved


@pytest.mark.falsification
def testOrphanedResidueIsDescribedAndNeverRemoved():
    """"Remove from list" keeps every byte, so an orphan sweep would betray it.

    The oracle is a contract this codebase already states: "Remove from
    list" un-registers a project and leaves every byte where it was,
    deliberately. Residue whose project is absent from the registry is
    therefore exactly what a researcher may have chosen to keep, and a
    sweep keyed on "not registered" would delete it.

    Kills: giving ``hostResidue`` a function that removes orphaned
    residue, or having the delete path reach for one.
    """
    import inspect
    sSource = inspect.getsource(hostResidue)
    iOrphanDefinition = sSource.index("def fdictDescribeOrphanedResidue")
    sOrphanBody = sSource[iOrphanDefinition:]
    for sForbidden in ("rmtree", "os.remove", "unlink"):
        assert sForbidden not in sOrphanBody, (
            f"the orphan path must only describe; it reached for "
            f"{sForbidden}"
        )
    import re
    sDeletion = pathlib.Path(
        "vaibify/gui/environmentDeletion.py",
    ).read_text(encoding="utf-8")
    # Prose may use the word; CODE may not reach for the function.
    sCode = re.sub(r'"""[\s\S]*?"""', "", sDeletion)
    sCode = re.sub(r"^\s*#.*$", "", sCode, flags=re.M)
    assert "Orphan" not in sCode, (
        "deleting one environment must never reach for another's residue"
    )
