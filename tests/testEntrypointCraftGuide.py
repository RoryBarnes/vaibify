"""The shipped craft guide must reach every in-container agent.

Vaibify's containers used to deliver only *operational* guidance (how
to drive vaibify-do, the dashboard, project.json) plus four naming
bullets. The craft of writing the researcher's science code —
readability, observability, localizability, error handling,
testing/invariants — lived only in vaibify's own repository guide,
which steers agents editing vaibify itself: the wrong audience. The
2026-08-10 audit confirmed the gap in a live container, so the craft
guidance now ships as ``containerImage/craftGuide.md``, appended by
``fnWriteClaudeMd`` onto the generated ``/workspace/CLAUDE.md`` that
every agent overlay reads through the provider symlinks.

These are structural tests over the shell source and the packaged
files, in the style of the other entrypoint tests. They pin the whole
delivery chain — the file ships, the Dockerfile copies it, the
entrypoint appends it from the same path, a missing guide warns
instead of failing silently — because "present in the repository" is
not "delivered to the agent."
"""

import os
import re
import subprocess

_S_CONTAINER_IMAGE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "vaibify", "containerImage",
)
_S_ENTRYPOINT = os.path.abspath(
    os.path.join(_S_CONTAINER_IMAGE_DIR, "entrypoint.sh")
)
_S_CRAFT_GUIDE_CONTAINER_PATH = "/usr/share/vaibify/craftGuide.md"


def _fsRunWriteClaudeMd(sWorkspace, sCraftGuidePath):
    """Source entrypoint.sh and run the real fnWriteClaudeMd.

    The main block at the bottom of entrypoint.sh is guarded by a
    ``BASH_SOURCE == 0`` check, so sourcing leaves the helpers defined
    without executing the entrypoint itself. The warnings array is
    echoed afterward so the missing-guide branch is observable.
    """
    sScript = (
        "set +e\n"
        f'WORKSPACE="{sWorkspace}"\n'
        f'CRAFT_GUIDE_PATH="{sCraftGuidePath}"\n'
        "export WORKSPACE CRAFT_GUIDE_PATH\n"
        f"source {_S_ENTRYPOINT}\n"
        "fnWriteClaudeMd\n"
        'printf "%s\\n" "${saStartupWarnings[@]}"\n'
    )
    return subprocess.run(
        ["bash", "-c", sScript], capture_output=True, text=True,
    )


def _fsReadContainerImageFile(sName):
    with open(
        os.path.join(_S_CONTAINER_IMAGE_DIR, sName), "r",
        encoding="utf-8",
    ) as fileHandle:
        return fileHandle.read()


def _fsExtractClaudeMdHeredoc(sEntrypoint):
    """Return the literal content fnWriteClaudeMd writes before the guide."""
    matchHeredoc = re.search(
        r"<< 'CLAUDEMD'\n(.*?)\nCLAUDEMD\n", sEntrypoint, re.DOTALL,
    )
    assert matchHeredoc, "fnWriteClaudeMd lost its CLAUDEMD heredoc"
    return matchHeredoc.group(1)


def testCraftGuideCoversEveryCraftAxis():
    """Each craft axis the guide exists to deliver has its section."""
    sGuide = _fsReadContainerImageFile("craftGuide.md")
    for sHeading in (
        "## Readability",
        "## Observability",
        "## Localizability",
        "## Error Handling",
        "## Testing and Invariants",
    ):
        assert sHeading in sGuide, (
            f"craftGuide.md lost its '{sHeading}' section; the "
            f"corresponding craft axis no longer ships to agents"
        )


def testDockerfileCopiesTheCraftGuideWhereTheEntrypointReadsIt():
    """The COPY destination and the entrypoint's read path must agree."""
    sDockerfile = _fsReadContainerImageFile("Dockerfile")
    assert (
        f"COPY craftGuide.md {_S_CRAFT_GUIDE_CONTAINER_PATH}"
        in sDockerfile
    ), "the Dockerfile no longer ships craftGuide.md into the image"
    sEntrypoint = _fsReadContainerImageFile("entrypoint.sh")
    assert (
        f'CRAFT_GUIDE_PATH="${{CRAFT_GUIDE_PATH:-'
        f'{_S_CRAFT_GUIDE_CONTAINER_PATH}}}"'
    ) in sEntrypoint, (
        "entrypoint.sh reads the craft guide from a different path "
        "than the Dockerfile copies it to"
    )


