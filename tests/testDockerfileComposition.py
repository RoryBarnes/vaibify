"""The composed image Dockerfile, and the lint changes it needs.

PROOF Level 3 reads ``<projectRepo>/Dockerfile``. A vaibify project
has none: the image is built from vaibify's OWN packaged Dockerfiles,
as a CHAIN — base, then one ``docker build`` per enabled overlay, each
given the previous image as ``BASE_IMAGE``. So the row opened red for
every project, with nothing the researcher could do.

Copying only the base would be worse than nothing: a file that did not
build the image, sitting in the repo looking like provenance. These
pin the properties that make the composed artifact honest — the chain
order, the refusal to clobber, and the two lint exemptions a
multi-stage file needs.
"""

import pytest

from vaibify.reproducibility.dockerfileComposer import (
    S_BASE_STAGE_NAME,
    S_GENERATED_MARKER,
    fbTextWasGeneratedByVaibify,
    fsComposeImageDockerfile,
    fsStageNameForOverlay,
)
from vaibify.reproducibility.dockerfileLint import (
    flistCheckBaseImageDigests,
    flistLintDockerfile,
)
from vaibify.reproducibility.imageDockerfileExport import (
    fsRefusalIfDockerfileNotReplaceable,
)
from vaibify.reproducibility.repoFiles import HostRepoFiles


S_DIGEST = "@sha256:" + "a" * 64
S_BASE = f"ARG BASE_IMAGE=ubuntu:24.04{S_DIGEST}\nFROM ${{BASE_IMAGE}}\nRUN echo base\n"


def _flistTOverlays(*names):
    return [
        (sName, "ARG BASE_IMAGE=vaibify:latest\nFROM ${BASE_IMAGE}\n"
                f"RUN echo {sName}\n")
        for sName in names
    ]


# ---------------------------------------------------------------- lint


def test_arg_defaulted_from_line_counts_as_pinned():
    """``FROM ${BASE_IMAGE}`` with a digest default is pinned."""
    assert flistCheckBaseImageDigests(
        [f"ARG BASE_IMAGE=ubuntu:24.04{S_DIGEST}", "FROM ${BASE_IMAGE}"],
    ) == []


def test_arg_without_a_default_is_still_unpinned():
    """The exemption must not extend to an ARG the file cannot vouch for.

    Without this leg the ARG fix would read as "any variable FROM is
    fine", which would waive the check on exactly the Dockerfiles that
    take their base from the builder.
    """
    assert flistCheckBaseImageDigests(
        ["ARG BASE_IMAGE", "FROM ${BASE_IMAGE}"],
    ) != []


def test_arg_default_that_is_a_floating_tag_is_unpinned():
    """Resolving the ARG must judge the RESOLVED text, not skip the line."""
    assert flistCheckBaseImageDigests(
        ["ARG BASE_IMAGE=ubuntu:24.04", "FROM ${BASE_IMAGE}"],
    ) != []


def test_a_reference_to_an_earlier_stage_needs_no_digest():
    """A multi-stage file's later FROMs name build products, not images."""
    assert flistCheckBaseImageDigests([
        f"FROM ubuntu{S_DIGEST} AS one",
        "FROM one AS two",
    ]) == []


def test_a_forward_stage_reference_is_not_exempt():
    """Only stages declared ABOVE count, or the exemption is unbounded.

    Asserting the multi-stage case alone would pass for an
    implementation that skipped every bare word.
    """
    assert flistCheckBaseImageDigests([
        "FROM later AS one",
        f"FROM ubuntu{S_DIGEST} AS later",
    ]) != []


# ------------------------------------------------------------ composer


def test_the_chain_is_stitched_in_application_order():
    """Each overlay must descend from the one before it, not from the base.

    The order IS the semantics: imageBuilder feeds each overlay the
    previous IMAGE. A composition that pointed every stage at the base
    would produce a file that builds and silently drops all but the
    last overlay's work.
    """
    sOut = fsComposeImageDockerfile(S_BASE, _flistTOverlays("claude", "uv"))
    assert f"FROM ${{BASE_IMAGE}} AS {S_BASE_STAGE_NAME}" in sOut
    assert f"FROM {S_BASE_STAGE_NAME} AS stage-claude" in sOut
    assert "FROM stage-claude AS stage-uv" in sOut


