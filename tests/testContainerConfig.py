"""Tests for vaibify.config.containerConfig pipe-delimited I/O."""

import pytest

from vaibify.config.containerConfig import (
    flistParseContainerConf,
    fnWriteContainerConf,
    flistConvertFromProjectConfig,
)


def _fnWriteTempConf(tmp_path, sContent):
    """Write sContent to a temporary container.conf and return its path."""
    pathFile = tmp_path / "container.conf"
    pathFile.write_text(sContent)
    return str(pathFile)


def test_flistParseContainerConf_parses_valid_file(tmp_path):
    sContent = (
        "vplanet|https://github.com/VPLanet/vplanet|main|c_and_pip\n"
        "vplot|https://github.com/VPLanet/vplot|main|pip_editable\n"
        "bigplanet|https://github.com/VPLanet/bigplanet|main|pip_no_deps\n"
    )
    sFilePath = _fnWriteTempConf(tmp_path, sContent)

    listRepos = flistParseContainerConf(sFilePath)

    assert len(listRepos) == 3
    assert listRepos[0]["sName"] == "vplanet"
    assert listRepos[0]["sUrl"] == "https://github.com/VPLanet/vplanet"
    assert listRepos[0]["sBranch"] == "main"
    assert listRepos[0]["sInstallMethod"] == "c_and_pip"
    assert listRepos[1]["sName"] == "vplot"
    assert listRepos[2]["sInstallMethod"] == "pip_no_deps"


def test_flistParseContainerConf_skips_comments_and_blank_lines(tmp_path):
    sContent = (
        "# This is a comment\n"
        "\n"
        "  \n"
        "# Another comment\n"
        "vplanet|https://github.com/VPLanet/vplanet|main|c_and_pip\n"
        "\n"
        "vplot|https://github.com/VPLanet/vplot|main|pip_editable\n"
    )
    sFilePath = _fnWriteTempConf(tmp_path, sContent)

    listRepos = flistParseContainerConf(sFilePath)

    assert len(listRepos) == 2
    assert listRepos[0]["sName"] == "vplanet"
    assert listRepos[1]["sName"] == "vplot"


def test_fnWriteContainerConf_roundtrips(tmp_path):
    listOriginal = [
        {
            "sName": "vplanet",
            "sUrl": "https://github.com/VPLanet/vplanet",
            "sBranch": "main",
            "sInstallMethod": "c_and_pip",
        },
        {
            "sName": "vspace",
            "sUrl": "https://github.com/VPLanet/vspace",
            "sBranch": "develop",
            "sInstallMethod": "pip_editable",
        },
    ]
    sFilePath = str(tmp_path / "roundtrip.conf")

    fnWriteContainerConf(listOriginal, sFilePath)
    listParsed = flistParseContainerConf(sFilePath)

    assert len(listParsed) == len(listOriginal)
    for iIndex in range(len(listOriginal)):
        assert listParsed[iIndex]["sName"] == listOriginal[iIndex]["sName"]
        assert listParsed[iIndex]["sUrl"] == listOriginal[iIndex]["sUrl"]
        assert listParsed[iIndex]["sBranch"] == listOriginal[iIndex]["sBranch"]
        assert (
            listParsed[iIndex]["sInstallMethod"]
            == listOriginal[iIndex]["sInstallMethod"]
        )


def test_flistConvertFromProjectConfig():
    """Create a mock ProjectConfig with repos and verify conversion."""

    class MockConfig:
        listRepositories = [
            {
                "name": "vplanet",
                "url": "https://github.com/VPLanet/vplanet",
                "branch": "v3.0",
                "installMethod": "c_and_pip",
            },
            {
                "name": "alabi",
                "url": "https://github.com/user/alabi",
            },
        ]

    listConverted = flistConvertFromProjectConfig(MockConfig())

    assert len(listConverted) == 2
    assert listConverted[0]["sName"] == "vplanet"
    assert listConverted[0]["sUrl"] == "https://github.com/VPLanet/vplanet"
    assert listConverted[0]["sBranch"] == "v3.0"
    assert listConverted[0]["sInstallMethod"] == "c_and_pip"
    assert listConverted[1]["sName"] == "alabi"
    assert listConverted[1]["sBranch"] == "main"
    assert listConverted[1]["sInstallMethod"] == "pip_editable"


def test_flistParseContainerConf_raises_on_missing_file(tmp_path):
    sMissingPath = str(tmp_path / "nonexistent.conf")
    with pytest.raises(FileNotFoundError):
        flistParseContainerConf(sMissingPath)


def test_flistParseContainerConf_raises_on_malformed_line(tmp_path):
    sContent = "vplanet|https://github.com/VPLanet/vplanet|main\n"
    sFilePath = _fnWriteTempConf(tmp_path, sContent)

    with pytest.raises(ValueError, match="Expected 4"):
        flistParseContainerConf(sFilePath)
