"""Tests for vaibify.gui.dependencyScanner scanning engine."""

import pytest

from vaibify.gui.dependencyScanner import (
    fbLooksLikeDataFile,
    fbLooksLikeFilePath,
    flistScanForLoadCalls,
    fsDetectLanguage,
    fsExtractStringLiteral,
    _flistHarvestStringLiterals,
    _flistMergeAndDeduplicate,
)


# ── Language detection ─────────────────────────────────────────


class TestDetectLanguage:

    def test_fsDetectLanguage_python_extension(self):
        assert fsDetectLanguage("analysis.py") == "python"

    def test_fsDetectLanguage_r_extension(self):
        assert fsDetectLanguage("script.R") == "r"

    def test_fsDetectLanguage_c_extension(self):
        assert fsDetectLanguage("main.c") == "c"

    def test_fsDetectLanguage_cpp_extension(self):
        assert fsDetectLanguage("solver.cpp") == "c"

    def test_fsDetectLanguage_fortran_extension(self):
        assert fsDetectLanguage("model.f90") == "fortran"

    def test_fsDetectLanguage_rust_extension(self):
        assert fsDetectLanguage("main.rs") == "rust"

    def test_fsDetectLanguage_javascript_extension(self):
        assert fsDetectLanguage("index.js") == "javascript"

    def test_fsDetectLanguage_typescript_extension(self):
        assert fsDetectLanguage("app.ts") == "javascript"

    def test_fsDetectLanguage_perl_extension(self):
        assert fsDetectLanguage("parse.pl") == "perl"

    def test_fsDetectLanguage_shell_extension(self):
        assert fsDetectLanguage("run.sh") == "shell"

    def test_fsDetectLanguage_julia_extension(self):
        assert fsDetectLanguage("compute.jl") == "julia"

    def test_fsDetectLanguage_matlab_extension(self):
        assert fsDetectLanguage("process.m") == "matlab"

    def test_fsDetectLanguage_unknown_extension(self):
        assert fsDetectLanguage("data.xyz") == "unknown"

    def test_fsDetectLanguage_shebang_python(self):
        assert fsDetectLanguage(
            "script", sFirstLine="#!/usr/bin/env python3"
        ) == "python"

    def test_fsDetectLanguage_shebang_bash(self):
        assert fsDetectLanguage(
            "script", sFirstLine="#!/bin/bash"
        ) == "shell"

    def test_fsDetectLanguage_shebang_perl(self):
        assert fsDetectLanguage(
            "script", sFirstLine="#!/usr/bin/perl"
        ) == "perl"

    def test_fsDetectLanguage_command_prefix_python(self):
        assert fsDetectLanguage(
            "script", sCommandPrefix="python"
        ) == "python"

    def test_fsDetectLanguage_command_prefix_rscript(self):
        assert fsDetectLanguage(
            "script", sCommandPrefix="Rscript"
        ) == "r"

    def test_fsDetectLanguage_command_prefix_julia(self):
        assert fsDetectLanguage(
            "script", sCommandPrefix="julia"
        ) == "julia"

    def test_fsDetectLanguage_command_prefix_node(self):
        assert fsDetectLanguage(
            "script", sCommandPrefix="node"
        ) == "javascript"


# ── String literal extraction ──────────────────────────────────


class TestExtractStringLiteral:

    def test_fsExtractStringLiteral_double_quotes(self):
        assert fsExtractStringLiteral('"output.csv"') == "output.csv"

    def test_fsExtractStringLiteral_single_quotes(self):
        assert fsExtractStringLiteral("'data.h5'") == "data.h5"

    def test_fsExtractStringLiteral_no_quotes(self):
        assert fsExtractStringLiteral("plain.txt") == "plain.txt"

    def test_fsExtractStringLiteral_empty(self):
        assert fsExtractStringLiteral("") == ""


# ── fbLooksLikeFilePath ───────────────────────────────────────


