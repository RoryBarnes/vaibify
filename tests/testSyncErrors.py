"""Tests for sync error classification in syncDispatcher.py."""

import pytest
from vaibify.gui.syncDispatcher import (
    fdictClassifyError, fdictSyncResult,
)


class TestClassifyError:
    """Test fdictClassifyError pattern matching."""

    def test_fbAuthenticationFailure(self):
        dictResult = fdictClassifyError(
            128, "fatal: Authentication failed for 'https://...'"
        )
        assert dictResult["sErrorType"] == "auth"

    def test_fbHttp401(self):
        dictResult = fdictClassifyError(
            1, "HTTP 401 Unauthorized"
        )
        assert dictResult["sErrorType"] == "auth"

    def test_fbHttp403(self):
        dictResult = fdictClassifyError(
            1, "403 Forbidden: insufficient permissions"
        )
        assert dictResult["sErrorType"] == "auth"

    def test_fbRateLimit(self):
        dictResult = fdictClassifyError(
            1, "Error: rate limit exceeded. Try again later."
        )
        assert dictResult["sErrorType"] == "rateLimit"

    def test_fbHttp429(self):
        dictResult = fdictClassifyError(
            1, "HTTP 429 Too Many Requests"
        )
        assert dictResult["sErrorType"] == "rateLimit"

    def test_fbNotFound(self):
        dictResult = fdictClassifyError(
            1, "Error: repository not found"
        )
        assert dictResult["sErrorType"] == "notFound"

    def test_fbHttp404(self):
        dictResult = fdictClassifyError(
            1, "HTTP 404: deposit not found"
        )
        assert dictResult["sErrorType"] == "notFound"

    def test_fbNetworkTimeout(self):
        dictResult = fdictClassifyError(
            1, "fatal: unable to access: Connection timeout"
        )
        assert dictResult["sErrorType"] == "network"

    def test_fbConnectionRefused(self):
        dictResult = fdictClassifyError(
            1, "Connection refused to git.overleaf.com"
        )
        assert dictResult["sErrorType"] == "network"

    def test_fbUnknownError(self):
        dictResult = fdictClassifyError(
            1, "some random failure message"
        )
        assert dictResult["sErrorType"] == "unknown"

    def test_fbOutputPreserved(self):
        sOutput = "detailed error description here"
        dictResult = fdictClassifyError(1, sOutput)
        assert dictResult["sMessage"] == sOutput


class TestSyncResult:
    """Test fdictSyncResult wrapper."""

    def test_fbSuccessResult(self):
        dictResult = fdictSyncResult(0, "abc1234\n")
        assert dictResult["bSuccess"] is True
        assert dictResult["sOutput"] == "abc1234"

    def test_fbFailureResult(self):
        dictResult = fdictSyncResult(
            128, "Authentication failed"
        )
        assert dictResult["bSuccess"] is False
        assert dictResult["sErrorType"] == "auth"

    def test_fbUnknownFailure(self):
        dictResult = fdictSyncResult(1, "oops")
        assert dictResult["bSuccess"] is False
        assert dictResult["sErrorType"] == "unknown"