def test_overlay_base_image_args_are_dropped():
    """A surviving ``ARG BASE_IMAGE=vaibify:latest`` would be a floating pin.

    It would also describe a base the composed file no longer uses.
    """
    sOut = fsComposeImageDockerfile(S_BASE, _flistTOverlays("claude"))
    assert "vaibify:latest" not in sOut


def test_the_composed_file_passes_the_l3_lint(tmp_path):
    """The whole point: the artifact vaibify writes must satisfy the gate."""
    (tmp_path / "Dockerfile").write_text(
        fsComposeImageDockerfile(S_BASE, _flistTOverlays("claude", "uv"))
        + "\nARG SOURCE_DATE_EPOCH=0\n",
    )
    assert flistLintDockerfile(HostRepoFiles(str(tmp_path))) == []


def test_camel_case_overlay_names_become_legal_stage_names():
    """Docker stage names are lower-case; overlay names are camelCase."""
    assert fsStageNameForOverlay("nestedSampling") == "stage-nestedsampling"


def test_the_header_states_what_the_file_is_not():
    """The artifact must carry its own limits, not rely on docs.

    A Dockerfile in a repo reads as "this builds the image". This one
    does not reproduce it byte-for-byte, and the file has to say so
    where a reader will be standing.
    """
    sOut = fsComposeImageDockerfile(S_BASE, _flistTOverlays("claude"))
    assert fbTextWasGeneratedByVaibify(sOut)
    assert "never builds from this file" in sOut
    assert "claude" in sOut.split("\n\n")[0]


# ------------------------------------------------------------- refusal


def test_a_researcher_authored_dockerfile_is_never_overwritten(tmp_path):
    """The file may be what actually builds their image."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
    sRefusal = fsRefusalIfDockerfileNotReplaceable(
        HostRepoFiles(str(tmp_path)),
    )
    assert sRefusal
    assert "did not generate" in sRefusal


@pytest.mark.parametrize("sExisting, bAllowed", [
    (None, True),
    (S_GENERATED_MARKER + "\nFROM x\n", True),
    ("FROM python:3.12\n", False),
])
def test_only_absent_or_vaibify_generated_files_are_replaceable(
    tmp_path, sExisting, bAllowed,
):
    """Refreshing vaibify's own artifact stays one click; clobbering never is."""
    if sExisting is not None:
        (tmp_path / "Dockerfile").write_text(sExisting)
    sRefusal = fsRefusalIfDockerfileNotReplaceable(
        HostRepoFiles(str(tmp_path)),
    )
    assert (sRefusal == "") is bAllowed


def test_vaibifys_own_shipped_dockerfile_satisfies_its_own_lint():
    """The base vaibify ships must pass the gate vaibify enforces.

    It did not until 2026-08-27: one ARG-defaulted FROM the lint
    misread as unpinned, 58 unpinned apt packages, and no
    SOURCE_DATE_EPOCH. Copying that into a researcher's repo would
    have traded one red row for sixty. The apt pins are WAIVED with a
    stated rationale in the file, not silently, so this passing is a
    recorded judgement rather than a hidden one.
    """
    from vaibify import resources
    import os
    sPath = os.path.join(
        str(resources.fpathContainerImageRoot()), "Dockerfile",
    )
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        listLines = fileHandle.read().splitlines()
    assert flistCheckBaseImageDigests(listLines) == []


# ------------------------------------------------- toolchain pinning


def _flistShippedDockerfileLines():
    """Return vaibify's own base Dockerfile as lines."""
    from vaibify import resources
    import os
    sPath = os.path.join(
        str(resources.fpathContainerImageRoot()), "Dockerfile",
    )
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read().splitlines()