class TestLooksLikeFilePath:

    def test_fbLooksLikeFilePath_csv(self):
        assert fbLooksLikeFilePath("output.csv") is True

    def test_fbLooksLikeFilePath_path_with_slash(self):
        assert fbLooksLikeFilePath("data/results.dat") is True

    def test_fbLooksLikeFilePath_template_variable(self):
        assert fbLooksLikeFilePath("{Step01.output}") is True

    def test_fbLooksLikeFilePath_http_url(self):
        assert fbLooksLikeFilePath("https://example.com/data") is False

    def test_fbLooksLikeFilePath_ftp_url(self):
        assert fbLooksLikeFilePath("ftp://server/file") is False

    def test_fbLooksLikeFilePath_module_name(self):
        assert fbLooksLikeFilePath("numpy") is False

    def test_fbLooksLikeFilePath_empty(self):
        assert fbLooksLikeFilePath("") is False

    def test_fbLooksLikeFilePath_whitespace(self):
        assert fbLooksLikeFilePath("   ") is False

    def test_fbLooksLikeFilePath_hdf5(self):
        assert fbLooksLikeFilePath("simulation.hdf5") is True


# ── Python scanner ─────────────────────────────────────────────


class TestScanPython:

    def test_np_load(self):
        sCode = 'data = np.load("results.npy")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "results.npy"
        assert listResult[0]["sLoadFunction"] == "np.load"
        assert listResult[0]["iLineNumber"] == 1

    def test_pd_read_csv(self):
        sCode = "df = pd.read_csv('catalog.csv')\n"
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "catalog.csv"
        assert listResult[0]["sLoadFunction"] == "pd.read_csv"

    def test_open_builtin(self):
        sCode = 'f = open("config.dat")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "config.dat"
        assert listResult[0]["sLoadFunction"] == "open"

    def test_h5py_file(self):
        sCode = 'h5 = h5py.File("archive.h5", "r")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "archive.h5"
        assert listResult[0]["sLoadFunction"] == "h5py.File"

    def test_os_path_join(self):
        sCode = 'sPath = os.path.join("data/subdir", sFilename)\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "data/subdir"

    def test_no_match_variable_argument(self):
        sCode = "data = np.load(sFilePath)\n"
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 0

    def test_multiple_loads(self):
        sCode = (
            'a = np.load("first.npy")\n'
            'b = pd.read_csv("second.csv")\n'
        )
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 2

    def test_json_load_open(self):
        sCode = 'cfg = json.load(open("params.json"))\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert any(
            d["sFileName"] == "params.json" for d in listResult
        )

    def test_comment_lines_skipped(self):
        sCode = '# np.load("skipped.npy")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 0

    def test_pd_read_excel(self):
        sCode = 'df = pd.read_excel("budget.xlsx")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "budget.xlsx"

    def test_fits_open(self):
        sCode = 'hdu = fits.open("image.fits")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "image.fits"


# ── R scanner ──────────────────────────────────────────────────


class TestScanR:

    def test_read_csv(self):
        sCode = 'df <- read.csv("data.csv")\n'
        listResult = flistScanForLoadCalls(sCode, "r")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "data.csv"
        assert listResult[0]["sLoadFunction"] == "read.csv"

    def test_read_csv_tidyverse(self):
        sCode = 'df <- read_csv("results.csv")\n'
        listResult = flistScanForLoadCalls(sCode, "r")
        assert len(listResult) == 1
        assert listResult[0]["sLoadFunction"] == "read_csv"

    def test_fread(self):
        sCode = 'dt <- fread("big.csv")\n'
        listResult = flistScanForLoadCalls(sCode, "r")
        assert len(listResult) == 1
        assert listResult[0]["sLoadFunction"] == "fread"


# ── C scanner ──────────────────────────────────────────────────


class TestScanC:

    def test_fopen(self):
        sCode = 'fp = fopen("input.dat", "r");\n'
        listResult = flistScanForLoadCalls(sCode, "c")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "input.dat"
        assert listResult[0]["sLoadFunction"] == "fopen"

    def test_ifstream(self):
        sCode = 'ifstream infile("config.txt");\n'
        listResult = flistScanForLoadCalls(sCode, "c")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "config.txt"

    def test_h5fopen(self):
        sCode = 'hid_t file = H5Fopen("archive.h5", H5F_ACC_RDONLY);\n'
        listResult = flistScanForLoadCalls(sCode, "c")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "archive.h5"


# ── Fortran scanner ───────────────────────────────────────────


