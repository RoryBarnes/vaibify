"""Links into the published docs site must name a page that exists.

The README pointed readers at ``.../vaibify/QuickStart.html``. The
Sphinx source is ``docs/quickStart.md``, so the published page is
``quickStart.html`` and GitHub Pages is case-sensitive: the first link
in the README — the one a new user follows before anything else — was a
404 for as long as it had existed.

Nothing could catch it. Sphinx only validates links *within* the doc
tree, and these are absolute URLs into the built site, so they are
opaque strings to every existing check. This test resolves them back to
their source pages instead.
"""

import re
from pathlib import Path

import pytest


_PATH_REPO = Path(__file__).resolve().parent.parent
_PATH_DOCS = _PATH_REPO / "docs"

# Absolute links into the built documentation site, e.g.
# https://<owner>.github.io/vaibify/quickStart.html
_PATTERN_PUBLISHED_LINK = re.compile(
    r"https://[\w.-]+\.github\.io/vaibify/([\w./-]+)\.html"
)

# Pages Sphinx generates that have no same-named source file.
_SET_GENERATED_PAGES = frozenset({"genindex", "search", "index"})


def _flistMarkdownSources():
    """Return every prose file that may carry a published-site link."""
    return [_PATH_REPO / "README.md"] + sorted(
        list(_PATH_DOCS.glob("*.md")) + list(_PATH_DOCS.glob("*.rst"))
    )


def _fsetDocsSourceStems():
    """Return the exact-case stems of every docs source file.

    Read from the directory listing rather than probed with
    ``is_file()``: macOS is case-insensitive, so a probe for
    ``QuickStart.md`` finds ``quickStart.md`` and reports that a
    404-on-GitHub-Pages link is fine. The bug this test exists for
    would be invisible on the machine most likely to introduce it.
    """
    return {
        pathItem.stem for pathItem in _PATH_DOCS.iterdir()
        if pathItem.suffix in (".md", ".rst")
    }


def _fbPageHasSource(sPage):
    """Return True when a built page name maps to a docs source file."""
    if sPage in _SET_GENERATED_PAGES:
        return True
    return sPage in _fsetDocsSourceStems()


@pytest.mark.parametrize(
    "pathSource", _flistMarkdownSources(), ids=lambda p: p.name,
)
def testEveryPublishedLinkNamesAPageThatExists(pathSource):
    """A link into the built site must resolve to a docs source page.

    Case matters: the site is served by GitHub Pages, so ``QuickStart``
    and ``quickStart`` are different URLs and only one of them is real.
    """
    sText = pathSource.read_text(encoding="utf-8")
    listBroken = [
        sPage for sPage in _PATTERN_PUBLISHED_LINK.findall(sText)
        if not _fbPageHasSource(sPage)
    ]
    assert not listBroken, (
        f"{pathSource.name} links to published pages with no source in "
        f"docs/: {listBroken}. GitHub Pages is case-sensitive, so check "
        f"the capitalisation of the source filename."
    )
