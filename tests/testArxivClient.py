"""Tests for vaibify.reproducibility.arxivClient.

All arXiv HTTP traffic is mocked; the tarball-extraction logic runs
against real on-disk archives generated per test in a tmp_path.
"""

import hashlib
import io
import os
import tarfile
from unittest.mock import MagicMock, patch

import pytest

from vaibify.reproducibility import arxivClient
from vaibify.reproducibility.arxivClient import (
    ArxivAmbiguousMatchError,
    ArxivError,
    ArxivExtractionError,
    ArxivNotFoundError,
    ArxivPathMapError,
    ArxivRateLimitError,
    fdictFetchRemoteHashes,
    fsResolveLatestVersion,
)


@pytest.fixture(autouse=True)
def _fnDisableRateLimitSleep():
    """No-op the 3-second floor so tests do not sleep."""
    with patch.object(arxivClient, "_fnEnforceRateLimit", lambda: None):
        yield


def _fbuildTarballBytes(listMembers):
    """Return the bytes of a tar.gz containing (name, content) tuples."""
    bufferTarball = io.BytesIO()
    with tarfile.open(fileobj=bufferTarball, mode="w:gz") as tarballHandle:
        for sName, baContent in listMembers:
            infoTar = tarfile.TarInfo(name=sName)
            infoTar.size = len(baContent)
            tarballHandle.addfile(infoTar, io.BytesIO(baContent))
    return bufferTarball.getvalue()


def _fmockMetadataResponse(sArxivId, sVersion):
    """Return a mocked requests.Response carrying a minimal arXiv Atom feed."""
    sXml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        '<entry>'
        f'<id>http://arxiv.org/abs/{sArxivId}{sVersion}</id>'
        '</entry></feed>'
    )
    mockResponse = MagicMock()
    mockResponse.status_code = 200
    mockResponse.text = sXml
    return mockResponse


def _fmockEprintResponse(baTarball, iStatusCode=200):
    """Return a mocked streaming requests.Response delivering tarball bytes."""
    mockResponse = MagicMock()
    mockResponse.status_code = iStatusCode
    mockResponse.iter_content.return_value = iter([baTarball])
    mockResponse.text = ""
    return mockResponse


def _fnInstallHttpStubs(mockMetadata, mockEprint):
    """Patch requests.get to return metadata first, then e-print."""
    return patch(
        "vaibify.reproducibility.arxivClient.requests.get",
        side_effect=[mockMetadata, mockEprint],
    )


def _fsExpectedSha256(baContent):
    """Return the SHA-256 hex digest of one byte string."""
    return hashlib.sha256(baContent).hexdigest()


def test_resolve_latest_version_returns_v_suffix():
    mockResponse = _fmockMetadataResponse("2401.12345", "v2")
    with patch(
        "vaibify.reproducibility.arxivClient.requests.get",
        return_value=mockResponse,
    ):
        sVersion = fsResolveLatestVersion("2401.12345")
    assert sVersion == "v2"


def test_resolve_latest_version_raises_not_found_on_empty_feed():
    mockResponse = MagicMock()
    mockResponse.status_code = 200
    mockResponse.text = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    )
    with patch(
        "vaibify.reproducibility.arxivClient.requests.get",
        return_value=mockResponse,
    ):
        with pytest.raises(ArxivNotFoundError):
            fsResolveLatestVersion("9999.99999")


def test_resolve_latest_version_raises_arxiverror_on_bad_xml():
    mockResponse = MagicMock()
    mockResponse.status_code = 200
    mockResponse.text = "not xml at all"
    with patch(
        "vaibify.reproducibility.arxivClient.requests.get",
        return_value=mockResponse,
    ):
        with pytest.raises(ArxivError):
            fsResolveLatestVersion("2401.12345")


def test_404_metadata_raises_not_found():
    mockResponse = MagicMock()
    mockResponse.status_code = 404
    mockResponse.text = ""
    with patch(
        "vaibify.reproducibility.arxivClient.requests.get",
        return_value=mockResponse,
    ):
        with pytest.raises(ArxivNotFoundError):
            fsResolveLatestVersion("0000.00000")