class TestScanFortran:

    def test_open_file(self):
        sCode = "OPEN(UNIT=10, FILE='orbit.dat', STATUS='OLD')\n"
        listResult = flistScanForLoadCalls(sCode, "fortran")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "orbit.dat"
        assert listResult[0]["sLoadFunction"] == "OPEN(FILE=)"

    def test_open_file_lowercase(self):
        sCode = "open(unit=10, file='trajectory.csv')\n"
        listResult = flistScanForLoadCalls(sCode, "fortran")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "trajectory.csv"


# ── Rust scanner ──────────────────────────────────────────────


class TestScanRust:

    def test_file_open(self):
        sCode = 'let f = File::open("data.bin");\n'
        listResult = flistScanForLoadCalls(sCode, "rust")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "data.bin"
        assert listResult[0]["sLoadFunction"] == "File::open"

    def test_fs_read_to_string(self):
        sCode = 'let s = fs::read_to_string("config.toml");\n'
        listResult = flistScanForLoadCalls(sCode, "rust")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "config.toml"


# ── JavaScript scanner ────────────────────────────────────────


class TestScanJavaScript:

    def test_fs_readFileSync(self):
        sCode = 'var data = fs.readFileSync("input.json");\n'
        listResult = flistScanForLoadCalls(sCode, "javascript")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "input.json"

    def test_require(self):
        sCode = 'var cfg = require("./config.json");\n'
        listResult = flistScanForLoadCalls(sCode, "javascript")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "./config.json"


# ── Perl scanner ──────────────────────────────────────────────


class TestScanPerl:

    def test_open(self):
        sCode = 'open(FH, "data.txt");\n'
        listResult = flistScanForLoadCalls(sCode, "perl")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "data.txt"
        assert listResult[0]["sLoadFunction"] == "open"


# ── Shell scanner ─────────────────────────────────────────────


class TestScanShell:

    def test_cat(self):
        sCode = "cat results.csv\n"
        listResult = flistScanForLoadCalls(sCode, "shell")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "results.csv"
        assert listResult[0]["sLoadFunction"] == "cat"

    def test_redirect(self):
        sCode = "while read line; do echo $line; done < input.dat\n"
        listResult = flistScanForLoadCalls(sCode, "shell")
        assert any(d["sFileName"] == "input.dat" for d in listResult)

    def test_source(self):
        sCode = "source config.sh\n"
        listResult = flistScanForLoadCalls(sCode, "shell")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "config.sh"


# ── Julia scanner ─────────────────────────────────────────────


class TestScanJulia:

    def test_csv_read(self):
        sCode = 'df = CSV.read("table.csv", DataFrame)\n'
        listResult = flistScanForLoadCalls(sCode, "julia")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "table.csv"
        assert listResult[0]["sLoadFunction"] == "CSV.read"

    def test_open(self):
        sCode = 'f = open("log.txt", "r")\n'
        listResult = flistScanForLoadCalls(sCode, "julia")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "log.txt"


# ── MATLAB scanner ────────────────────────────────────────────


class TestScanMatlab:

    def test_load(self):
        sCode = "data = load('results.mat');\n"
        listResult = flistScanForLoadCalls(sCode, "matlab")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "results.mat"
        assert listResult[0]["sLoadFunction"] == "load"

    def test_fopen(self):
        sCode = "fid = fopen('output.dat', 'r');\n"
        listResult = flistScanForLoadCalls(sCode, "matlab")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "output.dat"

    def test_readtable(self):
        sCode = "T = readtable('measurements.csv');\n"
        listResult = flistScanForLoadCalls(sCode, "matlab")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "measurements.csv"


# ── Edge cases ─────────────────────────────────────────────────


class TestEdgeCases:

    def test_unknown_language_returns_empty(self):
        listResult = flistScanForLoadCalls("anything", "unknown")
        assert listResult == []

    def test_empty_source(self):
        listResult = flistScanForLoadCalls("", "python")
        assert listResult == []

    def test_no_matches(self):
        sCode = "x = 42\ny = x + 1\n"
        listResult = flistScanForLoadCalls(sCode, "python")
        assert listResult == []

    def test_url_filtered_out(self):
        sCode = 'data = pd.read_csv("https://example.com/data.csv")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 0

    def test_multiline_source(self):
        sCode = (
            "import numpy as np\n"
            "\n"
            'a = np.load("first.npy")\n'
            "# a comment\n"
            'b = np.load("second.npy")\n'
        )
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 2
        assert listResult[0]["iLineNumber"] == 3
        assert listResult[1]["iLineNumber"] == 5

    def test_line_numbers_correct(self):
        sCode = "x = 1\ny = 2\nz = np.load('third.npy')\n"
        listResult = flistScanForLoadCalls(sCode, "python")
        assert listResult[0]["iLineNumber"] == 3


