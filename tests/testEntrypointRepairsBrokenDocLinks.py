"""A repo-root doc link vaibify made and then stranded must be repaired.

`fnLinkRepoClaudeMd` links CLAUDE.md, AGENTS.md and GEMINI.md at the
canonical `.vaibify/AGENTS.md`, and the same function migrates a legacy
`.vaibify/CLAUDE.md` into that name. The migration is what strands the
link: a root symlink still aimed at `.vaibify/CLAUDE.md` now resolves to
nothing.

The guard on the link loop was `[ ! -e ] && [ ! -L ]`, and a dangling
symlink is `-L` true and `-e` false. It matched neither arm -- not
"absent", so nothing was created; not "present", so nothing was checked
-- and that provider silently read no guidance at all. Silent is the
whole problem: an agent with no instructions does not announce itself,
it just behaves as though the researcher wrote nothing.

These tests SOURCE the real entrypoint and run the real function against
real symlinks in a temporary workspace, rather than asserting over the
shell source as a string. A structural test would pass against a repair
function that is defined and never reached, which is exactly the shape
of the bug being fixed.
"""

import os
import shutil
import subprocess

import pytest


_S_ENTRYPOINT = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "vaibify",
    "containerImage", "entrypoint.sh",
))

_S_PROVIDER_NAMES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md")


def _fnRequireBash():
    """Skip where bash is unavailable; the lane's own env var forbids it."""
    if shutil.which("bash") is None:
        if os.environ.get("VAIBIFY_REQUIRE_DOCKER_DAEMON"):
            pytest.fail("bash is absent on a lane that forbids skipping")
        pytest.skip("bash is not available on this host")


def _fnRunLinkerOverWorkspace(pathWorkspace):
    """Source the real entrypoint and run its doc linker over a workspace.

    The entrypoint guards its startup on ``BASH_SOURCE == $0``, so
    sourcing defines the functions without running a container phase.
    """
    _fnRequireBash()
    sScript = (
        'set -euo pipefail\n'
        'WORKSPACE=%s\n'
        'source %s\n'
        'fnLinkRepoClaudeMd\n'
    ) % (_fsQuote(str(pathWorkspace)), _fsQuote(_S_ENTRYPOINT))
    processResult = subprocess.run(
        ["bash", "-c", sScript],
        capture_output=True, text=True, timeout=60,
    )
    assert processResult.returncode == 0, (
        f"the doc linker failed: {processResult.stderr}"
    )
    return processResult.stdout


def _fsQuote(sValue):
    """Single-quote a value for safe interpolation into a bash script."""
    return "'" + sValue.replace("'", "'\\''") + "'"


def _fpathSeedRepository(pathWorkspace, sRepositoryName="project"):
    """Create a workspace repo holding the canonical guidance file."""
    pathRepository = pathWorkspace / sRepositoryName
    pathVaibify = pathRepository / ".vaibify"
    pathVaibify.mkdir(parents=True)
    (pathVaibify / "AGENTS.md").write_text(
        "# Project context\n", encoding="utf-8",
    )
    return pathRepository


@pytest.mark.falsification
def testADanglingLinkVaibifyMadeIsRepointedAtTheCanonicalFile(tmp_path):
    """The exact state the legacy migration leaves behind.

    Kills: restoring the bare `[ ! -e ] && [ ! -L ]` guard, under which
    the link stays broken and the provider reading that name gets
    nothing.
    """
    pathRepository = _fpathSeedRepository(tmp_path)
    pathStranded = pathRepository / "CLAUDE.md"
    pathStranded.symlink_to(".vaibify/CLAUDE.md")
    assert pathStranded.is_symlink() and not pathStranded.exists(), (
        "the fixture did not actually produce a dangling link"
    )

    _fnRunLinkerOverWorkspace(tmp_path)

    assert pathStranded.exists(), (
        "the stranded link still resolves to nothing, so the provider "
        "reading CLAUDE.md receives no guidance at all"
    )
    assert pathStranded.read_text(encoding="utf-8") == "# Project context\n"


