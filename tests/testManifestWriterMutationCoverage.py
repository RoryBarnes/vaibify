"""Mutation-coverage tests for vaibify.reproducibility.manifestWriter.

Each test closes a specific coverage hole found by mutation testing:
it asserts the guarantee the production code makes, so it passes on the
unmutated module and fails when the corresponding survived mutation is
applied.
"""

from vaibify.reproducibility.manifestWriter import (
    fnWriteManifest,
    flistParseManifestLines,
)


_MANIFEST_FILENAME = "MANIFEST.sha256"


def _fnWriteFile(pathRepo, sRelativePath, baContent):
    """Create a file inside pathRepo with the given bytes."""
    pathFile = pathRepo / sRelativePath
    pathFile.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(baContent, str):
        baContent = baContent.encode("utf-8")
    pathFile.write_bytes(baContent)
    return pathFile


# ----------------------------------------------------------------------
# Hole 1: flag-like tokens whose value ends in .py must NOT be resolved
# as step-directory test scripts.
# ----------------------------------------------------------------------


def test_flag_token_ending_in_py_is_not_treated_as_test_script(tmp_path):
    """``pytest --config=conf.py tests/test_real.py`` pins only the real test.

    Regression: a flag value ending in ``.py`` (here ``--config=conf.py``)
    must not be resolved against the step directory. If it were, the
    writer would try to hash ``stepB/--config=conf.py``, which is absent,
    and ``_fnRaiseUnhashableFile`` would raise ``FileNotFoundError`` and
    abort the ENTIRE manifest write — the L3 manifest could never be
    generated. The guard is the ``not sToken.startswith('-')`` clause in
    ``_fbLooksLikeTestScriptToken``.
    """
    _fnWriteFile(tmp_path, "stepB/tests/test_real.py", "def test(): pass\n")
    dictWorkflow = {"listSteps": [{
        "sName": "S1",
        "sDirectory": "stepB",
        "saTestCommands": ["pytest --config=conf.py tests/test_real.py"],
    }]}

    # Must not raise: the flag token cannot abort the write.
    fnWriteManifest(str(tmp_path), dictWorkflow)

    listEntries = flistParseManifestLines(str(tmp_path))
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    assert setPaths == {"stepB/tests/test_real.py"}
    assert not any("--config" in sPath for sPath in setPaths)
    assert not any("conf.py" in sPath for sPath in setPaths)


# ----------------------------------------------------------------------
# Hole 2: the manifest header banner is a pinned literal first line.
# ----------------------------------------------------------------------


def test_manifest_header_is_exact_literal_first_line(tmp_path):
    """The first manifest line is the exact canonical banner string.

    The header is pinned as a literal here (NOT via
    ``manifestWriter._MANIFEST_HEADER``) so the assertion cannot adapt
    to a mutated banner. External format-sniffing tools key off this
    exact line.
    """
    dictWorkflow = {"listSteps": []}
    fnWriteManifest(str(tmp_path), dictWorkflow)
    sContent = (tmp_path / _MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert (
        sContent.splitlines(keepends=True)[0]
        == "# SHA-256 manifest of workflow artefacts\n"
    )