# ── Whitespace variations ──────────────────────────────────────


class TestWhitespaceVariations:

    def test_np_load_with_extra_spaces(self):
        sCode = 'data = np.load(  "file.npy"  )\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "file.npy"


# ── Comment skipping per language ──────────────────────────────


class TestCommentSkipping:

    def test_c_double_slash_comment_skipped(self):
        sCode = '// fopen("secret.dat", "r");\n'
        listResult = flistScanForLoadCalls(sCode, "c")
        assert len(listResult) == 0

    def test_c_code_not_skipped(self):
        sCode = 'fp = fopen("real.dat", "r");\n'
        listResult = flistScanForLoadCalls(sCode, "c")
        assert len(listResult) == 1

    def test_fortran_exclamation_comment_skipped(self):
        sCode = "! OPEN(UNIT=10, FILE='ghost.dat')\n"
        listResult = flistScanForLoadCalls(sCode, "fortran")
        assert len(listResult) == 0

    def test_fortran_code_not_skipped(self):
        sCode = "OPEN(UNIT=10, FILE='real.dat', STATUS='OLD')\n"
        listResult = flistScanForLoadCalls(sCode, "fortran")
        assert len(listResult) == 1

    def test_matlab_percent_comment_skipped(self):
        sCode = "% data = load('phantom.mat');\n"
        listResult = flistScanForLoadCalls(sCode, "matlab")
        assert len(listResult) == 0

    def test_matlab_code_not_skipped(self):
        sCode = "data = load('real.mat');\n"
        listResult = flistScanForLoadCalls(sCode, "matlab")
        assert len(listResult) == 1


# ── Shell heredoc exclusion ────────────────────────────────────


class TestShellHeredoc:

    def test_heredoc_not_matched(self):
        sCode = "cat <<EOF\nsome content\nEOF\n"
        listResult = flistScanForLoadCalls(sCode, "shell")
        listRedirectMatches = [
            d for d in listResult if d["sLoadFunction"] == "<"
        ]
        assert len(listRedirectMatches) == 0


# ── JavaScript require filtering ───────────────────────────────


class TestJavaScriptRequireFiltering:

    def test_require_module_filtered_out(self):
        sCode = 'var express = require("express");\n'
        listResult = flistScanForLoadCalls(sCode, "javascript")
        assert len(listResult) == 0

    def test_require_relative_path_kept(self):
        sCode = 'var cfg = require("./config.json");\n'
        listResult = flistScanForLoadCalls(sCode, "javascript")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "./config.json"

    def test_require_parent_path_kept(self):
        sCode = 'var data = require("../data/input.json");\n'
        listResult = flistScanForLoadCalls(sCode, "javascript")
        assert len(listResult) == 1

    def test_require_data_extension_kept(self):
        sCode = 'var tbl = require("table.csv");\n'
        listResult = flistScanForLoadCalls(sCode, "javascript")
        assert len(listResult) == 1


# ── Pandas long-form detection ─────────────────────────────────


class TestPandasLongForm:

    def test_pandas_read_csv(self):
        sCode = 'df = pandas.read_csv("catalog.csv")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "catalog.csv"
        assert listResult[0]["sLoadFunction"] == "pd.read_csv"

    def test_pandas_read_excel(self):
        sCode = 'df = pandas.read_excel("budget.xlsx")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "budget.xlsx"


# ── fits.open negative lookbehind ──────────────────────────────


class TestFitsOpenLookbehind:

    def test_fits_open_detected(self):
        sCode = 'hdu = fits.open("image.fits")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "image.fits"

    def test_benefits_open_not_detected(self):
        sCode = 'x = benefits.open("report.pdf")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        listFitsMatches = [
            d for d in listResult if d["sLoadFunction"] == "fits.open"
        ]
        assert len(listFitsMatches) == 0


# ── Go scanner ─────────────────────────────────────────────────


