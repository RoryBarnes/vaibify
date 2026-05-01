"""Static guards that every external network call inside a Dockerfile
is wrapped in the retry-plus-diagnostic template established by the
deadsnakes-PPA fix (F-B-01).

The audit document
``~/.claude/plans/mac-docker-failure-audit.md`` (sections F-B-02..07)
catalogs every network call: NodeSource setup, npm install, CRAN
keyring, IRkernel install, Julia binary, Miniforge installer. Each
must show up here exactly once with the same three guarantees:

1. The wrapping ``RUN`` block carries a curl ``--retry`` flag (or, for
   the npm install, ``--fetch-retries``); the IRkernel install pins a
   single CRAN mirror instead of relying on a shell retry flag.
2. The wrapping ``RUN`` block emits the ``vaibify build`` diagnostic
   prefix on failure.
3. The wrapping ``RUN`` block exits with ``exit 1`` so a failed
   download surfaces immediately.

These checks are static — they parse the Dockerfile source and never
invoke Docker. A future agent that drops the retry, the diagnostic,
or the explicit ``exit 1`` will trip the corresponding test.
"""
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKER_DIR = REPO_ROOT / "docker"


def fsReadDockerfile(sName):
    """Return the source of a Dockerfile under ``docker/`` as a string."""
    return (DOCKER_DIR / sName).read_text(encoding="utf-8")


def flistFindRunBlocks(sSource):
    """Return every ``RUN`` block in a Dockerfile as a list of strings.

    A RUN block starts at a line whose first non-whitespace token is
    ``RUN`` and continues across line continuations (``\\`` at end of
    line) until a logical newline terminates it.
    """
    listLines = sSource.splitlines()
    listBlocks = []
    listCurrent = None
    for sLine in listLines:
        sStripped = sLine.lstrip()
        if listCurrent is None:
            if sStripped.startswith("RUN "):
                listCurrent = [sLine]
                if not sLine.rstrip().endswith("\\"):
                    listBlocks.append("\n".join(listCurrent))
                    listCurrent = None
        else:
            listCurrent.append(sLine)
            if not sLine.rstrip().endswith("\\"):
                listBlocks.append("\n".join(listCurrent))
                listCurrent = None
    if listCurrent is not None:
        listBlocks.append("\n".join(listCurrent))
    return listBlocks


def fsFindBlockContaining(sSource, sNeedle):
    """Return the first RUN block in ``sSource`` that contains ``sNeedle``."""
    for sBlock in flistFindRunBlocks(sSource):
        if sNeedle in sBlock:
            return sBlock
    raise AssertionError(
        f"No RUN block in source contains the substring {sNeedle!r}; "
        "the network-call site may have moved or been renamed"
    )


def fnAssertHardenedBlock(sBlock, sExpectedRetryToken, sLabel):
    """Assert that ``sBlock`` carries retry, diagnostic, and exit-1 guards."""
    assert sExpectedRetryToken in sBlock, (
        f"{sLabel}: missing retry token {sExpectedRetryToken!r}; the "
        "Dockerfile network call must use the deadsnakes-style retry "
        "wrapper to survive Mac+Colima TLS flakiness"
    )
    assert "vaibify build" in sBlock, (
        f"{sLabel}: missing the 'vaibify build' diagnostic prefix; on "
        "network failure the user must see an actionable message that "
        "names the failing component"
    )
    assert "exit 1" in sBlock, (
        f"{sLabel}: missing 'exit 1' after the diagnostic; without it "
        "a network failure can silently fall through to the next step"
    )


def testNodeSourceCurlIsHardened():
    """F-B-02: NodeSource setup script download must retry + diagnose."""
    sSource = fsReadDockerfile("Dockerfile.claude")
    sBlock = fsFindBlockContaining(sSource, "deb.nodesource.com")
    fnAssertHardenedBlock(sBlock, "--retry", "Dockerfile.claude NodeSource")


def testNpmClaudeInstallIsHardened():
    """F-B-03: Claude Code npm install must retry + diagnose."""
    sSource = fsReadDockerfile("Dockerfile.claude")
    sBlock = fsFindBlockContaining(sSource, "@anthropic-ai/claude-code")
    fnAssertHardenedBlock(
        sBlock, "--fetch-retries", "Dockerfile.claude npm install"
    )


def testCranKeyringCurlIsHardened():
    """F-B-04: CRAN keyring fetch must retry + diagnose."""
    sSource = fsReadDockerfile("Dockerfile.rlang")
    sBlock = fsFindBlockContaining(sSource, "marutter_pubkey.asc")
    fnAssertHardenedBlock(sBlock, "--retry", "Dockerfile.rlang CRAN keyring")


def testIRkernelInstallIsHardened():
    """F-B-05: IRkernel install must pin a mirror + diagnose on failure."""
    sSource = fsReadDockerfile("Dockerfile.rlang")
    sBlock = fsFindBlockContaining(sSource, "IRkernel")
    assert "repos='https://" in sBlock or 'repos="https://' in sBlock, (
        "Dockerfile.rlang IRkernel install: must pin a CRAN mirror via "
        "repos=' https://...' to avoid R's mirror-resolution heuristics"
    )
    assert "vaibify build" in sBlock, (
        "Dockerfile.rlang IRkernel install: missing 'vaibify build' "
        "diagnostic prefix on Rscript failure"
    )
    assert "exit 1" in sBlock, (
        "Dockerfile.rlang IRkernel install: missing 'exit 1' after the "
        "diagnostic"
    )


def testJuliaCurlIsHardened():
    """F-B-06: Julia binary download must retry + diagnose."""
    sSource = fsReadDockerfile("Dockerfile.julia")
    sBlock = fsFindBlockContaining(sSource, "julialang-s3.julialang.org")
    fnAssertHardenedBlock(sBlock, "--retry", "Dockerfile.julia binary")


def testMiniforgeCurlIsHardened():
    """F-B-07: Miniforge installer download must retry + diagnose."""
    sSource = fsReadDockerfile("Dockerfile")
    sBlock = fsFindBlockContaining(sSource, "miniforge")
    fnAssertHardenedBlock(sBlock, "--retry", "Dockerfile Miniforge installer")