def test_429_metadata_raises_rate_limit():
    mockResponse = MagicMock()
    mockResponse.status_code = 429
    mockResponse.text = ""
    with patch(
        "vaibify.reproducibility.arxivClient.requests.get",
        return_value=mockResponse,
    ):
        with pytest.raises(ArxivRateLimitError):
            fsResolveLatestVersion("2401.12345")


def test_happy_path_basename_match(tmp_path):
    baFig1 = b"\x89PNG\r\n\x1a\nFIGURE-ONE"
    baFig2 = b"\x89PNG\r\n\x1a\nFIGURE-TWO"
    baTarball = _fbuildTarballBytes([
        ("paper/figs/fig1.png", baFig1),
        ("paper/figs/fig2.png", baFig2),
        ("paper/main.tex", b"%dummy"),
    ])
    mockMetadata = _fmockMetadataResponse("2401.12345", "v1")
    mockEprint = _fmockEprintResponse(baTarball)
    with _fnInstallHttpStubs(mockMetadata, mockEprint):
        dictResult = fdictFetchRemoteHashes(
            "2401.12345",
            ["figures/fig1.png", "figures/fig2.png"],
            sCacheDir=str(tmp_path),
        )
    assert dictResult == {
        "figures/fig1.png": _fsExpectedSha256(baFig1),
        "figures/fig2.png": _fsExpectedSha256(baFig2),
    }


def test_explicit_path_map_overrides_basename_match(tmp_path):
    baFigA = b"FIGURE-A-BYTES"
    baFigB = b"FIGURE-B-BYTES"
    baTarball = _fbuildTarballBytes([
        ("paper/figs/fig_a.pdf", baFigA),
        ("paper/figs/fig_b.pdf", baFigB),
    ])
    mockMetadata = _fmockMetadataResponse("2401.12345", "v1")
    mockEprint = _fmockEprintResponse(baTarball)
    dictPathMap = {
        "figures/alpha.pdf": "paper/figs/fig_a.pdf",
        "figures/beta.pdf": "paper/figs/fig_b.pdf",
    }
    with _fnInstallHttpStubs(mockMetadata, mockEprint):
        dictResult = fdictFetchRemoteHashes(
            "2401.12345",
            ["figures/alpha.pdf", "figures/beta.pdf"],
            dictPathMap=dictPathMap,
            sCacheDir=str(tmp_path),
        )
    assert dictResult == {
        "figures/alpha.pdf": _fsExpectedSha256(baFigA),
        "figures/beta.pdf": _fsExpectedSha256(baFigB),
    }


def test_missing_local_path_returns_none(tmp_path):
    baFig1 = b"FIG-ONE"
    baTarball = _fbuildTarballBytes([("paper/figs/fig1.png", baFig1)])
    mockMetadata = _fmockMetadataResponse("2401.12345", "v1")
    mockEprint = _fmockEprintResponse(baTarball)
    with _fnInstallHttpStubs(mockMetadata, mockEprint):
        dictResult = fdictFetchRemoteHashes(
            "2401.12345",
            ["figures/fig1.png", "figures/missing.png"],
            sCacheDir=str(tmp_path),
        )
    assert dictResult["figures/fig1.png"] == _fsExpectedSha256(baFig1)
    assert dictResult["figures/missing.png"] is None


def test_ambiguous_basename_raises_without_path_map(tmp_path):
    baTarball = _fbuildTarballBytes([
        ("dirA/fig1.png", b"A"),
        ("dirB/fig1.png", b"B"),
    ])
    mockMetadata = _fmockMetadataResponse("2401.12345", "v1")
    mockEprint = _fmockEprintResponse(baTarball)
    with _fnInstallHttpStubs(mockMetadata, mockEprint):
        with pytest.raises(ArxivAmbiguousMatchError):
            fdictFetchRemoteHashes(
                "2401.12345",
                ["figures/fig1.png"],
                sCacheDir=str(tmp_path),
            )