def testTheRealWriteDeliversCraftThroughEveryProviderName(tmp_path):
    """Running fnWriteClaudeMd lands the craft guide in the agent docs.

    This executes the actual delivery, not its spelling: the generated
    workspace doc must carry both the operational context and every
    craft section, and the AGENTS.md / GEMINI.md provider symlinks
    must resolve to that same content.
    """
    sGuideSource = os.path.join(_S_CONTAINER_IMAGE_DIR, "craftGuide.md")
    resultProc = _fsRunWriteClaudeMd(str(tmp_path), sGuideSource)
    assert resultProc.returncode == 0, resultProc.stderr
    with open(
        tmp_path / "CLAUDE.md", "r", encoding="utf-8"
    ) as fileHandle:
        sDelivered = fileHandle.read()
    assert "# Vaibify Container Environment" in sDelivered
    for sHeading in (
        "## Readability", "## Observability", "## Localizability",
        "## Error Handling", "## Testing and Invariants",
    ):
        assert sHeading in sDelivered, (
            f"fnWriteClaudeMd ran but '{sHeading}' never reached the "
            f"workspace agent context"
        )
    for sProviderName in ("AGENTS.md", "GEMINI.md"):
        pathLink = tmp_path / sProviderName
        assert pathLink.is_symlink(), f"{sProviderName} link missing"
        assert pathLink.resolve() == (tmp_path / "CLAUDE.md").resolve()
    assert "craftGuide" not in resultProc.stdout, (
        "the missing-guide warning fired even though the guide was "
        "present"
    )


def testMissingCraftGuideWarnsInsteadOfFailingSilently(tmp_path):
    """A container without the guide must say so at startup.

    The operational context must still be written — a missing craft
    guide degrades the context, it must not take down the container —
    but the degradation has to be visible in the readiness warnings.
    """
    resultProc = _fsRunWriteClaudeMd(
        str(tmp_path), str(tmp_path / "absent.md"),
    )
    assert resultProc.returncode == 0, resultProc.stderr
    with open(
        tmp_path / "CLAUDE.md", "r", encoding="utf-8"
    ) as fileHandle:
        sDelivered = fileHandle.read()
    assert "# Vaibify Container Environment" in sDelivered
    assert "## Observability" not in sDelivered
    assert re.search(
        r"craftGuide: agent-docs: craft guide missing",
        resultProc.stdout,
    ), (
        "a container missing the craft guide started with no warning; "
        "the degraded agent context would be invisible"
    )


def testComposedContextReachesEveryProviderWithoutClobbering(tmp_path):
    """The real linker delivers craft to every provider name.

    Delivery must not depend on how a given agent walks the directory
    tree, so every provider's repo-root name resolves to a file
    carrying both vaibify's shipped guidance and the researcher's own
    — and the researcher's file is left exactly as it was.
    """
    pathRepo = tmp_path / "someRepository"
    pathVaib = pathRepo / ".vaibify"
    pathVaib.mkdir(parents=True)
    sResearcherText = "# My project\n\nNever refit the calibration.\n"
    (pathVaib / "AGENTS.md").write_text(sResearcherText)

    sGuideSource = os.path.join(_S_CONTAINER_IMAGE_DIR, "craftGuide.md")
    resultProc = _fsRunWriteClaudeMd(str(tmp_path), sGuideSource)
    assert resultProc.returncode == 0, resultProc.stderr

    for sProviderName in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"):
        pathRoot = pathRepo / sProviderName
        assert pathRoot.is_symlink(), (
            f"{sProviderName} is not linked, so that provider reads no "
            f"guidance at all"
        )
        sDelivered = pathRoot.read_text()
        assert "## Observability" in sDelivered, (
            f"{sProviderName} resolves to a file with no craft "
            f"guidance; delivery still depends on tree traversal"
        )
        assert "Never refit the calibration." in sDelivered, (
            f"{sProviderName} lost the researcher's project context"
        )
        assert sDelivered.index("## Observability") < sDelivered.index(
            "Never refit the calibration.",
        ), "the researcher's context must come last so it wins"

    assert (pathVaib / "AGENTS.md").read_text() == sResearcherText, (
        "vaibify rewrote the researcher's own context file"
    )