def testAResearchersOwnFileAtAProviderNameIsNeverTouched(tmp_path):
    """A real file at one of those names is theirs, not vaibify's.

    The repair must be scoped by what the link POINTS AT, never by the
    name it occupies -- overwriting a researcher's own CLAUDE.md would
    destroy work to fix a symlink.
    """
    pathRepository = _fpathSeedRepository(tmp_path)
    pathOwn = pathRepository / "CLAUDE.md"
    pathOwn.write_text("# My own instructions\n", encoding="utf-8")

    _fnRunLinkerOverWorkspace(tmp_path)

    assert not pathOwn.is_symlink(), (
        "a researcher's real file was replaced by a symlink"
    )
    assert pathOwn.read_text(encoding="utf-8") == "# My own instructions\n"


@pytest.mark.falsification
def testALinkPointingSomewhereElseIsLeftAlone(tmp_path):
    """Only links vaibify itself created are vaibify's to repoint.

    A dangling link aimed at anything else belongs to the researcher or
    to another tool, and repairing it would be vaibify claiming a name
    it never created.

    Kills: scoping the repair by the NAME occupied rather than by what
    the link points at, which repoints links vaibify never made.
    """
    pathRepository = _fpathSeedRepository(tmp_path)
    pathForeign = pathRepository / "GEMINI.md"
    pathForeign.symlink_to("docs/notMine.md")

    _fnRunLinkerOverWorkspace(tmp_path)

    assert os.readlink(str(pathForeign)) == "docs/notMine.md", (
        "a link vaibify did not create was repointed"
    )


def testAHealthyLinkIsLeftExactlyAsItIs(tmp_path):
    """The repair fires on broken links only, not on every start."""
    pathRepository = _fpathSeedRepository(tmp_path)
    for sName in _S_PROVIDER_NAMES:
        (pathRepository / sName).symlink_to(".vaibify/AGENTS.md")

    sOutput = _fnRunLinkerOverWorkspace(tmp_path)

    assert "Repaired" not in sOutput, (
        f"a healthy link was reported as repaired: {sOutput}"
    )
    for sName in _S_PROVIDER_NAMES:
        assert (pathRepository / sName).exists()


def testTheMigrationAndTheRepairRunInOneStart(tmp_path):
    """End to end: the sequence that produced the bug now self-heals.

    A repository carrying a legacy `.vaibify/CLAUDE.md` and a root link
    aimed at it is migrated and repaired in the same pass, so a
    researcher never has to notice either.
    """
    pathRepository = tmp_path / "project"
    pathVaibify = pathRepository / ".vaibify"
    pathVaibify.mkdir(parents=True)
    (pathVaibify / "CLAUDE.md").write_text(
        "# Legacy context\n", encoding="utf-8",
    )
    (pathRepository / "CLAUDE.md").symlink_to(".vaibify/CLAUDE.md")

    _fnRunLinkerOverWorkspace(tmp_path)

    assert (pathVaibify / "AGENTS.md").exists(), "the migration did not run"
    for sName in _S_PROVIDER_NAMES:
        pathLink = pathRepository / sName
        assert pathLink.exists(), (
            f"{sName} does not resolve after migration + repair"
        )
        assert pathLink.read_text(encoding="utf-8") == "# Legacy context\n"