def test_cached_extraction_skips_second_download(tmp_path):
    baFig1 = b"FIG-ONE"
    baTarball = _fbuildTarballBytes([("paper/figs/fig1.png", baFig1)])
    mockMetadata1 = _fmockMetadataResponse("2401.12345", "v1")
    mockEprint = _fmockEprintResponse(baTarball)
    mockMetadata2 = _fmockMetadataResponse("2401.12345", "v1")
    with patch(
        "vaibify.reproducibility.arxivClient.requests.get",
        side_effect=[mockMetadata1, mockEprint, mockMetadata2],
    ) as mockGet:
        dictFirst = fdictFetchRemoteHashes(
            "2401.12345",
            ["figures/fig1.png"],
            sCacheDir=str(tmp_path),
        )
        dictSecond = fdictFetchRemoteHashes(
            "2401.12345",
            ["figures/fig1.png"],
            sCacheDir=str(tmp_path),
        )
    assert dictFirst == dictSecond
    assert mockGet.call_count == 3


def test_eprint_404_raises_not_found(tmp_path):
    baTarball = b""
    mockMetadata = _fmockMetadataResponse("2401.12345", "v1")
    mockEprint = _fmockEprintResponse(baTarball, iStatusCode=404)
    with _fnInstallHttpStubs(mockMetadata, mockEprint):
        with pytest.raises(ArxivNotFoundError):
            fdictFetchRemoteHashes(
                "2401.12345",
                ["figures/fig1.png"],
                sCacheDir=str(tmp_path),
            )


def test_eprint_oversize_aborts_download(tmp_path):
    iCeiling = arxivClient._I_MAX_UNCOMPRESSED_BYTES
    baLarge = b"\x00" * (iCeiling + 1024)
    mockMetadata = _fmockMetadataResponse("2401.12345", "v1")
    mockEprint = _fmockEprintResponse(baLarge)
    with _fnInstallHttpStubs(mockMetadata, mockEprint):
        with pytest.raises(ArxivExtractionError):
            fdictFetchRemoteHashes(
                "2401.12345",
                ["figures/fig1.png"],
                sCacheDir=str(tmp_path),
            )


def test_path_map_pointing_at_missing_tarball_path_raises(tmp_path):
    """A wrong ``dictPathMap`` entry must surface as a config error,
    not silently report drift — drift would hide the misconfiguration
    from the user."""
    baFig = b"FIG-BYTES"
    baTarball = _fbuildTarballBytes([("paper/figs/fig.pdf", baFig)])
    mockMetadata = _fmockMetadataResponse("2401.12345", "v1")
    mockEprint = _fmockEprintResponse(baTarball)
    dictPathMap = {"figures/local.pdf": "paper/figs/typo.pdf"}
    with _fnInstallHttpStubs(mockMetadata, mockEprint):
        with pytest.raises(ArxivPathMapError):
            fdictFetchRemoteHashes(
                "2401.12345",
                ["figures/local.pdf"],
                dictPathMap=dictPathMap,
                sCacheDir=str(tmp_path),
            )


def test_old_style_arxiv_id_resolves_version_correctly():
    """Old-style IDs like astro-ph/0601001 must yield a clean vN suffix."""
    mockResponse = _fmockMetadataResponse("astro-ph/0601001", "v3")
    with patch(
        "vaibify.reproducibility.arxivClient.requests.get",
        return_value=mockResponse,
    ):
        sVersion = fsResolveLatestVersion("astro-ph/0601001")
    assert sVersion == "v3"


def test_old_style_arxiv_id_produces_safe_extraction_path(tmp_path):
    """The slash in an old-style ID must not create a nested filesystem
    path under the cache root — it must be sanitised to a single
    directory name."""
    sExtractDir = arxivClient._fsBuildExtractionPath(
        str(tmp_path), "astro-ph/0601001", "v1",
    )
    sParent = os.path.dirname(sExtractDir)
    sLeaf = os.path.basename(sExtractDir)
    assert sParent == str(tmp_path)
    assert "/" not in sLeaf
    assert sLeaf == "astro-ph_0601001v1"