class TestScanGo:

    def test_os_open(self):
        sCode = 'f, err := os.Open("data.csv")\n'
        listResult = flistScanForLoadCalls(sCode, "go")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "data.csv"
        assert listResult[0]["sLoadFunction"] == "os.Open"

    def test_os_read_file(self):
        sCode = 'content, err := os.ReadFile("config.yaml")\n'
        listResult = flistScanForLoadCalls(sCode, "go")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "config.yaml"

    def test_ioutil_read_file(self):
        sCode = 'data, err := ioutil.ReadFile("legacy.dat")\n'
        listResult = flistScanForLoadCalls(sCode, "go")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "legacy.dat"

    def test_os_open_file(self):
        sCode = 'f, err := os.OpenFile("output.log", os.O_RDONLY, 0644)\n'
        listResult = flistScanForLoadCalls(sCode, "go")
        assert len(listResult) == 1
        assert listResult[0]["sFileName"] == "output.log"

    def test_go_comment_skipped(self):
        sCode = '// f, err := os.Open("hidden.csv")\n'
        listResult = flistScanForLoadCalls(sCode, "go")
        assert len(listResult) == 0


# ── Go language detection ──────────────────────────────────────


class TestGoLanguageDetection:

    def test_fsDetectLanguage_go_extension(self):
        assert fsDetectLanguage("main.go") == "go"

    def test_fsDetectLanguage_go_command_prefix(self):
        assert fsDetectLanguage(
            "script", sCommandPrefix="go"
        ) == "go"


# ── String harvesting tests ────────────────────────────────────


class TestStringHarvest:

    def test_string_harvest_variable_assignment(self):
        sCode = (
            'sPath = "results.npy"\n'
            'data = np.loadtxt(sPath)\n'
        )
        listResult = flistScanForLoadCalls(sCode, "python")
        listFileNames = [d["sFileName"] for d in listResult]
        assert "results.npy" in listFileNames

    def test_string_harvest_path_component(self):
        sCode = 'sFile = "../MaximumLikelihood/maxlike_results.txt"\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        listFileNames = [d["sFileName"] for d in listResult]
        assert "../MaximumLikelihood/maxlike_results.txt" in listFileNames

    def test_string_harvest_comment_filtered(self):
        sCode = '# "old_data.csv"\n'
        listResult = _flistHarvestStringLiterals(sCode, "python")
        assert len(listResult) == 0

    def test_string_harvest_non_file_filtered(self):
        sCode = (
            'sVersion = "v2.0"\n'
            'sMode = "readonly"\n'
        )
        listResult = _flistHarvestStringLiterals(sCode, "python")
        assert len(listResult) == 0

    def test_merge_deduplication(self):
        sCode = 'data = np.load("data.npy")\n'
        listResult = flistScanForLoadCalls(sCode, "python")
        listDataNpy = [
            d for d in listResult if d["sFileName"] == "data.npy"
        ]
        assert len(listDataNpy) == 1
        assert listDataNpy[0]["sLoadFunction"] == "np.load"

    def test_string_harvest_fortran(self):
        sCode = 'CHARACTER(LEN=50) :: fname = "output.dat"\n'
        listResult = flistScanForLoadCalls(sCode, "fortran")
        listFileNames = [d["sFileName"] for d in listResult]
        assert "output.dat" in listFileNames

    def test_string_harvest_r(self):
        sCode = 'sPath <- "../step01/results.csv"\n'
        listResult = flistScanForLoadCalls(sCode, "r")
        listFileNames = [d["sFileName"] for d in listResult]
        assert "../step01/results.csv" in listFileNames

    def test_string_harvest_shell(self):
        sCode = 'INPUT_FILE="data.txt"\n'
        listResult = flistScanForLoadCalls(sCode, "shell")
        listFileNames = [d["sFileName"] for d in listResult]
        assert "data.txt" in listFileNames


# ── Upstream fallback tests ────────────────────────────────────


class TestUpstreamFallback:

    def test_upstream_fallback_empty(self):
        from vaibify.gui.pipelineServer import _flistCollectUpstreamOutputs
        dictWorkflow = {
            "listSteps": [
                {"sName": "Step01", "saDataFiles": ["output.csv"]},
            ],
        }
        listResult = _flistCollectUpstreamOutputs(dictWorkflow, 0)
        assert listResult == []
