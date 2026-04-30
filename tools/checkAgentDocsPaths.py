"""Fail CI if any AGENTS.md references a path that no longer exists."""

import re
import sys
from pathlib import Path


__all__ = [
    "fnMain",
    "flistFindAgentsFiles",
    "flistExtractPaths",
    "flistExtractBareFilenames",
]


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = REPO_ROOT / ".claude" / "skills"

REGEX_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
REGEX_SLASHED_PATH = re.compile(
    r"`([A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+\.(?:py|md|js|rst|json|yml|yaml|ts|txt|ini|cfg|toml))`"
)
REGEX_BARE_FILENAME = re.compile(
    r"`([A-Za-z0-9_-]+\.(?:py|md|js|rst|json|yml|yaml|ts|txt|ini|cfg|toml))`"
)
SET_SCHEME_PREFIXES = ("http://", "https://", "mailto:", "#")
# Generic/illustrative filenames used in templates and examples that are not
# meant to resolve to a concrete file on disk.
SET_GENERIC_FILENAME_EXAMPLES = {
    "Routes.py",
    "metricsRoutes.py",
    "userRoutes.py",
}


def flistFindAgentsFiles(pathRoot):
    """Return every AGENTS.md and SKILL.md under the repo."""
    listResults = sorted(pathRoot.rglob("AGENTS.md"))
    if SKILL_ROOT.exists():
        listResults.extend(sorted(SKILL_ROOT.rglob("SKILL.md")))
    return [p for p in listResults if ".git" not in p.parts]


def flistExtractPaths(sContent):
    """Return a list of relative paths referenced in a doc."""
    listRaw = REGEX_MD_LINK.findall(sContent)
    listRaw.extend(REGEX_SLASHED_PATH.findall(sContent))
    return [
        s.split("#", 1)[0]
        for s in listRaw
        if not s.startswith(SET_SCHEME_PREFIXES) and "://" not in s
    ]


def flistExtractBareFilenames(sContent):
    """Return backticked bare filenames (no slash) that look like file refs."""
    listRaw = REGEX_BARE_FILENAME.findall(sContent)
    return [s for s in listRaw if s not in SET_GENERIC_FILENAME_EXAMPLES]


def fbReferenceResolves(pathDoc, sReference):
    """Return True when sReference resolves relative to the doc or repo root."""
    if sReference.startswith("/"):
        return (REPO_ROOT / sReference.lstrip("/")).exists()
    if (pathDoc.parent / sReference).resolve().exists():
        return True
    return (REPO_ROOT / sReference).exists()


def fbBareFilenameResolves(pathDoc, sFilename):
    """Return True when sFilename exists anywhere searchable under the repo."""
    if (pathDoc.parent / sFilename).exists():
        return True
    if (REPO_ROOT / sFilename).exists():
        return True
    for pathMatch in REPO_ROOT.rglob(sFilename):
        if ".git" in pathMatch.parts:
            continue
        return True
    return False


def flistBrokenReferences(pathDoc):
    """Return the list of broken path references found in pathDoc."""
    sContent = pathDoc.read_text(encoding="utf-8")
    listBroken = []
    for sRef in flistExtractPaths(sContent):
        if not fbReferenceResolves(pathDoc, sRef):
            listBroken.append(sRef)
    for sRef in flistExtractBareFilenames(sContent):
        if not fbBareFilenameResolves(pathDoc, sRef):
            listBroken.append(sRef)
    return listBroken


def fnReportAndExit(listFailures):
    """Print any failures and exit with status 1 if non-empty."""
    if not listFailures:
        print("All AGENTS.md and SKILL.md path references resolve.")
        return 0
    print("Broken path references:")
    for pathDoc, sRef in listFailures:
        sRelative = pathDoc.relative_to(REPO_ROOT)
        print(f"  {sRelative}: {sRef}")
    return 1


def fnMain():
    """Entry point invoked by CI."""
    listDocs = flistFindAgentsFiles(REPO_ROOT)
    listFailures = []
    for pathDoc in listDocs:
        for sRef in flistBrokenReferences(pathDoc):
            listFailures.append((pathDoc, sRef))
    return fnReportAndExit(listFailures)


if __name__ == "__main__":
    sys.exit(fnMain())
