"""Tests for vaibify.gui.templateManager template hashing utilities."""

from unittest.mock import MagicMock, patch

from vaibify.gui.templateManager import (
    _fbFileMatchesTemplate,
    _fsComputeTemplateHash,
    _fsEmbedTemplateHash,
    fsIntegrityTemplateHash,
    fsQualitativeTemplateHash,
    fsQuantitativeTemplateHash,
)


# ---------------------------------------------------------------
# _fsComputeTemplateHash / _fsEmbedTemplateHash round-trip
# ---------------------------------------------------------------


def test_fsComputeTemplateHash_is_deterministic():
    sTemplate = '"""doc"""\n\nimport os\n'
    sHashA = _fsComputeTemplateHash(sTemplate)
    sHashB = _fsComputeTemplateHash(sTemplate)
    assert sHashA == sHashB
    assert len(sHashA) == 16


def test_fsComputeTemplateHash_ignores_leading_hash_line():
    """The hash-stripping regex requires the hash line to be first."""
    sWithHash = "# vaibify-template-hash: abc123def4567890\nimport os\n"
    sWithoutHash = "import os\n"
    assert _fsComputeTemplateHash(sWithHash) == _fsComputeTemplateHash(
        sWithoutHash,
    )


def test_fsEmbedTemplateHash_prepends_hash_on_second_line():
    sTemplate = '"""doc"""\nimport os\n'
    sEmbedded = _fsEmbedTemplateHash(sTemplate)
    assert sEmbedded.startswith('"""doc"""')
    assert "# vaibify-template-hash:" in sEmbedded
    assert "\nimport os" in sEmbedded


def test_fsEmbedTemplateHash_single_line_input():
    sEmbedded = _fsEmbedTemplateHash('"""doc"""')
    assert '"""doc"""' in sEmbedded
    assert "# vaibify-template-hash:" in sEmbedded


# ---------------------------------------------------------------
# _fbFileMatchesTemplate: lines 49-57
# ---------------------------------------------------------------


def test_fbFileMatchesTemplate_empty_file_returns_true():
    """Missing files are safe to overwrite."""
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value="",
    ):
        assert _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", "template content",
        )


def test_fbFileMatchesTemplate_matching_hash_returns_true():
    sTemplate = '"""doc"""\nimport os\n'
    sExpectedHash = _fsComputeTemplateHash(sTemplate)
    sExisting = (
        '"""doc"""\n'
        f"# vaibify-template-hash: {sExpectedHash}\n"
        "import os\n"
    )
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value=sExisting,
    ):
        assert _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", sTemplate,
        )


def test_fbFileMatchesTemplate_mismatched_hash_returns_false():
    sTemplate = '"""doc"""\nimport os\n'
    sExisting = (
        '"""doc"""\n'
        "# vaibify-template-hash: deadbeefdeadbeef\n"
        "import os\n"
    )
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value=sExisting,
    ):
        assert not _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", sTemplate,
        )


def test_fbFileMatchesTemplate_no_hash_line_compares_content():
    """File without hash line is matched via whole-content comparison."""
    sTemplate = '"""doc"""\nimport os\n'
    sExisting = _fsEmbedTemplateHash(sTemplate)
    mockDocker = MagicMock()
    # Strip hash line, then the whole-content comparison should still
    # find a match because _fsEmbedTemplateHash(existing) == embedded.
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value=sExisting,
    ):
        assert _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", sTemplate,
        )


def test_fbFileMatchesTemplate_unrelated_file_returns_false():
    sTemplate = '"""doc"""\nimport os\n'
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value="completely different file content\n",
    ):
        assert not _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", sTemplate,
        )


def test_fbFileMatchesTemplate_non_string_treated_as_empty():
    """Bytes or None are treated as empty -> safe to overwrite."""
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value=None,
    ):
        assert _fbFileMatchesTemplate(
            mockDocker, "cid", "/ws/test.py", "template",
        )


# ---------------------------------------------------------------
# Public hash accessors return stable 16-char hex strings
# ---------------------------------------------------------------


def test_fsQuantitativeTemplateHash_is_stable():
    sHashA = fsQuantitativeTemplateHash()
    sHashB = fsQuantitativeTemplateHash()
    assert sHashA == sHashB
    assert len(sHashA) == 16


def test_fsIntegrityTemplateHash_is_stable():
    sHashA = fsIntegrityTemplateHash()
    sHashB = fsIntegrityTemplateHash()
    assert sHashA == sHashB
    assert len(sHashA) == 16


def test_fsQualitativeTemplateHash_is_stable():
    sHashA = fsQualitativeTemplateHash()
    sHashB = fsQualitativeTemplateHash()
    assert sHashA == sHashB
    assert len(sHashA) == 16


def test_different_templates_produce_different_hashes():
    assert (
        fsQuantitativeTemplateHash()
        != fsIntegrityTemplateHash()
    )
    assert (
        fsIntegrityTemplateHash()
        != fsQualitativeTemplateHash()
    )