def test_old_style_arxiv_id_end_to_end_hashes_figures(tmp_path):
    """Full happy-path run for astro-ph/0601001v1 with one figure."""
    baFig = b"OLD-STYLE-FIGURE-BYTES"
    baTarball = _fbuildTarballBytes([("paper/fig1.png", baFig)])
    mockMetadata = _fmockMetadataResponse("astro-ph/0601001", "v1")
    mockEprint = _fmockEprintResponse(baTarball)
    with _fnInstallHttpStubs(mockMetadata, mockEprint):
        dictResult = fdictFetchRemoteHashes(
            "astro-ph/0601001",
            ["figures/fig1.png"],
            sCacheDir=str(tmp_path),
        )
    assert dictResult == {"figures/fig1.png": _fsExpectedSha256(baFig)}


def test_partial_extraction_is_purged_before_retry(tmp_path):
    """A hostile tarball that errors mid-extraction must not poison the
    cache — the next call with a clean tarball at the same (id, version)
    must succeed by purging the partial directory first."""
    baFig = b"GOOD-FIG"
    # First call: hostile tarball that fails after writing one good member.
    baHostileTarball = _fbuildTarballBytes([
        ("paper/good.png", baFig),
        ("../escape.txt", b"X"),
    ])
    mockMetadataOne = _fmockMetadataResponse("2401.12345", "v1")
    mockEprintHostile = _fmockEprintResponse(baHostileTarball)
    with _fnInstallHttpStubs(mockMetadataOne, mockEprintHostile):
        with pytest.raises(ArxivExtractionError):
            fdictFetchRemoteHashes(
                "2401.12345", ["figures/good.png"],
                sCacheDir=str(tmp_path),
            )
    sExtractDir = arxivClient._fsBuildExtractionPath(
        str(tmp_path), "2401.12345", "v1",
    )
    assert not os.path.exists(sExtractDir), (
        "Partial extraction must be purged on failure"
    )
    # Second call with the same (id, version): clean tarball.
    baCleanTarball = _fbuildTarballBytes([("paper/good.png", baFig)])
    mockMetadataTwo = _fmockMetadataResponse("2401.12345", "v1")
    mockEprintClean = _fmockEprintResponse(baCleanTarball)
    with _fnInstallHttpStubs(mockMetadataTwo, mockEprintClean):
        dictResult = fdictFetchRemoteHashes(
            "2401.12345", ["figures/good.png"],
            sCacheDir=str(tmp_path),
        )
    assert dictResult == {"figures/good.png": _fsExpectedSha256(baFig)}


def test_completed_extraction_is_reused(tmp_path):
    """A successful extraction marked with the sentinel must be reused
    on the next call — no second e-print download."""
    baFig = b"FIG-ONE"
    baTarball = _fbuildTarballBytes([("paper/fig.png", baFig)])
    mockMetadataOne = _fmockMetadataResponse("2401.12345", "v1")
    mockEprint = _fmockEprintResponse(baTarball)
    mockMetadataTwo = _fmockMetadataResponse("2401.12345", "v1")
    with patch(
        "vaibify.reproducibility.arxivClient.requests.get",
        side_effect=[mockMetadataOne, mockEprint, mockMetadataTwo],
    ) as mockGet:
        fdictFetchRemoteHashes(
            "2401.12345", ["figures/fig.png"], sCacheDir=str(tmp_path),
        )
        fdictFetchRemoteHashes(
            "2401.12345", ["figures/fig.png"], sCacheDir=str(tmp_path),
        )
    assert mockGet.call_count == 3  # 2 metadata + 1 e-print


def test_sentinel_file_is_not_exposed_as_tarball_entry(tmp_path):
    """The extraction-complete sentinel must never appear in the
    {tarball-relpath: hash} map produced for the comparator."""
    baFig = b"FIG"
    baTarball = _fbuildTarballBytes([("paper/fig.png", baFig)])
    mockMetadata = _fmockMetadataResponse("2401.12345", "v1")
    mockEprint = _fmockEprintResponse(baTarball)
    with _fnInstallHttpStubs(mockMetadata, mockEprint):
        fdictFetchRemoteHashes(
            "2401.12345", ["figures/fig.png"], sCacheDir=str(tmp_path),
        )
    sExtractDir = arxivClient._fsBuildExtractionPath(
        str(tmp_path), "2401.12345", "v1",
    )
    dictHashes = arxivClient._fdictHashTreeByRelPath(sExtractDir)
    assert arxivClient._S_EXTRACTION_COMPLETE_SENTINEL not in dictHashes
