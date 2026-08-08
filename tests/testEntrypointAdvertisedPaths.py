"""What the agent guide advertises must be something the image stages.

The generated in-container `CLAUDE.md` told the agent:

    - `/workspace/.vaibify/director.py` — Standalone pipeline executor
    Run a project: `python /workspace/.vaibify/director.py --config ...`

`director.py` could not start. It carries package-relative imports and
the image staged none of its siblings, so every invocation -- `--help`
included -- died with `ImportError: attempted relative import with no
known parent package`. The file was copied verbatim, installed, and
never once executed.

**That is worse than a missing feature.** An agent following its own
instructions was sent to a command that cannot run, in a document whose
whole purpose is to tell it what is true about its environment. It is
the same failure as the `vaibify-doc-map` skill advertising documents a
wheel-built image did not carry, and the same lesson as the shipped
template that invoked scripts existing nowhere in the repository: an
artifact nobody executed is not an artifact.

So this file tests classes, not just the instance -- but the two
classes are different, and conflating them would leave the real one
uncovered:

**Advertised but ABSENT.** Every container path the guide names under
the image's own directories must be produced by the Dockerfile or the
entrypoint. This is the `vaibify-doc-map` failure. Ordinary workspace
content -- a researcher's repositories, project files, a log directory
-- is theirs, and is out of scope.

**Advertised, PRESENT, and non-functional.** This is the director
failure, and the absence rule above does NOT catch it: checked against
a reconstruction of the pre-fix sources, the path was correctly
reported as produced, because it WAS produced. It simply could not run.
What catches this class is the convention already stated in
``commandBuild.fnCopyContainerScripts`` -- a module staged into the
image runs at ``/usr/share/vaibify/`` with no vaibify package around
it, so it must import as flat top-level names. A package-relative
import in a staged file is an ImportError at first execution, every
time, and is visible without running anything.
"""

import os
import re

import pytest


_S_CONTAINER_IMAGE_DIRECTORY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "containerImage",
)
_S_ENTRYPOINT = os.path.join(_S_CONTAINER_IMAGE_DIRECTORY, "entrypoint.sh")
_S_DOCKERFILE = os.path.join(_S_CONTAINER_IMAGE_DIRECTORY, "Dockerfile")

# Paths the image owns. A guide reference under one of these is a claim
# about something the BUILD produced, so the build has to have produced
# it. `/workspace` is deliberately absent: its contents are the
# researcher's, created at runtime.
_T_IMAGE_OWNED_ROOTS = ("/usr/share/vaibify",)

# The one workspace path the image itself installs into, and therefore
# the one it may be held to.
_S_WORKSPACE_VAIBIFY = "/workspace/.vaibify"

_RE_ADVERTISED_PATH = re.compile(r"`(/(?:usr/share/vaibify|workspace)[^`\s]*)`")


_S_GUIDE_HEREDOC_MARKER = "CLAUDEMD"


@pytest.fixture(scope="module")
def sEntrypointSource():
    with open(_S_ENTRYPOINT, "r", encoding="utf-8") as fileEntrypoint:
        return fileEntrypoint.read()


@pytest.fixture(scope="module")
def sGeneratedGuide(sEntrypointSource):
    """Return only the text written into the container's CLAUDE.md.

    Scoped to the heredoc rather than the whole script, because the
    script legitimately NAMES a withdrawn path in order to sweep a
    stale copy of it and to explain why. What must not reappear is the
    ADVERTISEMENT -- the guide telling the agent the thing is there.
    """
    listLines = sEntrypointSource.splitlines()
    listGuide = []
    bInside = False
    for sLine in listLines:
        if not bInside and sLine.strip().endswith(
            f"<< '{_S_GUIDE_HEREDOC_MARKER}'",
        ):
            bInside = True
            continue
        if bInside and sLine.strip() == _S_GUIDE_HEREDOC_MARKER:
            break
        if bInside:
            listGuide.append(sLine)
    assert listGuide, "the generated agent guide heredoc was not found"
    return "\n".join(listGuide)


@pytest.fixture(scope="module")
def sDockerfileSource():
    with open(_S_DOCKERFILE, "r", encoding="utf-8") as fileDockerfile:
        return fileDockerfile.read()