def testTheClineRulesLinkIsRepairedToo(tmp_path):
    """Cline's link is stranded by the same migration.

    It lives one directory deeper, so its target carries a `../`
    prefix; a repair that assumed the flat spelling would silently skip
    it and leave exactly one agent with no guidance.

    ``cline`` is stubbed onto PATH because ``fnLinkClineRules`` is gated
    on the binary being installed, and the gate is not what is under
    test here.
    """
    _fnRequireBash()
    pathRepository = _fpathSeedRepository(tmp_path)
    pathRules = pathRepository / ".clinerules"
    pathRules.mkdir()
    pathStranded = pathRules / "vaibify.md"
    pathStranded.symlink_to("../.vaibify/CLAUDE.md")

    pathStubBin = tmp_path / "stubBin"
    pathStubBin.mkdir()
    pathCline = pathStubBin / "cline"
    pathCline.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pathCline.chmod(0o755)

    dictEnvironment = dict(os.environ)
    dictEnvironment["PATH"] = (
        str(pathStubBin) + os.pathsep + dictEnvironment.get("PATH", "")
    )
    sScript = (
        'set -euo pipefail\n'
        'WORKSPACE=%s\n'
        'source %s\n'
        'fnLinkRepoClaudeMd\n'
    ) % (_fsQuote(str(tmp_path)), _fsQuote(_S_ENTRYPOINT))
    processResult = subprocess.run(
        ["bash", "-c", sScript],
        capture_output=True, text=True, timeout=60, env=dictEnvironment,
    )
    assert processResult.returncode == 0, processResult.stderr
    assert pathStranded.exists(), (
        "the prefixed Cline link was not recognized as vaibify's own, "
        "so Cline alone would start with no guidance"
    )
    assert pathStranded.read_text(encoding="utf-8") == "# Project context\n"


@pytest.mark.falsification
def testTheOwnershipTestAnswersCorrectlyUnderProductionShellOptions(
    tmp_path,
):
    """The predicate is exercised the way the entrypoint calls it.

    Under ``set -euo pipefail``, and from a CONDITION -- which is the
    only shape any ``fb*`` predicate may be called in, since answering
    false as a bare statement ends the script by design.

    The three answers are asserted together because the risk is not one
    of them being wrong in isolation; it is the ownership rule reducing
    to "is it broken", which would let vaibify claim a name it never
    created. That is only visible when the foreign link and the owned
    link are asked the same question in the same run.

    Kills: reducing the ownership rule to "is it broken", so a dangling
    link vaibify never created is repointed at vaibify's own file.
    """
    _fnRequireBash()
    pathRepository = _fpathSeedRepository(tmp_path)
    pathOwnedBroken = pathRepository / "CLAUDE.md"
    pathOwnedBroken.symlink_to(".vaibify/CLAUDE.md")
    pathHealthy = pathRepository / "AGENTS.md"
    pathHealthy.symlink_to(".vaibify/AGENTS.md")
    pathForeignBroken = pathRepository / "GEMINI.md"
    pathForeignBroken.symlink_to("elsewhere/notMine.md")

    sScript = (
        'set -euo pipefail\n'
        'source %s\n'
        'for sCase in %s %s %s; do\n'
        '    if fbLinkIsVaibifyOwnedAndBroken "$sCase" ""; then\n'
        '        echo "OWNED_AND_BROKEN $sCase"\n'
        '    else\n'
        '        echo "LEAVE_ALONE $sCase"\n'
        '    fi\n'
        'done\n'
        'echo REACHED_THE_END\n'
    ) % (
        _fsQuote(_S_ENTRYPOINT), _fsQuote(str(pathOwnedBroken)),
        _fsQuote(str(pathHealthy)), _fsQuote(str(pathForeignBroken)),
    )
    processResult = subprocess.run(
        ["bash", "-c", sScript], capture_output=True, text=True, timeout=60,
    )
    sOutput = processResult.stdout
    assert "REACHED_THE_END" in sOutput, (
        "the predicate ended the script under the entrypoint's own shell "
        f"options: {sOutput}{processResult.stderr}"
    )
    assert f"OWNED_AND_BROKEN {pathOwnedBroken}" in sOutput, sOutput
    assert f"LEAVE_ALONE {pathHealthy}" in sOutput, sOutput
    assert f"LEAVE_ALONE {pathForeignBroken}" in sOutput, (
        "a dangling link vaibify never created was claimed as its own: "
        + sOutput
    )
