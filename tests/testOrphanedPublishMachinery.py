"""Keep the publish story and the code telling the same story.

`docs/cli.md` claimed the publishing machinery was "already available
through the GUI's Settings → Publish pane". No such pane exists, and
`vaibify/reproducibility/githubWorkflow.py` -- the GitHub Actions
generator behind the claim -- has no caller in the product. A reader
budgeting on that sentence would look for a feature that was never
built.

The module is kept rather than deleted because wiring it expands
remote-execution surface, which is the maintainer's call. What must not
happen again is the docs and the code disagreeing about which it is.
So these tests fail in BOTH directions: if the docs re-advertise it
while it is unreachable, and if it gains a caller while its docstring
still says it has none.
"""

import pathlib

import pytest

from vaibify.reproducibility import githubWorkflow


_PATH_REPO = pathlib.Path(__file__).resolve().parent.parent
_T_PRODUCT_ROOTS = ("vaibify", "bin")


def _flistProductImporters():
    """Return product files that import the workflow generator."""
    listImporters = []
    for sRoot in _T_PRODUCT_ROOTS:
        pathRoot = _PATH_REPO / sRoot
        if not pathRoot.is_dir():
            continue
        for pathFile in pathRoot.rglob("*.py"):
            if pathFile.name == "githubWorkflow.py":
                continue
            sText = pathFile.read_text(encoding="utf-8", errors="ignore")
            if "githubWorkflow" in sText:
                listImporters.append(
                    pathFile.relative_to(_PATH_REPO).as_posix()
                )
    return listImporters


def testDocsDoNotAdvertiseAPublishPaneThatDoesNotExist():
    """The GUI has no Publish pane; the CLI docs must not claim one."""
    sDoc = (_PATH_REPO / "docs" / "cli.md").read_text()
    # The corrected text mentions the pane in order to deny it, so the
    # forbidden thing is the CLAIM, not the words.
    assert "already available through the GUI" not in sDoc, (
        "docs/cli.md advertises publishing machinery as available "
        "through the GUI. No Publish pane exists in the frontend."
    )


def testPublishPaneIsAbsentFromTheFrontend():
    """Pins the fact the doc claim was false, so it stays checkable."""
    pathStatic = _PATH_REPO / "vaibify" / "gui" / "static"
    listHits = [
        pathFile.name for pathFile in pathStatic.rglob("*.js")
        if "PublishPane" in pathFile.read_text(
            encoding="utf-8", errors="ignore",
        )
    ]
    assert not listHits, (
        "A Publish pane now exists in " + ", ".join(listHits)
        + " -- update docs/cli.md and this test together."
    )


@pytest.mark.falsification
def testUnreachableGeneratorSaysSoOrGainsACaller():
    """Kills: the module gaining a caller while still marked dead.

    Mutation: import ``githubWorkflow`` from a product module without
    removing the UNREACHABLE note. Whichever way this changes, the
    docstring and the call graph must move together -- a module
    documented as dead but wired into a live path is worse than either
    state alone.
    """
    listImporters = _flistProductImporters()
    bDocumentedUnreachable = "UNREACHABLE" in (
        githubWorkflow.__doc__ or ""
    )
    if listImporters:
        assert not bDocumentedUnreachable, (
            "githubWorkflow is imported by "
            f"{listImporters} but its docstring still says "
            "UNREACHABLE. Remove the note and document the feature."
        )
        return
    assert bDocumentedUnreachable, (
        "githubWorkflow has no product caller. Say so in its "
        "docstring, or wire it and document the feature."
    )