@pytest.mark.falsification
def testTheGuideDoesNotAdvertiseTheWithdrawnDirector(sGeneratedGuide):
    """The specific lie, pinned so it cannot come back by copy-paste.

    Kills: restoring the `/workspace/.vaibify/director.py` line to the
    generated agent guide in entrypoint.sh.
    """
    assert "director.py" not in sGeneratedGuide, (
        "the generated agent guide names director.py again. It cannot "
        "start (package-relative imports, no siblings staged), and the "
        "guide is the agent's ground truth about its environment."
    )


def testTheImageDoesNotStageTheWithdrawnDirector(sDockerfileSource):
    """The Dockerfile must not COPY what the build no longer stages.

    A `COPY director.py` with nothing writing it into the context fails
    the build outright, so this also guards the reverse mistake.
    """
    assert "director.py" not in sDockerfileSource, (
        "the Dockerfile COPYs director.py, which vaibify build no "
        "longer stages into the context"
    )


def testEveryAdvertisedImagePathIsProducedByTheBuild(
    sGeneratedGuide, sEntrypointSource, sDockerfileSource,
):
    """No guide reference to an image-owned path without something making it.

    The ABSENCE class -- the `vaibify-doc-map` failure, not the director
    one. A path under ``/usr/share/vaibify`` exists only if the
    Dockerfile COPYs or creates it; a path under ``/workspace/.vaibify``
    exists only if the entrypoint creates it. An advertised path with no
    producer is a claim the image cannot honour.

    Stated so nobody reads this as more than it is: run against a
    reconstruction of the pre-fix sources, this check PASSES on
    director.py, because the image really did stage it. Presence was
    never the problem there. ``testEveryStagedModuleImportsAsAFlatName``
    is the one that covers that.
    """
    listAdvertised = _flistAdvertisedPaths(sGeneratedGuide)
    assert listAdvertised, (
        "no advertised paths found -- the extraction pattern has "
        "drifted from the guide's formatting and is proving nothing"
    )
    listUnproduced = [
        sPath for sPath in listAdvertised
        if not _fbPathIsProduced(sPath, sEntrypointSource, sDockerfileSource)
    ]
    assert listUnproduced == [], (
        f"the agent guide advertises paths the image never creates: "
        f"{listUnproduced}"
    )


def _flistAdvertisedPaths(sGeneratedGuide):
    """Return the image-owned container paths the guide names in backticks."""
    setPaths = set()
    for sMatch in _RE_ADVERTISED_PATH.findall(sGeneratedGuide):
        sPath = sMatch.rstrip("/.,")
        if sPath.startswith(_T_IMAGE_OWNED_ROOTS):
            setPaths.add(sPath)
        elif sPath.startswith(_S_WORKSPACE_VAIBIFY + "/"):
            setPaths.add(sPath)
    return sorted(setPaths)


def _fbPathIsProduced(sPath, sEntrypointSource, sDockerfileSource):
    """True when some build step creates, copies, or writes the path.

    Deliberately generous about HOW -- a COPY, an mkdir, a redirect, a
    variable-spelled reference. The check being defended is that
    something in the build names it at all; a stricter reading would
    fail on ordinary shell indirection and teach people to append to an
    exemption list.
    """
    sBasename = os.path.basename(sPath)
    sWorkspaceRelative = sPath.replace(
        _S_WORKSPACE_VAIBIFY, '${WORKSPACE}/.vaibify',
    )
    for sHaystack in (sDockerfileSource, sEntrypointSource):
        for sNeedle in (sPath, sWorkspaceRelative, sBasename):
            if sHaystack.count(sNeedle) > 1:
                return True
    return False


@pytest.mark.falsification
def testEveryStagedModuleImportsAsAFlatName():
    """A module the image installs must run without the vaibify package.

    THE director class, and the one the absence check above cannot see.
    Staged modules live at ``/usr/share/vaibify/`` in a container with
    no vaibify install, so a package-relative import (``from .x import
    y``, ``from . import x``) raises ImportError at the first
    execution -- before argument parsing, so even ``--help`` dies.
    ``commandBuild.fnCopyContainerScripts`` already states the flat-name
    convention in prose; this is the check that the shipped files obey
    it.

    Sources are read from the package rather than from an assembled
    context so the check runs anywhere, with no Docker and no build.

    Kills: staging a module with a package-relative import, e.g.
    restoring director.py to T_CONTAINER_SCRIPT_SOURCES.
    """
    listOffenders = []
    for sName, sPath in _tlistStagedModuleSources():
        listRelative = _flistRelativeImports(sPath)
        if listRelative:
            listOffenders.append(f"{sName}: {', '.join(listRelative)}")
    assert listOffenders == [], (
        f"these modules are staged into the image but import as though "
        f"a vaibify package were installed beside them, so they raise "
        f"ImportError on first execution: {listOffenders}"
    )