def test_the_compiler_toolchain_is_pinned_and_not_waived():
    """gcc/g++/make carry exact versions in a block with NO waiver.

    The researcher's ruling (2026-08-28): a future researcher SHOULD
    learn that a version is gone and decide how to proceed, rather
    than have a rebuild swap their compiler silently. That only holds
    while this block stays out of the waiver.

    Asserting the pins alone would not be enough -- an
    ``allow-unpinned`` marker added to this block would leave the pins
    textually present while making the lint stop enforcing them, and
    a later edit could then drop them with nothing failing.
    """
    listLines = _flistShippedDockerfileLines()
    listToolchainBlocks = [
        (iStart, sText)
        for iStart, sText in _flistLogicalAptBlocks(listLines)
        if "gcc" in sText
    ]
    assert listToolchainBlocks, "no apt block installs gcc any more"
    for iStart, sText in listToolchainBlocks:
        assert "# allow-unpinned" not in sText, (
            f"the toolchain apt block at line {iStart} is waived; a "
            "rebuild may then substitute a different compiler without "
            "telling anyone"
        )
    # The pins are real, and cover the whole compile-and-link closure.
    # Spot-checked by GROUP rather than by counting, because a count
    # goes stale on the next base-image bump while these roles do not:
    # the compiler and its metapackage (pinning `gcc` alone leaves the
    # actual compiler free to float), the assembler/linker, the C
    # library and its headers, and gcc's constant-folding math
    # libraries, which can change emitted numeric values.
    sAll = " ".join(sText for _, sText in listToolchainBlocks)
    for sPackage in (
        "gcc=", "gcc-13=", "g++=", "g++-13=", "cpp-13=",
        "libgcc-13-dev=", "libstdc++-13-dev=",
        "binutils=", "libbinutils=",
        "libc6=", "libc6-dev=", "linux-libc-dev=",
        "libisl23=", "libmpc3=", "libmpfr6=",
        "make=",
    ):
        assert sPackage in sAll, (
            f"{sPackage!r} is not version-pinned in the toolchain "
            "block; a rebuild may emit a different binary"
        )


def test_the_toolchain_block_fails_loudly_with_guidance():
    """A vanished pin must explain itself, not just exit non-zero.

    An unadorned apt failure says "Version '...' was not found",
    which tells a researcher nothing about why the pin exists or what
    their options are.
    """
    sText = "\n".join(_flistShippedDockerfileLines())
    assert "This build stopped on purpose" in sText
    for sOption in (
        "REPRODUCE THE ORIGINAL",
        "ACCEPT A NEWER TOOLCHAIN",
        "FETCH THE OLD PACKAGES",
    ):
        assert sOption in sText, f"the diagnostic omits {sOption!r}"
    # A withdrawn version and an architecture mismatch produce the
    # SAME apt message ("Version ... was not found"), and the fixes are
    # nothing alike, so the diagnostic has to separate them.
    assert "ARCHITECTURE" in sText, (
        "the diagnostic does not mention the architecture cause, so a "
        "BASE_IMAGE repointed at arm64 reads as a withdrawn version"
    )
    assert "apt-cache policy" in sText, (
        "the diagnostic does not show which versions ARE available, "
        "so option 2 cannot be acted on from the error alone"
    )


def _flistLogicalAptBlocks(listLines):
    """Return ``(start_line, joined_text)`` per apt-install block."""
    from vaibify.reproducibility.dockerfileLint import (
        _flistLogicalAptInstallLines,
    )
    return _flistLogicalAptInstallLines(listLines)


def test_trailing_shell_is_not_read_as_a_package_list():
    """``&& rm -rf ...`` after an install must not become fake findings.

    The extractor used to DELETE shell separators and keep reading, so
    every word after the packages became an unpinned-package finding.
    It went unnoticed while every block was either waived (the marker
    short-circuits the check) or ended at its package list; the first
    pinned block followed by shell reported 296 issues.
    """
    from vaibify.reproducibility.dockerfileLint import (
        flistCheckAptVersionPins,
    )
    assert flistCheckAptVersionPins([
        "RUN apt-get install -y foo=1 && rm -rf /var/lib/apt/lists/*",
    ]) == []
    assert flistCheckAptVersionPins([
        "RUN apt-get install -y foo && rm -rf /var/lib/apt/lists/*",
    ]) != []