class TestCollectOutputFiles:
    """Test flistCollectOutputFiles file collection."""

    def test_fbCollectsAllFiles(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {
                    "saDataFiles": ["data.h5"],
                    "saPlotFiles": ["fig.pdf"],
                },
            ],
        }
        listFiles = flistCollectOutputFiles(dictWorkflow, {})
        assert len(listFiles) == 2
        listPaths = [d["sPath"] for d in listFiles]
        assert "data.h5" in listPaths
        assert "fig.pdf" in listPaths

    def test_fbIncludesCategory(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {
                    "saDataFiles": [],
                    "saPlotFiles": ["fig.pdf"],
                    "dictPlotFileCategories": {
                        "fig.pdf": "supporting",
                    },
                },
            ],
        }
        listFiles = flistCollectOutputFiles(dictWorkflow, {})
        assert listFiles[0]["sCategory"] == "supporting"

    def test_fbDataFilesAlwaysArchive(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {"saDataFiles": ["data.h5"], "saPlotFiles": []},
            ],
        }
        listFiles = flistCollectOutputFiles(dictWorkflow, {})
        assert listFiles[0]["sCategory"] == "archive"

    def test_fbResolvesVariablesInPaths(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {
                    "saDataFiles": [],
                    "saPlotFiles": [
                        "{sPlotDirectory}/fig.{sFigureType}",
                    ],
                },
            ],
        }
        dictVars = {
            "sPlotDirectory": "Plots", "sFigureType": "pdf",
        }
        listFiles = flistCollectOutputFiles(
            dictWorkflow, {}, dictVars)
        assert listFiles[0]["sPath"] == "Plots/fig.pdf"

    def test_fbOverleafFilterKeepsLatexExtensions(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {
                    "saDataFiles": [
                        "data.json", "samples.npy", "notes.txt",
                    ],
                    "saPlotFiles": [
                        "fig.pdf", "photo.png", "draft.tex",
                        "refs.bib",
                    ],
                },
            ],
        }
        listFiles = flistCollectOutputFiles(
            dictWorkflow, {}, {}, "overleaf")
        listPaths = sorted(d["sPath"] for d in listFiles)
        assert listPaths == [
            "draft.tex", "fig.pdf", "photo.png", "refs.bib",
        ]

    def test_fbOverleafFilterDropsUnsupportedExtensions(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {
                    "saDataFiles": ["out.csv", "arr.npz"],
                    "saPlotFiles": ["model.pkl"],
                },
            ],
        }
        listFiles = flistCollectOutputFiles(
            dictWorkflow, {}, {}, "overleaf")
        assert listFiles == []

    def test_fbOverleafFilterIsExtensionCaseInsensitive(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {
                    "saDataFiles": [],
                    "saPlotFiles": ["Figure.PDF", "Photo.JPEG"],
                },
            ],
        }
        listFiles = flistCollectOutputFiles(
            dictWorkflow, {}, {}, "overleaf")
        assert len(listFiles) == 2

    def test_fbZenodoServiceReturnsEverything(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {
                    "saDataFiles": ["data.h5", "log.txt"],
                    "saPlotFiles": ["fig.pdf"],
                },
            ],
        }
        listFiles = flistCollectOutputFiles(
            dictWorkflow, {}, {}, "zenodo")
        assert len(listFiles) == 3

    def test_fbOverleafFilterSkipsExtensionlessPaths(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {
                    "saDataFiles": ["Makefile"],
                    "saPlotFiles": ["fig.pdf"],
                },
            ],
        }
        listFiles = flistCollectOutputFiles(
            dictWorkflow, {}, {}, "overleaf")
        assert [d["sPath"] for d in listFiles] == ["fig.pdf"]

    def test_fbWorkflowRootMakesPathsAbsolute(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {
                    "sDirectory": "CumulativeXuvAndCosmicShoreline",
                    "saDataFiles": [],
                    "saPlotFiles": [
                        "{sPlotDirectory}/CosmicShoreline."
                        "{sFigureType}",
                    ],
                },
            ],
        }
        dictVars = {
            "sPlotDirectory": "Plot", "sFigureType": "pdf",
        }
        listFiles = flistCollectOutputFiles(
            dictWorkflow, {}, dictVars, None,
            "/workspace/GJ1132_XUV",
        )
        assert listFiles[0]["sPath"] == (
            "/workspace/GJ1132_XUV/CumulativeXuvAndCosmicShoreline/"
            "Plot/CosmicShoreline.pdf"
        )

    def test_fbAbsoluteStepDirIsNotDoubleJoined(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {
                    "sDirectory": "/workspace/already/absolute",
                    "saDataFiles": [],
                    "saPlotFiles": ["fig.pdf"],
                },
            ],
        }
        listFiles = flistCollectOutputFiles(
            dictWorkflow, {}, {}, None, "/workspace/ignored",
        )
        assert listFiles[0]["sPath"] == (
            "/workspace/already/absolute/fig.pdf"
        )

    def test_fbWorkflowRootOmittedLeavesRelativePaths(self):
        from vaibify.gui.syncDispatcher import flistCollectOutputFiles
        dictWorkflow = {
            "listSteps": [
                {
                    "sDirectory": "step1",
                    "saDataFiles": [],
                    "saPlotFiles": ["fig.pdf"],
                },
            ],
        }
        listFiles = flistCollectOutputFiles(dictWorkflow, {})
        assert listFiles[0]["sPath"] == "step1/fig.pdf"