def _tlistStagedModuleSources():
    """Return (name, path) for every Python file the build stages."""
    from vaibify.cli.commandBuild import T_CONTAINER_SCRIPT_SOURCES
    sPackageRoot = os.path.dirname(_S_CONTAINER_IMAGE_DIRECTORY)
    listSources = [
        (sName, os.path.join(sPackageRoot, "reproducibility", sName))
        for sName in T_CONTAINER_SCRIPT_SOURCES
    ]
    listSources.append(
        ("vaibifyDo.py",
         os.path.join(_S_CONTAINER_IMAGE_DIRECTORY, "vaibifyDo.py")),
    )
    return [(sName, sPath) for sName, sPath in listSources
            if os.path.isfile(sPath)]


@pytest.mark.falsification
def testEveryStagedModuleActuallyImports(tmp_path):
    """Assemble what the image receives, and import it from there.

    The relative-import check above is a source-shape fnTestCommand, and a
    source-shape test proves what it inspects and nothing else: a
    staged file containing ``import missingSibling`` passes it while
    failing on the first execution exactly as director did. The whole
    lesson of this file is that inspecting an artifact is not executing
    it.

    So this stages the real set into a directory and imports each
    module from there **with the vaibify package made unimportable**.
    That last part is the difference between proving the container case
    and proving the checkout case: ``overleafSync`` imports
    ``vaibify.reproducibility.latexConnector`` with a flat fallback, so
    in a checkout the package branch always wins and the flat path --
    the only one the container has -- is never executed. The container
    installs no vaibify package, which is what
    ``fnCopyContainerScripts`` means by "flat top-level names".

    Run in a subprocess, because a blocker installed in-process would
    have to fight the pytest session's already-imported vaibify.

    Kills: staging a module whose flat dependency is not itself staged.
    """
    import shutil
    import subprocess
    import sys

    listStaged = _tlistStagedModuleSources()
    assert listStaged, "no staged modules found -- the list has drifted"
    sStagingDirectory = str(tmp_path / "usr-share-vaibify")
    os.makedirs(sStagingDirectory)
    for sName, sPath in listStaged:
        shutil.copy2(sPath, os.path.join(sStagingDirectory, sName))

    listFailures = []
    for sName, _ in listStaged:
        sModuleName = os.path.splitext(sName)[0]
        resultImport = subprocess.run(
            [sys.executable, "-c", _S_ISOLATED_IMPORT_DRIVER, sModuleName],
            cwd=sStagingDirectory, capture_output=True, text=True,
        )
        if resultImport.returncode != 0:
            listFailures.append(
                f"{sName}: {resultImport.stderr.strip().splitlines()[-1]}"
                if resultImport.stderr.strip() else sName
            )
    assert listFailures == [], (
        f"these modules are staged into the image but cannot be "
        f"imported the way the container imports them -- flat, with no "
        f"vaibify package present -- so they fail on first execution "
        f"inside it: {listFailures}"
    )


# Imports one staged module with `vaibify` made unimportable, modelling
# /usr/share/vaibify inside a container that has no vaibify install.
_S_ISOLATED_IMPORT_DRIVER = """
import importlib, sys

class _BlockVaibify:
    def find_module(self, sName, sPath=None):
        return self if sName == "vaibify" or sName.startswith("vaibify.") \\
            else None
    def find_spec(self, sName, sPath=None, moduleTarget=None):
        if sName == "vaibify" or sName.startswith("vaibify."):
            raise ImportError(
                "vaibify is not installed in the container; a staged "
                "module must import as a flat top-level name"
            )
        return None

sys.meta_path.insert(0, _BlockVaibify())
sys.path.insert(0, "")
importlib.import_module(sys.argv[1])
"""


def _flistRelativeImports(sPath):
    """Return the package-relative import statements a source file uses."""
    import ast
    with open(sPath, "r", encoding="utf-8") as fileSource:
        treeModule = ast.parse(fileSource.read())
    return [
        f"line {nodeImport.lineno}: from "
        f"{'.' * nodeImport.level}{nodeImport.module or ''} import ..."
        for nodeImport in ast.walk(treeModule)
        if isinstance(nodeImport, ast.ImportFrom) and nodeImport.level
    ]