def testComposedContextIsGitIgnored(tmp_path):
    """The generated file must not enter the researcher's commits."""
    pathRepo = tmp_path / "someRepository"
    (pathRepo / ".vaibify").mkdir(parents=True)
    sGuideSource = os.path.join(_S_CONTAINER_IMAGE_DIR, "craftGuide.md")
    resultProc = _fsRunWriteClaudeMd(str(tmp_path), sGuideSource)
    assert resultProc.returncode == 0, resultProc.stderr
    sIgnored = (pathRepo / ".vaibify" / ".gitignore").read_text()
    assert "agentContext.md" in sIgnored


def testCraftReachesARepositoryWithNoProjectContext(tmp_path):
    """A repo the researcher never wrote context for still gets craft.

    This is the masking failure the whole change exists to fix: craft
    guidance must not be contingent on the researcher having authored
    a project context first.
    """
    pathRepo = tmp_path / "someRepository"
    (pathRepo / ".vaibify").mkdir(parents=True)
    sGuideSource = os.path.join(_S_CONTAINER_IMAGE_DIR, "craftGuide.md")
    resultProc = _fsRunWriteClaudeMd(str(tmp_path), sGuideSource)
    assert resultProc.returncode == 0, resultProc.stderr
    sDelivered = (pathRepo / "CLAUDE.md").read_text()
    assert "## Observability" in sDelivered
    assert "## Localizability" in sDelivered


def testAnExistingRepoIsRepointedOntoTheComposedContext(tmp_path):
    """A link from before this change must not strand the repo.

    Every already-vaibified repository has repo-root symlinks onto
    `.vaibify/AGENTS.md`. Left alone they would keep resolving to
    project context only, so the craft guidance would reach new
    repositories and silently skip every existing one.
    """
    pathRepo = tmp_path / "someRepository"
    pathVaib = pathRepo / ".vaibify"
    pathVaib.mkdir(parents=True)
    (pathVaib / "AGENTS.md").write_text("# Existing context\n")
    (pathRepo / "CLAUDE.md").symlink_to(".vaibify/AGENTS.md")

    sGuideSource = os.path.join(_S_CONTAINER_IMAGE_DIR, "craftGuide.md")
    resultProc = _fsRunWriteClaudeMd(str(tmp_path), sGuideSource)
    assert resultProc.returncode == 0, resultProc.stderr
    assert "## Observability" in (pathRepo / "CLAUDE.md").read_text(), (
        "an already-vaibified repository never receives the craft "
        "guidance"
    )


def testAResearchersRealRootFileIsNeverOverwritten(tmp_path):
    """A pre-existing real root CLAUDE.md is the researcher's, not ours."""
    pathRepo = tmp_path / "someRepository"
    (pathRepo / ".vaibify").mkdir(parents=True)
    sTheirs = "# Their own notes\n"
    (pathRepo / "CLAUDE.md").write_text(sTheirs)
    sGuideSource = os.path.join(_S_CONTAINER_IMAGE_DIR, "craftGuide.md")
    resultProc = _fsRunWriteClaudeMd(str(tmp_path), sGuideSource)
    assert resultProc.returncode == 0, resultProc.stderr
    assert (pathRepo / "CLAUDE.md").read_text() == sTheirs
    assert not (pathRepo / "CLAUDE.md").is_symlink()


def testALegacyRootLinkIsRepairedNotLeftDangling(tmp_path):
    """The legacy migration must not strand a provider on a dead link.

    Reproduces a real repository state: the repo-root `CLAUDE.md`
    points at `.vaibify/CLAUDE.md`, which the migration renames to
    `.vaibify/AGENTS.md`. The old guard tested `[ ! -e ] && [ ! -L ]`,
    and a dangling symlink is `-L` true, so the link was never
    repaired and that provider read nothing at all — silently, since a
    missing context file looks the same as an empty one.
    """
    pathRepo = tmp_path / "someRepository"
    pathVaib = pathRepo / ".vaibify"
    pathVaib.mkdir(parents=True)
    (pathVaib / "CLAUDE.md").write_text("# Legacy project context\n")
    (pathRepo / "CLAUDE.md").symlink_to(".vaibify/CLAUDE.md")

    sGuideSource = os.path.join(_S_CONTAINER_IMAGE_DIR, "craftGuide.md")
    resultProc = _fsRunWriteClaudeMd(str(tmp_path), sGuideSource)
    assert resultProc.returncode == 0, resultProc.stderr

    pathRoot = pathRepo / "CLAUDE.md"
    assert pathRoot.exists(), (
        "the root link dangles after the legacy migration renamed its "
        "target; this provider now reads no context whatsoever"
    )
    sDelivered = pathRoot.read_text()
    assert "# Legacy project context" in sDelivered
    assert "## Observability" in sDelivered


def testARealRootFileIsNeverRepointed(tmp_path):
    """Repointing must key on vaibify's own targets, not on being a link.

    A symlink the researcher made themselves — to their own notes, or
    into a shared directory — is not vaibify's to redirect.
    """
    pathRepo = tmp_path / "someRepository"
    (pathRepo / ".vaibify").mkdir(parents=True)
    (tmp_path / "theirNotes.md").write_text("# Their own notes\n")
    (pathRepo / "CLAUDE.md").symlink_to("../theirNotes.md")

    sGuideSource = os.path.join(_S_CONTAINER_IMAGE_DIR, "craftGuide.md")
    resultProc = _fsRunWriteClaudeMd(str(tmp_path), sGuideSource)
    assert resultProc.returncode == 0, resultProc.stderr
    assert os.readlink(pathRepo / "CLAUDE.md") == "../theirNotes.md", (
        "vaibify redirected a symlink the researcher created"
    )


def testComposedContextSeparatorMatchesTheHost():
    """The container and host must compose the same file.

    Both build `.vaibify/agentContext.md` — the entrypoint at startup,
    the backend when the Project Context editor saves. The banner is
    duplicated because a container script cannot import from the host,
    so nothing but this test stops the two spellings from drifting.
    """
    from vaibify.gui.routes.replayRoutes import (
        S_COMPOSED_CONTEXT_RELATIVE_PATH,
        S_COMPOSED_CONTEXT_SEPARATOR,
    )
    sEntrypoint = _fsReadContainerImageFile("entrypoint.sh")
    matchSeparator = re.search(
        r'COMPOSED_CONTEXT_SEPARATOR="(.*?)"\n', sEntrypoint, re.DOTALL,
    )
    assert matchSeparator, "the entrypoint lost its separator constant"
    sShell = matchSeparator.group(1).replace("\\`", "`")
    assert sShell == S_COMPOSED_CONTEXT_SEPARATOR, (
        "the container and host compose different banners; one of the "
        "two writes an agent context the other would not recognize"
    )
    assert S_COMPOSED_CONTEXT_RELATIVE_PATH.endswith(
        "agentContext.md",
    )
    assert 'COMPOSED_CONTEXT_BASENAME="agentContext.md"' in sEntrypoint


def testNamingConventionsHaveExactlyOneHome():
    """The heredoc must not regrow a second copy of the naming rules.

    The environment heredoc used to carry four naming bullets; the
    craft guide now owns them. Two copies of the same convention
    drift, which is the divergence bug the style guide names as the
    strongest reason to keep a single home.
    """
    sEntrypoint = _fsReadContainerImageFile("entrypoint.sh")
    sHeredoc = _fsExtractClaudeMdHeredoc(sEntrypoint)
    assert "Hungarian" not in sHeredoc, (
        "the environment heredoc regrew its own naming-convention "
        "section; the craft guide is the single home for those rules"
    )
    sGuide = _fsReadContainerImageFile("craftGuide.md")
    assert "Hungarian" in sGuide, (
        "the naming conventions vanished from the craft guide without "
        "returning to the environment doc"
    )
