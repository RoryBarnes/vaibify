"""Tests for deterministic test generation in vaibify.gui.testGenerator."""

import ast
import json

import numpy as np
import pytest
from unittest.mock import MagicMock

from vaibify.gui.testGenerator import (
    _fbOutputLooksValid,
    _fdictBuildIntegrityStandards,
    _fdictBuildQualitativeStandards,
    _fdictBuildQuantitativeStandards,
    _flistParseIntrospectionOutput,
    _fsBuildIntrospectionScript,
    _fsFormatSafeName,
    _fsRemoveOldTestSection,
    fsBuildIntegrityTestCode,
    fsBuildQualitativeTestCode,
    fsBuildQuantitativeTestCode,
)


def _fdictExecTemplate(tmp_path=None):
    """Execute the quantitative test template and return its namespace.

    Creates a minimal quantitative_standards.json so the template's
    module-level code can load it without error.
    """
    import tempfile
    import pathlib

    if tmp_path is None:
        tmp_path = pathlib.Path(tempfile.mkdtemp())
    sStandardsPath = str(tmp_path / "quantitative_standards.json")
    with open(sStandardsPath, "w", encoding="utf-8") as fh:
        json.dump({"fDefaultRtol": 1e-6, "listStandards": []}, fh)
    sFakeFile = str(tmp_path / "test_quantitative.py")
    sCode = fsBuildQuantitativeTestCode()
    dictLocals = {"__file__": sFakeFile}
    exec(compile(sCode, "<template>", "exec"), dictLocals)
    return dictLocals


def _fdictExecIntegrityTemplate(tmp_path=None):
    """Execute the integrity test template and return its namespace."""
    import tempfile
    import pathlib

    if tmp_path is None:
        tmp_path = pathlib.Path(tempfile.mkdtemp())
    sStandardsPath = str(tmp_path / "integrity_standards.json")
    with open(sStandardsPath, "w", encoding="utf-8") as fh:
        json.dump({"listStandards": []}, fh)
    sFakeFile = str(tmp_path / "test_integrity.py")
    sCode = fsBuildIntegrityTestCode()
    dictLocals = {"__file__": sFakeFile}
    exec(compile(sCode, "<template>", "exec"), dictLocals)
    return dictLocals


def _fdictExecQualitativeTemplate(tmp_path=None):
    """Execute the qualitative test template and return its namespace."""
    import tempfile
    import pathlib

    if tmp_path is None:
        tmp_path = pathlib.Path(tempfile.mkdtemp())
    sStandardsPath = str(tmp_path / "qualitative_standards.json")
    with open(sStandardsPath, "w", encoding="utf-8") as fh:
        json.dump({"listStandards": []}, fh)
    sFakeFile = str(tmp_path / "test_qualitative.py")
    sCode = fsBuildQualitativeTestCode()
    dictLocals = {"__file__": sFakeFile}
    exec(compile(sCode, "<template>", "exec"), dictLocals)
    return dictLocals


# -----------------------------------------------------------------------
# _fsFormatSafeName
# -----------------------------------------------------------------------


def test_fsFormatSafeName_dots_and_dashes():
    assert _fsFormatSafeName("data-file.csv") == "data_file"


def test_fsFormatSafeName_spaces():
    assert _fsFormatSafeName("my file.npy") == "my_file"


def test_fsFormatSafeName_leading_digit():
    sResult = _fsFormatSafeName("3body.dat")
    assert sResult[0].isalpha()
    assert sResult == "f3body"


def test_fsFormatSafeName_already_valid():
    assert _fsFormatSafeName("output.npy") == "output"


# -----------------------------------------------------------------------
# _fdictBuildIntegrityStandards
# -----------------------------------------------------------------------


def test_fdictBuildIntegrityStandards_produces_valid_json():
    listdictReports = [
        {
            "sFileName": "data.npy",
            "sFormat": "npy",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [100, 3],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    assert "listStandards" in dictResult
    assert len(dictResult["listStandards"]) == 1
    assert dictResult["listStandards"][0]["sFileName"] == "data.npy"


def test_fdictBuildIntegrityStandards_file_entry():
    listdictReports = [
        {
            "sFileName": "output.csv",
            "sFormat": "csv",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [50, 4],
            "listColumnNames": ["time", "flux"],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    dictEntry = dictResult["listStandards"][0]
    assert dictEntry["sFileName"] == "output.csv"
    assert dictEntry["sFormat"] == "csv"
    assert dictEntry["tExpectedShape"] == [50, 4]


def test_fdictBuildIntegrityStandards_empty_reports():
    dictResult = _fdictBuildIntegrityStandards([])
    assert dictResult["listStandards"] == []


def test_integrity_template_valid_python():
    sCode = fsBuildIntegrityTestCode()
    ast.parse(sCode)


def test_integrity_template_has_parametrize():
    sCode = fsBuildIntegrityTestCode()
    assert "pytest.mark.parametrize" in sCode
    assert "test_integrity_check" in sCode


def test_integrity_template_exec():
    _fdictExecIntegrityTemplate()


# -----------------------------------------------------------------------
# _fdictBuildQualitativeStandards
# -----------------------------------------------------------------------


def test_fdictBuildQualitativeStandards_with_columns():
    listdictReports = [
        {
            "sFileName": "results.csv",
            "sFormat": "csv",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": ["time", "temperature", "pressure"],
            "listJsonTopKeys": [],
        },
    ]
    dictResult = _fdictBuildQualitativeStandards(listdictReports)
    assert len(dictResult["listStandards"]) == 1
    dictEntry = dictResult["listStandards"][0]
    assert "time" in dictEntry["listExpectedColumns"]
    assert "temperature" in dictEntry["listExpectedColumns"]
    assert "pressure" in dictEntry["listExpectedColumns"]


def test_fdictBuildQualitativeStandards_no_strings():
    listdictReports = [
        {
            "sFileName": "data.npy",
            "sFormat": "npy",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": [],
            "listJsonTopKeys": [],
        },
    ]
    dictResult = _fdictBuildQualitativeStandards(listdictReports)
    assert dictResult["listStandards"] == []


def test_fdictBuildQualitativeStandards_json_keys():
    listdictReports = [
        {
            "sFileName": "config.json",
            "sFormat": "json",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": [],
            "listJsonTopKeys": ["model", "version", "parameters"],
        },
    ]
    dictResult = _fdictBuildQualitativeStandards(listdictReports)
    assert len(dictResult["listStandards"]) == 1
    dictEntry = dictResult["listStandards"][0]
    assert "model" in dictEntry["listExpectedJsonKeys"]
    assert "version" in dictEntry["listExpectedJsonKeys"]


def test_qualitative_template_valid_python():
    sCode = fsBuildQualitativeTestCode()
    ast.parse(sCode)


def test_qualitative_template_has_parametrize():
    sCode = fsBuildQualitativeTestCode()
    assert "pytest.mark.parametrize" in sCode
    assert "test_qualitative_check" in sCode


def test_qualitative_template_exec():
    _fdictExecQualitativeTemplate()


# -----------------------------------------------------------------------
# _fdictBuildQuantitativeStandards
# -----------------------------------------------------------------------


def test_fdictBuildQuantitativeStandards_structure():
    listdictReports = [
        {
            "sFileName": "data.npy",
            "listBenchmarks": [
                {
                    "sName": "fDataFirst",
                    "sDataFile": "data.npy",
                    "sAccessPath": "index:0",
                    "fValue": 1.23456789,
                },
                {
                    "sName": "fDataLast",
                    "sDataFile": "data.npy",
                    "sAccessPath": "index:-1",
                    "fValue": 9.87654321,
                },
            ],
        },
    ]
    dictResult = _fdictBuildQuantitativeStandards(
        listdictReports, 1e-6,
    )
    assert "fDefaultRtol" in dictResult
    assert dictResult["fDefaultRtol"] == 1e-6
    assert "listStandards" in dictResult
    assert len(dictResult["listStandards"]) == 2
    dictFirst = dictResult["listStandards"][0]
    assert dictFirst["sName"] == "fDataFirst"
    assert dictFirst["fValue"] == 1.23456789
    assert "sUnit" in dictFirst


def test_fdictBuildQuantitativeStandards_preserves_format_override():
    listdictReports = [
        {
            "sFileName": "params.txt",
            "listBenchmarks": [
                {
                    "sName": "fMass",
                    "sDataFile": "params.txt",
                    "sAccessPath": "key:mass",
                    "sFormat": "keyvalue",
                    "fValue": 5.972e24,
                },
            ],
        },
    ]
    dictResult = _fdictBuildQuantitativeStandards(
        listdictReports, 1e-4,
    )
    assert dictResult["listStandards"][0]["sFormat"] == "keyvalue"


# -----------------------------------------------------------------------
# fdictGenerateAllTestsDeterministic (mocked Docker)
# -----------------------------------------------------------------------


def test_fdictGenerateAllTestsDeterministic_mock():
    from vaibify.gui.testGenerator import fdictGenerateAllTestsDeterministic

    sIntrospectionOutput = json.dumps([
        {
            "sFileName": "output.npy",
            "sFormat": "npy",
            "bExists": True,
            "iByteSize": 800,
            "bLoadable": True,
            "sError": "",
            "tShape": [100],
            "sDtype": "float64",
            "iNanCount": 0,
            "iInfCount": 0,
            "listColumnNames": [],
            "bHasHeader": False,
            "listJsonTopKeys": [],
            "dictJsonScalars": {},
            "listBenchmarks": [
                {
                    "sName": "fOutputFirst",
                    "sDataFile": "output.npy",
                    "sAccessPath": "index:0",
                    "fValue": 0.5,
                },
            ],
        },
    ])

    def fMockExecute(sContainerId, sCommand, sUser=None):
        if "mkdir" in sCommand:
            return (0, "")
        if "python3" in sCommand:
            return (0, sIntrospectionOutput)
        if "rm -f" in sCommand:
            return (0, "")
        return (0, "")

    mockConnection = MagicMock()
    mockConnection.ftResultExecuteCommand.side_effect = fMockExecute
    mockConnection.fnWriteFile = MagicMock()

    dictWorkflow = {
        "listSteps": [{
            "sName": "Analyze",
            "sDirectory": "/work/step01",
            "saDataCommands": ["python run.py"],
            "saDataFiles": ["output.npy"],
        }],
        "fTolerance": 1e-6,
    }
    dictResult = fdictGenerateAllTestsDeterministic(
        mockConnection, "cid123", 0, dictWorkflow, {},
    )
    assert "dictIntegrity" in dictResult
    assert "dictQualitative" in dictResult
    assert "dictQuantitative" in dictResult
    assert dictResult["dictIntegrity"]["sFilePath"].endswith(
        "test_integrity.py"
    )
    assert dictResult["dictQuantitative"]["sFilePath"].endswith(
        "test_quantitative.py"
    )
    assert "sStandardsPath" in dictResult["dictQuantitative"]
    assert "sStandardsPath" in dictResult["dictIntegrity"]
    assert "sStandardsPath" in dictResult["dictQualitative"]


# -----------------------------------------------------------------------
# _fsBuildIntrospectionScript
# -----------------------------------------------------------------------


def test_fsBuildIntrospectionScript_valid_python():
    sScript = _fsBuildIntrospectionScript(
        ["data.csv", "output.npy"], "/workspace/step01",
    )
    ast.parse(sScript)


def test_fsBuildIntrospectionScript_contains_file_list():
    sScript = _fsBuildIntrospectionScript(
        ["alpha.npy", "beta.json"], "/workspace",
    )
    assert "alpha.npy" in sScript
    assert "beta.json" in sScript


# -----------------------------------------------------------------------
# Whitespace loader fixes (verified via template)
# -----------------------------------------------------------------------


def test_whitespace_loader_skips_comments():
    sCode = fsBuildQuantitativeTestCode()
    assert "_flistFilterDataLines" in sCode
    assert "_ftSplitHeaderAndData" in sCode


def test_whitespace_loader_headerless():
    sCode = fsBuildQuantitativeTestCode()
    assert "bAllNumeric" in sCode


# -----------------------------------------------------------------------
# NPZ format support
# -----------------------------------------------------------------------


def test_npz_in_format_map():
    sCode = fsBuildQuantitativeTestCode()
    assert '".npz": "npz"' in sCode
    assert "_fLoadNpzValue" in sCode


# -----------------------------------------------------------------------
# Integrity: all-format loadable tests
# -----------------------------------------------------------------------


def test_fdictBuildIntegrityStandards_json_format():
    listdictReports = [
        {
            "sFileName": "stats.json",
            "sFormat": "json",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": None,
            "listJsonTopKeys": ["alpha", "beta"],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    assert len(dictResult["listStandards"]) == 1
    assert dictResult["listStandards"][0]["sFormat"] == "json"


def test_fdictBuildIntegrityStandards_hdf5_format():
    listdictReports = [
        {
            "sFileName": "data.h5",
            "sFormat": "hdf5",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [1000],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    dictEntry = dictResult["listStandards"][0]
    assert dictEntry["sFormat"] == "hdf5"
    assert dictEntry["tExpectedShape"] == [1000]


def test_fdictBuildIntegrityStandards_npz_format():
    listdictReports = [
        {
            "sFileName": "archive.npz",
            "sFormat": "npz",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [500, 3],
            "listColumnNames": ["samples", "weights"],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    assert dictResult["listStandards"][0]["sFormat"] == "npz"


def test_fdictBuildIntegrityStandards_whitespace_format():
    listdictReports = [
        {
            "sFileName": "ages.txt",
            "sFormat": "whitespace",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [200, 1],
            "listColumnNames": [],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    dictEntry = dictResult["listStandards"][0]
    assert dictEntry["tExpectedShape"] == [200, 1]


def test_fdictBuildIntegrityStandards_keyvalue_format():
    listdictReports = [
        {
            "sFileName": "results.txt",
            "sFormat": "keyvalue",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": None,
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    assert dictResult["listStandards"][0]["sFormat"] == "keyvalue"


# -----------------------------------------------------------------------
# Integrity: multi-format no-NaN tests
# -----------------------------------------------------------------------


def test_fdictBuildIntegrityStandards_csv_nan_check():
    listdictReports = [
        {
            "sFileName": "data.csv",
            "sFormat": "csv",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [10, 3],
            "listColumnNames": ["x", "y", "z"],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    dictEntry = dictResult["listStandards"][0]
    assert dictEntry["bCheckNaN"] is True
    assert dictEntry["bCheckInf"] is True


def test_fdictBuildIntegrityStandards_npz_nan_check():
    listdictReports = [
        {
            "sFileName": "samples.npz",
            "sFormat": "npz",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [100],
            "listColumnNames": ["arr"],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    dictEntry = dictResult["listStandards"][0]
    assert dictEntry["bCheckNaN"] is True


# -----------------------------------------------------------------------
# Nested JSON benchmarking
# -----------------------------------------------------------------------


def test_fdictBuildQuantitativeStandards_nested_json():
    listdictReports = [
        {
            "sFileName": "stats.json",
            "listBenchmarks": [
                {
                    "sName": "fMedian",
                    "sDataFile": "stats.json",
                    "sAccessPath": "key:daMedians,index:0",
                    "fValue": -0.148,
                },
                {
                    "sName": "fMediansMean",
                    "sDataFile": "stats.json",
                    "sAccessPath": "key:daMedians,index:mean",
                    "fValue": 1.234,
                },
            ],
        },
    ]
    dictResult = _fdictBuildQuantitativeStandards(
        listdictReports, 1e-6,
    )
    assert len(dictResult["listStandards"]) == 2
    assert dictResult["listStandards"][1]["sAccessPath"] == (
        "key:daMedians,index:mean"
    )


def test_fsBuildIntrospectionScript_has_json_walker():
    sScript = _fsBuildIntrospectionScript(
        ["stats.json"], "/workspace",
    )
    ast.parse(sScript)
    assert "_fnWalkJsonValues" in sScript
    assert "_fnBenchmarkJsonArray" in sScript


# -----------------------------------------------------------------------
# Security tests
# -----------------------------------------------------------------------


def test_allow_pickle_false_in_template():
    sCode = fsBuildQuantitativeTestCode()
    assert "allow_pickle=False" in sCode


def test_introspection_script_allow_pickle():
    sScript = _fsBuildIntrospectionScript(["x.npy"], "/tmp")
    assert "allow_pickle=False" in sScript


def test_introspection_script_path_traversal():
    sScript = _fsBuildIntrospectionScript(["x.csv"], "/tmp")
    assert "os.path.realpath" in sScript
    assert "path traversal blocked" in sScript


def test_introspection_temp_path_unique():
    from vaibify.gui.testGenerator import _fsRunIntrospection
    import inspect
    sSource = inspect.getsource(_fsRunIntrospection)
    assert "secrets.token_hex" in sSource


# -----------------------------------------------------------------------
# Architectural tests
# -----------------------------------------------------------------------


def test_numeric_token_scientific_notation():
    sCode = fsBuildQuantitativeTestCode()
    assert "_fbIsNumericToken" in sCode
    sScript = _fsBuildIntrospectionScript(["x.dat"], "/tmp")
    assert "_fbIsNumericToken" in sScript


def test_json_array_benchmarks_include_min_max():
    sScript = _fsBuildIntrospectionScript(["x.json"], "/tmp")
    assert "_fnAddJsonArrayBenchmarks" in sScript
    assert "_fnAddStatsBenchmarks" in sScript
    assert "_fnAppendNumericAggregates" in sScript
    sAfterFunc = sScript.split("def _fnAppendNumericAggregates")[1]
    sBody = sAfterFunc.split("\ndef ")[0]
    assert "Min" in sBody
    assert "Max" in sBody


def test_hdf5_dataset_limit_raised():
    sScript = _fsBuildIntrospectionScript(["x.h5"], "/tmp")
    assert "[:50]" in sScript
    assert "[:10]" not in sScript


def test_benchmark_count_cap():
    sScript = _fsBuildIntrospectionScript(["x.csv"], "/tmp")
    assert "_I_MAX_BENCHMARKS_PER_FILE" in sScript
    assert "250" in sScript


def test_encoding_utf8_in_template():
    sCode = fsBuildQuantitativeTestCode()
    assert 'encoding="utf-8"' in sCode


def test_unknown_extension_not_csv():
    sCode = fsBuildQuantitativeTestCode()
    assert "_fsInferFormat" in sCode
    sAfterFunc = sCode.split("def _fsInferFormat")[1]
    sBody = sAfterFunc.split("\ndef ")[0]
    assert '"csv"' not in sBody.split("_DICT_FORMAT_MAP")[0] or (
        sBody.count('"csv"') == sBody.count('.get(')
    )
    assert "None" in sBody


# -----------------------------------------------------------------------
# New format map tests
# -----------------------------------------------------------------------


def test_jsonl_in_format_map():
    sCode = fsBuildQuantitativeTestCode()
    assert '".jsonl": "jsonl"' in sCode


def test_excel_in_format_map():
    sCode = fsBuildQuantitativeTestCode()
    assert '".xlsx": "excel"' in sCode


def test_fits_in_format_map():
    sCode = fsBuildQuantitativeTestCode()
    assert '".fits": "fits"' in sCode


def test_matlab_in_format_map():
    sCode = fsBuildQuantitativeTestCode()
    assert '".mat": "matlab"' in sCode


def test_parquet_in_format_map():
    sCode = fsBuildQuantitativeTestCode()
    assert '".parquet": "parquet"' in sCode


def test_image_in_format_map():
    sCode = fsBuildQuantitativeTestCode()
    assert '".png": "image"' in sCode


def test_introspection_script_has_all_formats():
    sScript = _fsBuildIntrospectionScript(
        ["x.csv", "y.jsonl", "z.fits"], "/tmp",
    )
    ast.parse(sScript)
    for sHandler in (
        "_fnBenchmarkJsonl",
        "_fnBenchmarkExcel",
        "_fnBenchmarkFits",
        "_fnBenchmarkMatlab",
        "_fnBenchmarkParquet",
        "_fnBenchmarkImage",
    ):
        assert sHandler in sScript


def test_integrity_standards_all_formats():
    listFormats = [
        ("data.jsonl", "jsonl"),
        ("data.xlsx", "excel"),
        ("data.fits", "fits"),
        ("data.mat", "matlab"),
        ("data.parquet", "parquet"),
        ("data.png", "image"),
    ]
    for sFileName, sFormat in listFormats:
        listdictReports = [
            {
                "sFileName": sFileName,
                "sFormat": sFormat,
                "bExists": True,
                "bLoadable": True,
                "iNanCount": 0,
                "iInfCount": 0,
                "tShape": [10],
            },
        ]
        dictResult = _fdictBuildIntegrityStandards(listdictReports)
        assert len(dictResult["listStandards"]) == 1
        assert dictResult["listStandards"][0]["sFormat"] == sFormat


# -----------------------------------------------------------------------
# New 23-format tests
# -----------------------------------------------------------------------


def test_all_new_formats_in_format_map():
    sCode = fsBuildQuantitativeTestCode()
    listExpected = [
        (".fasta", "fasta"), (".fa", "fasta"),
        (".fastq", "fastq"), (".fq", "fastq"),
        (".vcf", "vcf"), (".bed", "bed"),
        (".gff", "gff"), (".gtf", "gff"), (".gff3", "gff"),
        (".sam", "sam"), (".log", "syslog"), (".cef", "cef"),
        (".bam", "bam"), (".unf", "fortran"),
        (".sav", "spss"), (".dta", "stata"), (".sas7bdat", "sas"),
        (".rds", "rdata"), (".RData", "rdata"), (".rda", "rdata"),
        (".vot", "votable"), (".ipac", "ipac"),
        (".pcap", "pcap"), (".pcapng", "pcap"),
        (".vtk", "vtk"), (".vtu", "vtk"),
        (".cgns", "cgns"), (".safetensors", "safetensors"),
        (".tfrecord", "tfrecord"),
    ]
    for sExt, sFormat in listExpected:
        assert f'"{sExt}": "{sFormat}"' in sCode, (
            f"Missing format map entry: {sExt} -> {sFormat}"
        )


def test_introspection_has_all_new_benchmarkers():
    sScript = _fsBuildIntrospectionScript(
        ["x.csv", "y.fasta", "z.vcf"], "/tmp",
    )
    ast.parse(sScript)
    listHandlers = [
        "_fnBenchmarkFasta", "_fnBenchmarkFastq",
        "_fnBenchmarkVcf", "_fnBenchmarkBed",
        "_fnBenchmarkGff", "_fnBenchmarkSam",
        "_fnBenchmarkSyslog", "_fnBenchmarkCef",
        "_fnBenchmarkFixedwidth", "_fnBenchmarkMultitable",
        "_fnBenchmarkBam", "_fnBenchmarkFortran",
        "_fnBenchmarkSpss", "_fnBenchmarkStata",
        "_fnBenchmarkSas", "_fnBenchmarkRdata",
        "_fnBenchmarkVotable", "_fnBenchmarkIpac",
        "_fnBenchmarkPcap", "_fnBenchmarkVtk",
        "_fnBenchmarkCgns", "_fnBenchmarkSafetensors",
        "_fnBenchmarkTfrecord",
    ]
    for sHandler in listHandlers:
        assert sHandler in sScript, (
            f"Missing introspection benchmarker: {sHandler}"
        )


def test_integrity_standards_new_formats():
    listFormats = [
        ("seqs.fasta", "fasta"),
        ("reads.fastq", "fastq"),
        ("variants.vcf", "vcf"),
        ("regions.bed", "bed"),
        ("annots.gff", "gff"),
        ("aligns.sam", "sam"),
        ("events.log", "syslog"),
        ("alerts.cef", "cef"),
        ("data.unf", "fortran"),
        ("data.sav", "spss"),
        ("data.dta", "stata"),
        ("data.sas7bdat", "sas"),
        ("data.rds", "rdata"),
        ("data.vot", "votable"),
        ("data.ipac", "ipac"),
        ("data.pcap", "pcap"),
        ("data.vtk", "vtk"),
        ("data.cgns", "cgns"),
        ("model.safetensors", "safetensors"),
        ("data.tfrecord", "tfrecord"),
        ("reads.bam", "bam"),
        ("table.fixedwidth", "fixedwidth"),
        ("multi.multitable", "multitable"),
    ]
    for sFileName, sFormat in listFormats:
        listdictReports = [
            {
                "sFileName": sFileName,
                "sFormat": sFormat,
                "bExists": True,
                "bLoadable": True,
                "iNanCount": 0,
                "iInfCount": 0,
                "tShape": [10],
            },
        ]
        dictResult = _fdictBuildIntegrityStandards(listdictReports)
        assert len(dictResult["listStandards"]) == 1
        assert dictResult["listStandards"][0]["sFormat"] == sFormat


# -----------------------------------------------------------------------
# _fsFormatSafeName — edge cases
# -----------------------------------------------------------------------


def test_fsFormatSafeName_empty_string():
    sResult = _fsFormatSafeName("")
    assert sResult == "" or sResult.isidentifier() or sResult == ""


def test_fsFormatSafeName_all_special_chars():
    sResult = _fsFormatSafeName("@#$%.dat")
    assert all(c.isalnum() or c == "_" for c in sResult)


def test_fsFormatSafeName_unicode():
    sResult = _fsFormatSafeName("datos_\u00e9nergia.csv")
    assert all(c.isalnum() or c == "_" for c in sResult)


def test_fsFormatSafeName_double_extension():
    sResult = _fsFormatSafeName("data.tar.gz")
    assert "." not in sResult


# -----------------------------------------------------------------------
# _fbIsNumericToken (template-embedded, tested via exec)
# -----------------------------------------------------------------------


def test_fbIsNumericToken_integer():
    dictNs = _fdictExecTemplate()
    assert dictNs["_fbIsNumericToken"]("42") is True


def test_fbIsNumericToken_float():
    dictNs = _fdictExecTemplate()
    assert dictNs["_fbIsNumericToken"]("3.14") is True


def test_fbIsNumericToken_scientific():
    dictNs = _fdictExecTemplate()
    assert dictNs["_fbIsNumericToken"]("1.23e-10") is True


def test_fbIsNumericToken_negative_scientific():
    dictNs = _fdictExecTemplate()
    assert dictNs["_fbIsNumericToken"]("-6.674e-11") is True


def test_fbIsNumericToken_nan():
    dictNs = _fdictExecTemplate()
    assert dictNs["_fbIsNumericToken"]("nan") is True


def test_fbIsNumericToken_inf():
    dictNs = _fdictExecTemplate()
    assert dictNs["_fbIsNumericToken"]("inf") is True


def test_fbIsNumericToken_negative_inf():
    dictNs = _fdictExecTemplate()
    assert dictNs["_fbIsNumericToken"]("-inf") is True


def test_fbIsNumericToken_empty_string():
    dictNs = _fdictExecTemplate()
    assert dictNs["_fbIsNumericToken"]("") is False


def test_fbIsNumericToken_text():
    dictNs = _fdictExecTemplate()
    assert dictNs["_fbIsNumericToken"]("temperature") is False


def test_fbIsNumericToken_mixed():
    dictNs = _fdictExecTemplate()
    assert dictNs["_fbIsNumericToken"]("12abc") is False


# -----------------------------------------------------------------------
# _flistFilterDataLines (template-embedded, tested via exec)
# -----------------------------------------------------------------------


def test_flistFilterDataLines_removes_comments():
    dictNs = _fdictExecTemplate()
    listInput = ["# comment\n", "1.0 2.0\n", "# another\n", "3.0 4.0\n"]
    listResult = dictNs["_flistFilterDataLines"](listInput)
    assert len(listResult) == 2
    assert "1.0 2.0" in listResult[0]


def test_flistFilterDataLines_removes_blanks():
    dictNs = _fdictExecTemplate()
    listInput = ["1.0 2.0\n", "\n", "  \n", "3.0 4.0\n"]
    listResult = dictNs["_flistFilterDataLines"](listInput)
    assert len(listResult) == 2


def test_flistFilterDataLines_empty_input():
    dictNs = _fdictExecTemplate()
    assert dictNs["_flistFilterDataLines"]([]) == []


def test_flistFilterDataLines_all_comments():
    dictNs = _fdictExecTemplate()
    listInput = ["# a\n", "# b\n"]
    assert dictNs["_flistFilterDataLines"](listInput) == []


def test_flistFilterDataLines_mixed():
    dictNs = _fdictExecTemplate()
    listInput = [
        "# header\n", "\n", "time flux\n",
        "# mid-comment\n", "0.0 1.5\n",
    ]
    listResult = dictNs["_flistFilterDataLines"](listInput)
    assert len(listResult) == 2
    assert "time" in listResult[0]


# -----------------------------------------------------------------------
# _ftSplitHeaderAndData (template-embedded, tested via exec)
# -----------------------------------------------------------------------


def test_ftSplitHeaderAndData_with_header():
    dictNs = _fdictExecTemplate()
    listInput = ["time flux", "0.0 1.5", "1.0 2.5"]
    sHeader, listRows = dictNs["_ftSplitHeaderAndData"](listInput)
    assert sHeader == "time flux"
    assert len(listRows) == 2


def test_ftSplitHeaderAndData_no_header():
    dictNs = _fdictExecTemplate()
    listInput = ["0.0 1.5", "1.0 2.5"]
    sHeader, listRows = dictNs["_ftSplitHeaderAndData"](listInput)
    assert sHeader == ""
    assert len(listRows) == 2


def test_ftSplitHeaderAndData_empty():
    dictNs = _fdictExecTemplate()
    sHeader, listRows = dictNs["_ftSplitHeaderAndData"]([])
    assert sHeader == ""
    assert listRows == []


def test_ftSplitHeaderAndData_single_numeric_line():
    dictNs = _fdictExecTemplate()
    sHeader, listRows = dictNs["_ftSplitHeaderAndData"](["3.14 2.72"])
    assert sHeader == ""
    assert len(listRows) == 1


def test_ftSplitHeaderAndData_mixed_first_line():
    dictNs = _fdictExecTemplate()
    listInput = ["col1 3.14", "1.0 2.0"]
    sHeader, listRows = dictNs["_ftSplitHeaderAndData"](listInput)
    assert sHeader == "col1 3.14"
    assert len(listRows) == 1


# -----------------------------------------------------------------------
# _flistParseIntrospectionOutput
# -----------------------------------------------------------------------


def test_flistParseIntrospectionOutput_clean_json():
    sOutput = json.dumps([{"sFileName": "data.npy", "bExists": True}])
    listResult = _flistParseIntrospectionOutput(sOutput)
    assert len(listResult) == 1
    assert listResult[0]["sFileName"] == "data.npy"


def test_flistParseIntrospectionOutput_warnings_mixed():
    sOutput = (
        "UserWarning: something\n"
        "DeprecationWarning: old api\n"
        + json.dumps([{"sFileName": "x.csv", "bExists": True}])
    )
    listResult = _flistParseIntrospectionOutput(sOutput)
    assert len(listResult) == 1
    assert listResult[0]["sFileName"] == "x.csv"


def test_flistParseIntrospectionOutput_no_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        _flistParseIntrospectionOutput("no json here at all")


def test_flistParseIntrospectionOutput_empty():
    with pytest.raises(ValueError, match="not valid JSON"):
        _flistParseIntrospectionOutput("")


# -----------------------------------------------------------------------
# _fbOutputLooksValid
# -----------------------------------------------------------------------


def test_fbOutputLooksValid_with_fences():
    assert _fbOutputLooksValid("```python\nimport os\n```") is True


def test_fbOutputLooksValid_with_test_function():
    assert _fbOutputLooksValid("def test_example(): pass") is True


def test_fbOutputLooksValid_with_quantitative():
    assert _fbOutputLooksValid('{"listStandards": []}') is True


def test_fbOutputLooksValid_random_text():
    assert _fbOutputLooksValid("some random error text") is False


def test_fbOutputLooksValid_empty():
    assert _fbOutputLooksValid("") is False


# -----------------------------------------------------------------------
# _fsRemoveOldTestSection
# -----------------------------------------------------------------------


def test_fsRemoveOldTestSection_no_marker():
    sContent = "# My Project\n\nSome content."
    sResult = _fsRemoveOldTestSection(sContent)
    assert sResult == sContent


def test_fsRemoveOldTestSection_with_marker():
    sContent = (
        "# My Project\n\nSome content.\n\n"
        "# Vaibify Test Generation Instructions\n\n"
        "Old instructions here."
    )
    sResult = _fsRemoveOldTestSection(sContent)
    assert "Vaibify Test Generation" not in sResult
    assert "My Project" in sResult


# -----------------------------------------------------------------------
# Code generators — edge cases
# -----------------------------------------------------------------------


def test_fdictBuildIntegrityStandards_unloadable_file():
    listdictReports = [
        {
            "sFileName": "broken.npy",
            "sFormat": "npy",
            "bExists": True,
            "bLoadable": False,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": None,
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    assert len(dictResult["listStandards"]) == 1
    assert dictResult["listStandards"][0]["bCheckNaN"] is False


def test_fdictBuildIntegrityStandards_multiple_files():
    listdictReports = [
        {
            "sFileName": "alpha.csv",
            "sFormat": "csv",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [10, 3],
            "listColumnNames": ["x", "y"],
        },
        {
            "sFileName": "beta.npy",
            "sFormat": "npy",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [50],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    assert len(dictResult["listStandards"]) == 2
    listFileNames = [s["sFileName"] for s in dictResult["listStandards"]]
    assert "alpha.csv" in listFileNames
    assert "beta.npy" in listFileNames


def test_fdictBuildQualitativeStandards_multiple_files():
    listdictReports = [
        {
            "sFileName": "results.csv",
            "sFormat": "csv",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": ["time", "flux"],
            "listJsonTopKeys": [],
        },
        {
            "sFileName": "config.json",
            "sFormat": "json",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": [],
            "listJsonTopKeys": ["model", "version"],
        },
    ]
    dictResult = _fdictBuildQualitativeStandards(listdictReports)
    assert len(dictResult["listStandards"]) == 2
    assert "time" in dictResult["listStandards"][0]["listExpectedColumns"]
    assert "model" in dictResult["listStandards"][1]["listExpectedJsonKeys"]


def test_fdictBuildQuantitativeStandards_empty_benchmarks():
    listdictReports = [
        {
            "sFileName": "data.txt",
            "listBenchmarks": [],
        },
    ]
    dictResult = _fdictBuildQuantitativeStandards(
        listdictReports, 1e-6,
    )
    assert dictResult["listStandards"] == []
    assert dictResult["fDefaultRtol"] == 1e-6


def test_fdictBuildQuantitativeStandards_empty_reports():
    dictResult = _fdictBuildQuantitativeStandards([], 1e-4)
    assert dictResult["listStandards"] == []


def test_fdictBuildQuantitativeStandards_multiple_files():
    listdictReports = [
        {
            "sFileName": "a.npy",
            "listBenchmarks": [
                {
                    "sName": "fAlphaFirst",
                    "sDataFile": "a.npy",
                    "sAccessPath": "index:0",
                    "fValue": 1.0,
                },
            ],
        },
        {
            "sFileName": "b.csv",
            "listBenchmarks": [
                {
                    "sName": "fBetaLast",
                    "sDataFile": "b.csv",
                    "sAccessPath": "column:x,index:-1",
                    "fValue": 2.0,
                },
            ],
        },
    ]
    dictResult = _fdictBuildQuantitativeStandards(
        listdictReports, 1e-8,
    )
    assert len(dictResult["listStandards"]) == 2
    listNames = [d["sName"] for d in dictResult["listStandards"]]
    assert "fAlphaFirst" in listNames
    assert "fBetaLast" in listNames


# -----------------------------------------------------------------------
# Template format loader tests — real temp files
# -----------------------------------------------------------------------


def test_loader_npy(tmp_path):
    """Create a real .npy file and load it via template code."""
    daData = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    np.save(str(tmp_path / "data.npy"), daData)
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.npy", "index:0", str(tmp_path),
    )
    assert fResult == 1.0


def test_loader_npy_aggregate_mean(tmp_path):
    daData = np.array([2.0, 4.0, 6.0])
    np.save(str(tmp_path / "data.npy"), daData)
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.npy", "index:mean", str(tmp_path),
    )
    assert abs(fResult - 4.0) < 1e-10


def test_loader_npy_last_index(tmp_path):
    daData = np.array([10.0, 20.0, 30.0])
    np.save(str(tmp_path / "data.npy"), daData)
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.npy", "index:-1", str(tmp_path),
    )
    assert fResult == 30.0


def test_loader_npz(tmp_path):
    np.savez(
        str(tmp_path / "archive.npz"),
        temperatures=np.array([100.0, 200.0, 300.0]),
    )
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "archive.npz", "key:temperatures,index:2", str(tmp_path),
    )
    assert fResult == 300.0


def test_loader_csv(tmp_path):
    sPath = str(tmp_path / "results.csv")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("time,temperature\n0.0,288.15\n1.0,290.0\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "results.csv", "column:temperature,index:-1", str(tmp_path),
    )
    assert fResult == 290.0


def test_loader_csv_aggregate(tmp_path):
    sPath = str(tmp_path / "data.csv")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("x\n1.0\n2.0\n3.0\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.csv", "column:x,index:min", str(tmp_path),
    )
    assert fResult == 1.0


def test_loader_json(tmp_path):
    sPath = str(tmp_path / "config.json")
    with open(sPath, "w", encoding="utf-8") as fh:
        json.dump({"fMass": 5.972e24, "sName": "Earth"}, fh)
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "config.json", "key:fMass", str(tmp_path),
    )
    assert abs(fResult - 5.972e24) < 1e18


def test_loader_json_nested(tmp_path):
    sPath = str(tmp_path / "nested.json")
    with open(sPath, "w", encoding="utf-8") as fh:
        json.dump({"results": {"temperature": 288.15}}, fh)
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "nested.json", "key:results.temperature", str(tmp_path),
    )
    assert abs(fResult - 288.15) < 1e-10


def test_loader_json_array(tmp_path):
    sPath = str(tmp_path / "arrays.json")
    with open(sPath, "w", encoding="utf-8") as fh:
        json.dump({"daValues": [1.0, 2.0, 3.0]}, fh)
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "arrays.json", "key:daValues,index:1", str(tmp_path),
    )
    assert fResult == 2.0


def test_loader_json_array_aggregate(tmp_path):
    sPath = str(tmp_path / "agg.json")
    with open(sPath, "w", encoding="utf-8") as fh:
        json.dump({"daValues": [10.0, 20.0, 30.0]}, fh)
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "agg.json", "key:daValues,index:max", str(tmp_path),
    )
    assert fResult == 30.0


def test_loader_whitespace_with_header(tmp_path):
    sPath = str(tmp_path / "output.dat")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("# comment line\ntime flux\n0.0 1.5\n1.0 2.5\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "output.dat", "column:flux,index:-1", str(tmp_path),
    )
    assert fResult == 2.5


def test_loader_whitespace_headerless(tmp_path):
    sPath = str(tmp_path / "data.txt")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("1.0 2.0\n3.0 4.0\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.txt", "index:0,0", str(tmp_path),
    )
    assert fResult == 1.0


def test_loader_keyvalue(tmp_path):
    sPath = str(tmp_path / "params.txt")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("# parameters\nfMass = 5.972e24\nsName = Earth\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "params.txt", "key:fMass", str(tmp_path), sFormat="keyvalue",
    )
    assert abs(fResult - 5.972e24) < 1e18


def test_loader_jsonl(tmp_path):
    sPath = str(tmp_path / "records.jsonl")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write('{"temp": 288.0}\n{"temp": 290.0}\n{"temp": 292.0}\n')
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "records.jsonl", "key:temp,index:2", str(tmp_path),
    )
    assert fResult == 292.0


def test_loader_fasta(tmp_path):
    sPath = str(tmp_path / "seqs.fasta")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write(">seq1\nACGTACGT\n>seq2\nACGT\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "seqs.fasta", "index:0", str(tmp_path),
    )
    assert fResult == 8.0


def test_loader_fastq(tmp_path):
    sPath = str(tmp_path / "reads.fastq")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("@read1\nACGT\n+\nIIII\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "reads.fastq", "key:length,index:0", str(tmp_path),
    )
    assert fResult == 4.0


def test_loader_syslog(tmp_path):
    sPath = str(tmp_path / "events.log")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("2024-01-01 INFO started\n")
        fh.write("2024-01-01 WARN high temp\n")
        fh.write("2024-01-01 INFO stopped\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "events.log", "index:0", str(tmp_path),
    )
    assert fResult == 3.0


def test_loader_cef(tmp_path):
    sPath = str(tmp_path / "alerts.cef")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("CEF:0|Vendor|Product|1.0|100|Alert|5|\n")
        fh.write("CEF:0|Vendor|Product|1.0|101|Alert|3|\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "alerts.cef", "index:0", str(tmp_path),
    )
    assert fResult == 2.0


def test_loader_fixedwidth(tmp_path):
    sPath = str(tmp_path / "table.dat")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("1.0 2.0 3.0\n4.0 5.0 6.0\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "table.dat", "index:-1", str(tmp_path),
        sFormat="fixedwidth",
    )
    assert fResult == 4.0


# -----------------------------------------------------------------------
# Security tests — real file validation
# -----------------------------------------------------------------------


def test_npy_allow_pickle_rejected(tmp_path):
    """Verify pickled .npy files are rejected by allow_pickle=False."""
    np.save(
        str(tmp_path / "evil.npy"),
        np.array([1, 2, 3]), allow_pickle=False,
    )
    daLoaded = np.load(str(tmp_path / "evil.npy"), allow_pickle=False)
    assert len(daLoaded) == 3
    np.save(
        str(tmp_path / "pickled.npy"),
        np.array([object()], dtype=object), allow_pickle=True,
    )
    dictNs = _fdictExecTemplate()
    with pytest.raises(ValueError):
        dictNs["_fLoadValue"](
            "pickled.npy", "index:0", str(tmp_path),
        )


def test_introspection_file_size_limit():
    sScript = _fsBuildIntrospectionScript(["x.npy"], "/tmp")
    assert "_I_MAX_FILE_BYTES" in sScript
    assert "500_000_000" in sScript or "500000000" in sScript


def test_introspection_json_depth_limit():
    sScript = _fsBuildIntrospectionScript(["x.json"], "/tmp")
    assert "iDepth > 10" in sScript


def test_template_path_directory_used():
    sCode = fsBuildQuantitativeTestCode()
    assert "_STEP_DIRECTORY" in sCode
    assert "pathlib.Path(__file__)" in sCode


# -----------------------------------------------------------------------
# _fdictParseAccessPath (via template exec)
# -----------------------------------------------------------------------


def test_parseAccessPath_key():
    dictNs = _fdictExecTemplate()
    dictResult = dictNs["_fdictParseAccessPath"]("key:temperature")
    assert dictResult["key"] == "temperature"


def test_parseAccessPath_column_and_index():
    dictNs = _fdictExecTemplate()
    dictResult = dictNs["_fdictParseAccessPath"](
        "column:flux,index:-1",
    )
    assert dictResult["column"] == "flux"
    assert dictResult["listIndices"] == [-1]


def test_parseAccessPath_dataset():
    dictNs = _fdictExecTemplate()
    dictResult = dictNs["_fdictParseAccessPath"](
        "dataset:/group/data,index:0",
    )
    assert dictResult["dataset"] == "/group/data"
    assert dictResult["listIndices"] == [0]


def test_parseAccessPath_aggregate():
    dictNs = _fdictExecTemplate()
    dictResult = dictNs["_fdictParseAccessPath"]("index:mean")
    assert dictResult["sAggregate"] == "mean"


def test_parseAccessPath_hdu():
    dictNs = _fdictExecTemplate()
    dictResult = dictNs["_fdictParseAccessPath"](
        "hdu:1,column:flux,index:0",
    )
    assert dictResult["iHdu"] == 1
    assert dictResult["column"] == "flux"


def test_parseAccessPath_section():
    dictNs = _fdictExecTemplate()
    dictResult = dictNs["_fdictParseAccessPath"]("section:2,index:0")
    assert dictResult["iSection"] == 2


# -----------------------------------------------------------------------
# Error handling — loader errors with temp files
# -----------------------------------------------------------------------


def test_loader_npy_corrupt_file(tmp_path):
    sPath = str(tmp_path / "bad.npy")
    with open(sPath, "wb") as fh:
        fh.write(b"this is not a numpy file")
    dictNs = _fdictExecTemplate()
    with pytest.raises(ValueError, match="Failed to load"):
        dictNs["_fLoadValue"](
            "bad.npy", "index:0", str(tmp_path),
        )


def test_loader_csv_missing_column(tmp_path):
    sPath = str(tmp_path / "data.csv")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("x,y\n1.0,2.0\n")
    dictNs = _fdictExecTemplate()
    with pytest.raises(ValueError, match="Failed to access"):
        dictNs["_fLoadValue"](
            "data.csv", "column:nonexistent,index:0", str(tmp_path),
        )


def test_loader_json_corrupt(tmp_path):
    sPath = str(tmp_path / "bad.json")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    dictNs = _fdictExecTemplate()
    with pytest.raises(ValueError, match="Failed to load"):
        dictNs["_fLoadValue"](
            "bad.json", "key:x", str(tmp_path),
        )


def test_loader_unsupported_format(tmp_path):
    sPath = str(tmp_path / "data.xyz")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("1.0 2.0\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.xyz", "index:0,0", str(tmp_path),
    )
    assert isinstance(fResult, float)


def test_loader_keyvalue_missing_key(tmp_path):
    sPath = str(tmp_path / "params.txt")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("fMass = 5.972e24\n")
    dictNs = _fdictExecTemplate()
    with pytest.raises(KeyError):
        dictNs["_fLoadValue"](
            "params.txt", "key:fRadius", str(tmp_path),
            sFormat="keyvalue",
        )


# -----------------------------------------------------------------------
# Introspection script — format-specific handler coverage
# -----------------------------------------------------------------------


def test_introspection_script_keyvalue_detection():
    sScript = _fsBuildIntrospectionScript(["params.dat"], "/tmp")
    ast.parse(sScript)
    assert "_fbLooksLikeKeyvalue" in sScript


def test_introspection_script_binary_detection():
    sScript = _fsBuildIntrospectionScript(["mystery.bin"], "/tmp")
    ast.parse(sScript)
    assert "_fsDetectFormat" in sScript


# -----------------------------------------------------------------------
# Qualitative code — format-specific column loading
# -----------------------------------------------------------------------


def test_fdictBuildQualitativeStandards_csv_format():
    listdictReports = [
        {
            "sFileName": "data.csv",
            "sFormat": "csv",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": ["time", "flux"],
            "listJsonTopKeys": [],
        },
    ]
    dictResult = _fdictBuildQualitativeStandards(listdictReports)
    dictEntry = dictResult["listStandards"][0]
    assert "time" in dictEntry["listExpectedColumns"]
    assert dictEntry["sFormat"] == "csv"


def test_fdictBuildQualitativeStandards_whitespace_format():
    listdictReports = [
        {
            "sFileName": "data.dat",
            "sFormat": "whitespace",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": ["age", "mass"],
            "listJsonTopKeys": [],
        },
    ]
    dictResult = _fdictBuildQualitativeStandards(listdictReports)
    dictEntry = dictResult["listStandards"][0]
    assert "age" in dictEntry["listExpectedColumns"]
    assert "mass" in dictEntry["listExpectedColumns"]


# -----------------------------------------------------------------------
# Integrity standards — NaN/Inf check flags
# -----------------------------------------------------------------------


def test_fdictBuildIntegrityStandards_npy_nan_flags():
    listdictReports = [
        {
            "sFileName": "array.npy",
            "sFormat": "npy",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [100],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    dictEntry = dictResult["listStandards"][0]
    assert dictEntry["bCheckNaN"] is True
    assert dictEntry["bCheckInf"] is True


def test_fdictBuildIntegrityStandards_whitespace_nan_flags():
    listdictReports = [
        {
            "sFileName": "data.dat",
            "sFormat": "whitespace",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [50, 3],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    assert dictResult["listStandards"][0]["bCheckNaN"] is True


def test_fdictBuildIntegrityStandards_nan_present_no_check():
    listdictReports = [
        {
            "sFileName": "data.npy",
            "sFormat": "npy",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 5,
            "iInfCount": 0,
            "tShape": [100],
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    assert dictResult["listStandards"][0]["bCheckNaN"] is False


# -----------------------------------------------------------------------
# A. Text-based format loaders with real temp files
# -----------------------------------------------------------------------


def test_loader_vcf(tmp_path):
    """Load a value from a real VCF temp file."""
    sPath = str(tmp_path / "variants.vcf")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("##fileformat=VCFv4.2\n")
        fh.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
        fh.write("chr1\t100\t.\tA\tT\t30.0\tPASS\t.\n")
        fh.write("chr1\t200\t.\tG\tC\t45.0\tPASS\t.\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "variants.vcf", "column:POS,index:0", str(tmp_path),
    )
    assert fResult == 100.0


def test_loader_bed(tmp_path):
    """Load a value from a real BED temp file."""
    sPath = str(tmp_path / "regions.bed")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("chr1\t100\t200\tgene1\t500\t+\n")
        fh.write("chr2\t300\t400\tgene2\t600\t-\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "regions.bed", "column:chromStart,index:0", str(tmp_path),
    )
    assert fResult == 100.0


def test_loader_gff(tmp_path):
    """Load a value from a real GFF temp file."""
    sPath = str(tmp_path / "annots.gff")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("chr1\tvaibify\tgene\t100\t500\t0.5\t+\t.\tID=g1\n")
        fh.write("chr1\tvaibify\texon\t200\t400\t0.8\t+\t.\tID=e1\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "annots.gff", "column:start,index:0", str(tmp_path),
    )
    assert fResult == 100.0


def test_loader_sam(tmp_path):
    """Load a value from a real SAM temp file."""
    sPath = str(tmp_path / "aligns.sam")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("@HD\tVN:1.6\n")
        fh.write("read1\t0\tchr1\t100\t60\t4M\t*\t0\t0\tACGT\tIIII\n")
        fh.write("read2\t0\tchr1\t200\t42\t4M\t*\t0\t0\tTGCA\tIIII\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "aligns.sam", "column:POS,index:0", str(tmp_path),
    )
    assert fResult == 100.0


def test_loader_multitable(tmp_path):
    """Load a value from a real multi-table temp file."""
    sPath = str(tmp_path / "multi.dat")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("1.0 2.0\n3.0 4.0\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "multi.dat", "section:0,index:0,0", str(tmp_path),
        sFormat="multitable",
    )
    assert fResult == 1.0


def test_loader_hdf5(tmp_path):
    """Load a value from a real HDF5 temp file."""
    h5py = pytest.importorskip("h5py")
    sPath = str(tmp_path / "data.h5")
    with h5py.File(sPath, "w") as fh:
        fh.create_dataset("temperatures", data=[288.15, 290.0, 300.0])
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.h5", "dataset:temperatures,index:0", str(tmp_path),
    )
    assert abs(fResult - 288.15) < 1e-10


def test_loader_excel(tmp_path):
    """Load a value from a real Excel temp file."""
    openpyxl = pytest.importorskip("openpyxl")
    sPath = str(tmp_path / "data.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["time", "flux"])
    ws.append([0.0, 1.5])
    ws.append([1.0, 2.5])
    wb.save(sPath)
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.xlsx", "column:flux,index:-1", str(tmp_path),
    )
    assert fResult == 2.5


def test_loader_fits(tmp_path):
    """Load a value from a real FITS temp file."""
    astropy = pytest.importorskip("astropy")
    from astropy.io import fits as fitsLib
    sPath = str(tmp_path / "data.fits")
    hdu = fitsLib.PrimaryHDU(data=np.array([1.0, 2.0, 3.0]))
    hdu.writeto(sPath)
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.fits", "hdu:0,index:0", str(tmp_path),
    )
    assert fResult == 1.0


def test_loader_matlab(tmp_path):
    """Load a value from a real MATLAB temp file."""
    scipy = pytest.importorskip("scipy")
    from scipy.io import savemat
    sPath = str(tmp_path / "data.mat")
    savemat(sPath, {"daTemps": np.array([100.0, 200.0, 300.0])})
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.mat", "key:daTemps,index:0", str(tmp_path),
    )
    assert fResult == 100.0


def test_loader_parquet(tmp_path):
    """Load a value from a real Parquet temp file."""
    pq = pytest.importorskip("pyarrow.parquet")
    import pyarrow as pa
    sPath = str(tmp_path / "data.parquet")
    table = pa.table({"flux": [1.5, 2.5, 3.5]})
    pq.write_table(table, sPath)
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.parquet", "column:flux,index:-1", str(tmp_path),
    )
    assert fResult == 3.5


def test_loader_image(tmp_path):
    """Load a value from a real image temp file."""
    PIL = pytest.importorskip("PIL")
    from PIL import Image
    sPath = str(tmp_path / "test.png")
    img = Image.new("L", (4, 4), color=128)
    img.save(sPath)
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "test.png", "index:0", str(tmp_path),
    )
    assert fResult == 128.0


# -----------------------------------------------------------------------
# B. Generated code exec validation — end-to-end
# -----------------------------------------------------------------------


def test_integrity_template_executes_with_npy(tmp_path):
    """Write integrity standards and exec the template with npy data."""
    import pathlib
    daData = np.array([1.0, 2.0, 3.0])
    np.save(str(tmp_path / "output.npy"), daData)
    sTestsDir = tmp_path / "tests"
    sTestsDir.mkdir()
    dictStandards = _fdictBuildIntegrityStandards([
        {
            "sFileName": "output.npy",
            "sFormat": "npy",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [3],
        },
    ])
    sStandardsPath = str(sTestsDir / "integrity_standards.json")
    with open(sStandardsPath, "w", encoding="utf-8") as fh:
        json.dump(dictStandards, fh)
    sFakeFile = str(sTestsDir / "test_integrity.py")
    sCode = fsBuildIntegrityTestCode()
    dictNs = {"__file__": sFakeFile}
    exec(compile(sCode, "<integrity>", "exec"), dictNs)
    dictEntry = dictStandards["listStandards"][0]
    dictNs["test_integrity_check"](dictEntry)


def test_integrity_template_executes_with_csv(tmp_path):
    """Write integrity standards and exec the template with csv data."""
    sPath = str(tmp_path / "data.csv")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("x,y\n1.0,2.0\n3.0,4.0\n")
    sTestsDir = tmp_path / "tests"
    sTestsDir.mkdir()
    dictStandards = _fdictBuildIntegrityStandards([
        {
            "sFileName": "data.csv",
            "sFormat": "csv",
            "bExists": True,
            "bLoadable": True,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": [2, 2],
            "listColumnNames": ["x", "y"],
        },
    ])
    sStandardsPath = str(sTestsDir / "integrity_standards.json")
    with open(sStandardsPath, "w", encoding="utf-8") as fh:
        json.dump(dictStandards, fh)
    sFakeFile = str(sTestsDir / "test_integrity.py")
    sCode = fsBuildIntegrityTestCode()
    dictNs = {"__file__": sFakeFile}
    exec(compile(sCode, "<integrity>", "exec"), dictNs)
    dictEntry = dictStandards["listStandards"][0]
    dictNs["test_integrity_check"](dictEntry)


def test_qualitative_template_executes_with_csv(tmp_path):
    """Write qualitative standards and exec the template with csv data."""
    sPath = str(tmp_path / "data.csv")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("time,flux\n0.0,1.5\n")
    sTestsDir = tmp_path / "tests"
    sTestsDir.mkdir()
    dictStandards = _fdictBuildQualitativeStandards([
        {
            "sFileName": "data.csv",
            "sFormat": "csv",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": ["time", "flux"],
            "listJsonTopKeys": [],
        },
    ])
    sStandardsPath = str(sTestsDir / "qualitative_standards.json")
    with open(sStandardsPath, "w", encoding="utf-8") as fh:
        json.dump(dictStandards, fh)
    sFakeFile = str(sTestsDir / "test_qualitative.py")
    sCode = fsBuildQualitativeTestCode()
    dictNs = {"__file__": sFakeFile}
    exec(compile(sCode, "<qualitative>", "exec"), dictNs)
    dictEntry = dictStandards["listStandards"][0]
    dictNs["test_qualitative_check"](dictEntry)


# -----------------------------------------------------------------------
# C. Error messages contain useful information
# -----------------------------------------------------------------------


def test_npy_error_includes_filepath(tmp_path):
    """Verify error from corrupt npy includes the file path."""
    sPath = str(tmp_path / "bad.npy")
    with open(sPath, "wb") as fh:
        fh.write(b"not numpy")
    dictNs = _fdictExecTemplate()
    with pytest.raises(ValueError, match="bad.npy"):
        dictNs["_fLoadValue"]("bad.npy", "index:0", str(tmp_path))


def test_json_error_includes_format(tmp_path):
    """Verify error from corrupt JSON mentions the format."""
    sPath = str(tmp_path / "bad.json")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("{invalid json")
    dictNs = _fdictExecTemplate()
    with pytest.raises(ValueError, match="json"):
        dictNs["_fLoadValue"]("bad.json", "key:x", str(tmp_path))


def test_csv_error_preserves_original(tmp_path):
    """Verify error from missing CSV column preserves info."""
    sPath = str(tmp_path / "data.csv")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("x,y\n1.0,2.0\n")
    dictNs = _fdictExecTemplate()
    with pytest.raises(ValueError, match="data.csv"):
        dictNs["_fLoadValue"](
            "data.csv", "column:missing,index:0", str(tmp_path),
        )


# -----------------------------------------------------------------------
# D. Quantitative template end-to-end with real data
# -----------------------------------------------------------------------


def test_quantitative_template_end_to_end(tmp_path):
    """Create real data + standards JSON and exec the full template."""
    daData = np.array([10.0, 20.0, 30.0])
    np.save(str(tmp_path / "output.npy"), daData)
    dictStandards = {
        "fDefaultRtol": 1e-6,
        "listStandards": [
            {
                "sName": "fOutputFirst",
                "sDataFile": "output.npy",
                "sAccessPath": "index:0",
                "fValue": 10.0,
                "sUnit": "",
            },
            {
                "sName": "fOutputLast",
                "sDataFile": "output.npy",
                "sAccessPath": "index:-1",
                "fValue": 30.0,
                "sUnit": "",
            },
        ],
    }
    sTestsDir = tmp_path / "tests"
    sTestsDir.mkdir()
    sStandardsPath = str(sTestsDir / "quantitative_standards.json")
    with open(sStandardsPath, "w", encoding="utf-8") as fh:
        json.dump(dictStandards, fh)
    sCode = fsBuildQuantitativeTestCode()
    sFakeFile = str(sTestsDir / "test_quantitative.py")
    dictLocals = {"__file__": sFakeFile}
    exec(compile(sCode, "<template>", "exec"), dictLocals)
    for dictStandard in dictStandards["listStandards"]:
        dictLocals["test_quantitative_benchmark"](dictStandard)


# -----------------------------------------------------------------------
# E. Edge cases in code generators
# -----------------------------------------------------------------------


def test_qualitative_standards_special_chars():
    """Column names with special chars are preserved in JSON."""
    listdictReports = [
        {
            "sFileName": "data.csv",
            "sFormat": "csv",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": ["it's", 'col"2', "back\\slash"],
            "listJsonTopKeys": [],
        },
    ]
    dictResult = _fdictBuildQualitativeStandards(listdictReports)
    sJson = json.dumps(dictResult)
    json.loads(sJson)
    listCols = dictResult["listStandards"][0]["listExpectedColumns"]
    assert "it's" in listCols


def test_qualitative_standards_json_keys_special_chars():
    """JSON keys with dots and brackets are preserved in standards."""
    listdictReports = [
        {
            "sFileName": "config.json",
            "sFormat": "json",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": [],
            "listJsonTopKeys": ["key.with.dots", "key[0]", "normal"],
        },
    ]
    dictResult = _fdictBuildQualitativeStandards(listdictReports)
    listKeys = dictResult["listStandards"][0]["listExpectedJsonKeys"]
    assert "key.with.dots" in listKeys
    assert "key[0]" in listKeys


def test_integrity_standards_not_exists_excluded():
    """File that does not exist is excluded from standards."""
    listdictReports = [
        {
            "sFileName": "missing.npy",
            "sFormat": "npy",
            "bExists": False,
            "bLoadable": False,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": None,
        },
    ]
    dictResult = _fdictBuildIntegrityStandards(listdictReports)
    assert len(dictResult["listStandards"]) == 0


def test_qualitative_standards_columns_and_json_keys():
    """Report with both column names and JSON keys in separate files."""
    listdictReports = [
        {
            "sFileName": "hybrid.csv",
            "sFormat": "csv",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": ["time", "flux"],
            "listJsonTopKeys": [],
        },
        {
            "sFileName": "meta.json",
            "sFormat": "json",
            "bExists": True,
            "bLoadable": True,
            "listColumnNames": [],
            "listJsonTopKeys": ["model", "version"],
        },
    ]
    dictResult = _fdictBuildQualitativeStandards(listdictReports)
    assert len(dictResult["listStandards"]) == 2
    assert "time" in dictResult["listStandards"][0]["listExpectedColumns"]
    assert "model" in dictResult["listStandards"][1]["listExpectedJsonKeys"]


# -----------------------------------------------------------------------
# F. _flistParseIntrospectionOutput robustness
# -----------------------------------------------------------------------


def test_flistParseIntrospectionOutput_multiple_json_arrays():
    """Should take the last JSON array when multiple are present."""
    sOutput = (
        json.dumps([{"sFileName": "wrong.npy", "bExists": False}])
        + "\n"
        + json.dumps([{"sFileName": "right.npy", "bExists": True}])
    )
    listResult = _flistParseIntrospectionOutput(sOutput)
    assert listResult[0]["sFileName"] == "right.npy"


def test_flistParseIntrospectionOutput_large_prefix():
    """Should extract JSON even with a large non-JSON prefix."""
    sPrefix = "WARNING: " * 1000
    sJson = json.dumps([{"sFileName": "data.npy", "bExists": True}])
    sOutput = sPrefix + "\n" + sJson
    listResult = _flistParseIntrospectionOutput(sOutput)
    assert listResult[0]["sFileName"] == "data.npy"


def test_flistParseIntrospectionOutput_partial_json():
    """Should raise ValueError on partial JSON without valid array."""
    with pytest.raises(ValueError, match="not valid JSON"):
        _flistParseIntrospectionOutput(
            '{"incomplete": true\nno array here'
        )


# -----------------------------------------------------------------------
# G. Integration mock — verify written test content validity
# -----------------------------------------------------------------------


def test_fdictGenerateAllTestsDeterministic_written_content():
    """Verify written test files are valid Python and standards JSON."""
    from vaibify.gui.testGenerator import fdictGenerateAllTestsDeterministic

    sIntrospectionOutput = json.dumps([
        {
            "sFileName": "data.csv",
            "sFormat": "csv",
            "bExists": True,
            "iByteSize": 100,
            "bLoadable": True,
            "sError": "",
            "tShape": [5, 2],
            "sDtype": "",
            "iNanCount": 0,
            "iInfCount": 0,
            "listColumnNames": ["time", "flux"],
            "bHasHeader": True,
            "listJsonTopKeys": [],
            "dictJsonScalars": {},
            "listBenchmarks": [
                {
                    "sName": "fTimeFirst",
                    "sDataFile": "data.csv",
                    "sAccessPath": "column:time,index:0",
                    "fValue": 0.0,
                },
                {
                    "sName": "fFluxLast",
                    "sDataFile": "data.csv",
                    "sAccessPath": "column:flux,index:-1",
                    "fValue": 5.0,
                },
            ],
        },
    ])
    dictWrittenFiles = {}

    def fMockExecute(sContainerId, sCommand, sUser=None):
        if "mkdir" in sCommand:
            return (0, "")
        if "python3" in sCommand:
            return (0, sIntrospectionOutput)
        if "rm -f" in sCommand:
            return (0, "")
        return (0, "")

    def fMockWriteFile(sContainerId, sPath, baContent):
        dictWrittenFiles[sPath] = baContent

    mockConnection = MagicMock()
    mockConnection.ftResultExecuteCommand.side_effect = fMockExecute
    mockConnection.fnWriteFile = fMockWriteFile

    dictWorkflow = {
        "listSteps": [{
            "sName": "Analyze",
            "sDirectory": "/work/step01",
            "saDataCommands": ["python run.py"],
            "saDataFiles": ["data.csv"],
        }],
        "fTolerance": 1e-6,
    }
    dictResult = fdictGenerateAllTestsDeterministic(
        mockConnection, "cid123", 0, dictWorkflow, {},
    )
    sIntegrityPath = "/work/step01/tests/test_integrity.py"
    sIntegrityStdPath = "/work/step01/tests/integrity_standards.json"
    sQualitativePath = "/work/step01/tests/test_qualitative.py"
    sQualitativeStdPath = "/work/step01/tests/qualitative_standards.json"
    sQuantitativePath = "/work/step01/tests/test_quantitative.py"
    sQuantitativeStdPath = "/work/step01/tests/quantitative_standards.json"
    assert sIntegrityPath in dictWrittenFiles
    assert sIntegrityStdPath in dictWrittenFiles
    assert sQualitativePath in dictWrittenFiles
    assert sQualitativeStdPath in dictWrittenFiles
    assert sQuantitativePath in dictWrittenFiles
    assert sQuantitativeStdPath in dictWrittenFiles
    sIntegrityCode = dictWrittenFiles[sIntegrityPath].decode("utf-8")
    ast.parse(sIntegrityCode)
    assert "test_integrity_check" in sIntegrityCode
    dictIntegrityStd = json.loads(
        dictWrittenFiles[sIntegrityStdPath].decode("utf-8"),
    )
    assert "listStandards" in dictIntegrityStd
    sQualitativeCode = dictWrittenFiles[sQualitativePath].decode("utf-8")
    ast.parse(sQualitativeCode)
    assert "test_qualitative_check" in sQualitativeCode
    dictQualitativeStd = json.loads(
        dictWrittenFiles[sQualitativeStdPath].decode("utf-8"),
    )
    assert "listStandards" in dictQualitativeStd
    sQuantitativeCode = dictWrittenFiles[sQuantitativePath].decode("utf-8")
    ast.parse(sQuantitativeCode)
    dictQuantitativeStd = json.loads(
        dictWrittenFiles[sQuantitativeStdPath].decode("utf-8"),
    )
    assert "fDefaultRtol" in dictQuantitativeStd
    assert "listStandards" in dictQuantitativeStd
    assert len(dictQuantitativeStd["listStandards"]) == 2
    assert dictQuantitativeStd["listStandards"][0]["sName"] == "fTimeFirst"


def test_fdictGenerateAllTestsDeterministic_no_data_files():
    """Verify minimal tests when no data files are present."""
    from vaibify.gui.testGenerator import fdictGenerateAllTestsDeterministic

    sIntrospectionOutput = json.dumps([])
    dictWrittenFiles = {}

    def fMockExecute(sContainerId, sCommand, sUser=None):
        if "mkdir" in sCommand:
            return (0, "")
        if "python3" in sCommand:
            return (0, sIntrospectionOutput)
        if "rm -f" in sCommand:
            return (0, "")
        return (0, "")

    def fMockWriteFile(sContainerId, sPath, baContent):
        dictWrittenFiles[sPath] = baContent

    mockConnection = MagicMock()
    mockConnection.ftResultExecuteCommand.side_effect = fMockExecute
    mockConnection.fnWriteFile = fMockWriteFile

    dictWorkflow = {
        "listSteps": [{
            "sName": "Empty",
            "sDirectory": "/work/step02",
            "saDataCommands": [],
            "saDataFiles": [],
        }],
        "fTolerance": 1e-4,
    }
    dictResult = fdictGenerateAllTestsDeterministic(
        mockConnection, "cid123", 0, dictWorkflow, {},
    )
    sIntegrityPath = "/work/step02/tests/test_integrity.py"
    sIntegrityCode = dictWrittenFiles[sIntegrityPath].decode("utf-8")
    ast.parse(sIntegrityCode)
    assert "test_no_integrity_outputs" in sIntegrityCode
    sQualitativePath = "/work/step02/tests/test_qualitative.py"
    sQualitativeCode = dictWrittenFiles[sQualitativePath].decode(
        "utf-8",
    )
    ast.parse(sQualitativeCode)
    assert "test_no_qualitative_outputs" in sQualitativeCode


# -----------------------------------------------------------------------
# Additional edge cases for module-level loaders
# -----------------------------------------------------------------------


def test_loader_vcf_aggregate(tmp_path):
    """VCF loader aggregate (mean) works."""
    sPath = str(tmp_path / "data.vcf")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("##fileformat=VCFv4.2\n")
        fh.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")
        fh.write("chr1\t100\t.\tA\tT\t30.0\tPASS\t.\n")
        fh.write("chr1\t300\t.\tG\tC\t50.0\tPASS\t.\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.vcf", "column:POS,index:mean", str(tmp_path),
    )
    assert abs(fResult - 200.0) < 1e-10


def test_loader_sam_last_row(tmp_path):
    """SAM loader can access last row via index:-1."""
    sPath = str(tmp_path / "data.sam")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("@HD\tVN:1.6\n")
        fh.write("r1\t0\tchr1\t100\t60\t4M\t*\t0\t0\tACGT\tIIII\n")
        fh.write("r2\t0\tchr1\t999\t42\t4M\t*\t0\t0\tTGCA\tIIII\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.sam", "column:POS,index:-1", str(tmp_path),
    )
    assert fResult == 999.0


def test_loader_bed_score(tmp_path):
    """BED loader can read the score column."""
    sPath = str(tmp_path / "data.bed")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("chr1\t0\t100\tgene1\t750\t+\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.bed", "column:score,index:0", str(tmp_path),
    )
    assert fResult == 750.0


def test_loader_gff_end_column(tmp_path):
    """GFF loader can read the end column."""
    sPath = str(tmp_path / "data.gff")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("seq\tsrc\tgene\t1\t500\t.\t+\t.\tID=g1\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "data.gff", "column:end,index:0", str(tmp_path),
    )
    assert fResult == 500.0


def test_loader_fasta_aggregate_mean(tmp_path):
    """FASTA loader mean aggregate works."""
    sPath = str(tmp_path / "seqs.fasta")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write(">seq1\nACGT\n>seq2\nACGTACGT\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "seqs.fasta", "index:mean", str(tmp_path),
    )
    assert abs(fResult - 6.0) < 1e-10


def test_loader_fastq_quality(tmp_path):
    """FASTQ loader quality key works."""
    sPath = str(tmp_path / "reads.fastq")
    with open(sPath, "w", encoding="utf-8") as fh:
        fh.write("@r1\nACGT\n+\nIIII\n@r2\nACGT\n+\nAAAA\n")
    dictNs = _fdictExecTemplate()
    fResult = dictNs["_fLoadValue"](
        "reads.fastq", "key:quality,index:0", str(tmp_path),
    )
    assert fResult > 0.0


# -----------------------------------------------------------------------
# _flistParseIntrospectionOutput — line-by-line fallback (2665-2666)
# -----------------------------------------------------------------------


def test_flistParseIntrospectionOutput_line_fallback():
    """Lines 2660-2666: JSON on last line after non-JSON prefix."""
    sOutput = 'Loading data...\nProcessing...\n[{"sFile": "x.csv"}]'
    listResult = _flistParseIntrospectionOutput(sOutput)
    assert listResult[0]["sFile"] == "x.csv"


def test_flistParseIntrospectionOutput_invalid_line_skipped():
    """Lines 2665-2666: invalid JSON line skipped, valid found."""
    sOutput = "[bad json\n" + '[{"sFile": "y.csv"}]'
    listResult = _flistParseIntrospectionOutput(sOutput)
    assert listResult[0]["sFile"] == "y.csv"


def test_flistParseIntrospectionOutput_all_invalid_raises():
    """Lines 2667-2669: no valid JSON anywhere raises ValueError."""
    with pytest.raises(ValueError, match="not valid JSON"):
        _flistParseIntrospectionOutput("no json here\nat all")


# -----------------------------------------------------------------------
# Integrity NaN check — unloadable file gets no NaN check
# -----------------------------------------------------------------------


def test_integrity_standards_unloadable_no_nan_check():
    """Unloadable files should have bCheckNaN=False."""
    dictResult = _fdictBuildIntegrityStandards([
        {
            "sFileName": "file.csv",
            "sFormat": "csv",
            "bExists": True,
            "bLoadable": False,
            "iNanCount": 0,
            "iInfCount": 0,
            "tShape": None,
        },
    ])
    assert dictResult["listStandards"][0]["bCheckNaN"] is False


# -----------------------------------------------------------------------
# _fsRemoveOldTestSection
# -----------------------------------------------------------------------


def test_fsRemoveOldTestSection_no_marker():
    sInput = "# Some content\nNo marker here"
    sResult = _fsRemoveOldTestSection(sInput)
    assert sResult == sInput


def test_fsRemoveOldTestSection_with_marker():
    sInput = (
        "# Existing content\n"
        "# Vaibify Test Generation Instructions\n"
        "old stuff here"
    )
    sResult = _fsRemoveOldTestSection(sInput)
    assert "Vaibify Test Generation Instructions" not in sResult
    assert "Existing content" in sResult


# -----------------------------------------------------------------------
# Stochasticity classification, tolerance helpers, distributional metrics
# -----------------------------------------------------------------------

import math as _math

from vaibify.gui.testGenerator import (
    _F_FLOOR_RTOL,
    _F_SIGMA_MULT,
    _fdictMergePreservingOverrides,
    _fsClassifyStochasticity,
    _ftolMeanFromCv,
    _ftolPercentileFromN,
    _ftolStdFromN,
    fbStepProducesStochasticOutputs,
)


def test_fbStepProducesStochasticOutputs_dynesty():
    sScript = "import dynesty\nsampler = dynesty.NestedSampler(...)"
    assert fbStepProducesStochasticOutputs({}, sScript) is True


def test_fbStepProducesStochasticOutputs_pure_pandas():
    sScript = "import pandas as pd\ndf = pd.read_csv('input.csv')"
    assert fbStepProducesStochasticOutputs({}, sScript) is False


def test_fbStepProducesStochasticOutputs_random_call_no_framework():
    sScript = "import numpy as np\narr = np.random.normal(0, 1, 1000)"
    assert fbStepProducesStochasticOutputs({}, sScript) is True


def test_fbStepProducesStochasticOutputs_emcee():
    assert fbStepProducesStochasticOutputs(
        {}, "from emcee import EnsembleSampler",
    ) is True


def test_fbStepProducesStochasticOutputs_empty_string():
    assert fbStepProducesStochasticOutputs({}, "") is False


def _flistdictStochasticReports(iSamples=128):
    listBench = []
    for sName, sKind in [
        ("fXFirst", "first"), ("fXLast", "last"),
        ("fXMin", "min"), ("fXMax", "max"),
        ("fXMean", "mean"), ("fXStd", "std"),
        ("fXP5", "percentile_5"), ("fXP25", "percentile_25"),
        ("fXP50", "percentile_50"), ("fXP75", "percentile_75"),
        ("fXP95", "percentile_95"),
    ]:
        listBench.append({
            "sName": sName, "sDataFile": "x.npy",
            "sAccessPath": "index:0", "fValue": 1.5,
            "sMetricKind": sKind, "iSampleSize": iSamples,
            "fObservedCv": 0.05,
        })
    return [{
        "sFileName": "x.npy", "listBenchmarks": listBench,
        "bLoadable": True,
    }]


def test_fdictBuildQuantitativeStandards_stochastic_drops_extremes():
    listdictReports = _flistdictStochasticReports(iSamples=1000)
    dictResult = _fdictBuildQuantitativeStandards(
        listdictReports, 1e-6, "stochastic",
    )
    setNames = {s["sName"] for s in dictResult["listStandards"]}
    assert "fXFirst" not in setNames
    assert "fXLast" not in setNames
    assert "fXMin" not in setNames
    assert "fXMax" not in setNames


def test_fdictBuildQuantitativeStandards_stochastic_includes_percentiles():
    listdictReports = _flistdictStochasticReports(iSamples=1000)
    dictResult = _fdictBuildQuantitativeStandards(
        listdictReports, 1e-6, "stochastic",
    )
    setNames = {s["sName"] for s in dictResult["listStandards"]}
    assert setNames == {
        "fXMean", "fXStd", "fXP5", "fXP25",
        "fXP50", "fXP75", "fXP95",
    }


def test_ftolMeanFromCv_known_value():
    fRtol = _ftolMeanFromCv(0.05, 1000)
    fExpected = max(_F_SIGMA_MULT * 0.05 / _math.sqrt(1000), _F_FLOOR_RTOL)
    assert abs(fRtol - fExpected) < 1e-12


def test_ftolStdFromN_known_value():
    fRtol = _ftolStdFromN(1000)
    fExpected = max(
        _F_SIGMA_MULT * _math.sqrt(2.0 / 999), _F_FLOOR_RTOL,
    )
    assert abs(fRtol - fExpected) < 1e-12


def test_ftolPercentileFromN_uses_floor_for_zero_value():
    assert _ftolPercentileFromN(0.5, 1000, 0.05, 0.0) == _F_FLOOR_RTOL


def test_fdictMergePreservingOverrides_keeps_user_fRtol():
    dictNew = {"listStandards": [
        {"sName": "fX", "fValue": 1.0, "fRtol": 1e-6},
    ]}
    dictOld = {"listStandards": [
        {"sName": "fX", "fValue": 1.0, "fRtol": 0.05, "sNote": "kept"},
    ]}
    dictResult = _fdictMergePreservingOverrides(dictNew, dictOld)
    dictEntry = dictResult["listStandards"][0]
    assert dictEntry["fRtol"] == 0.05
    assert dictEntry["sNote"] == "kept"


def test_unseeded_classification_caps_metric_set_to_two():
    listdictReports = _flistdictStochasticReports(iSamples=512)
    dictResult = _fdictBuildQuantitativeStandards(
        listdictReports, 1e-6, "stochastic_unseeded",
    )
    setNames = {s["sName"] for s in dictResult["listStandards"]}
    assert setNames == {"fXMean", "fXP50"}
    for dictStd in dictResult["listStandards"]:
        assert dictStd["fRtol"] == 0.10
        assert "Placeholder" in dictStd["sNote"]


def test_unintrospectable_classification_emits_no_benchmarks():
    listdictReports = [{
        "sFileName": "blob.bin", "sError": "unsupported binary format",
        "bLoadable": False, "listBenchmarks": [],
    }]
    sClass = _fsClassifyStochasticity({}, "import dynesty", listdictReports)
    assert sClass == "unintrospectable"
    dictResult = _fdictBuildQuantitativeStandards(
        listdictReports, 1e-6, sClass,
    )
    assert dictResult["listStandards"] == []
    assert (
        dictResult["sStochasticityClassification"] == "unintrospectable"
    )
    assert "blob.bin" in dictResult["sIntrospectorError"]


def test_unintrospectable_prompt_includes_conservative_tolerance():
    from vaibify.gui.llmInvoker import _CLAUDE_MD_TEST_SECTION
    assert "Conservative Tolerance Rules" in _CLAUDE_MD_TEST_SECTION
    assert "rtol < 1e-3" in _CLAUDE_MD_TEST_SECTION


def test_classifier_deterministic_when_no_random_pattern():
    listdictReports = _flistdictStochasticReports(iSamples=256)
    sClass = _fsClassifyStochasticity(
        {}, "import pandas\nimport numpy as np", listdictReports,
    )
    assert sClass == "deterministic"


def test_classifier_stochastic_when_random_and_large_array():
    listdictReports = _flistdictStochasticReports(iSamples=1024)
    sClass = _fsClassifyStochasticity(
        {}, "import dynesty", listdictReports,
    )
    assert sClass == "stochastic"


def test_classifier_stochastic_unseeded_via_lint_flag():
    listdictReports = _flistdictStochasticReports(iSamples=1024)
    dictStep = {"dictVerification": {"bUnseededRandomnessWarning": True}}
    sClass = _fsClassifyStochasticity(
        dictStep, "import dynesty", listdictReports,
    )
    assert sClass == "stochastic_unseeded"


def test_classifier_deterministic_when_array_too_small():
    listdictReports = _flistdictStochasticReports(iSamples=16)
    sClass = _fsClassifyStochasticity(
        {}, "import dynesty", listdictReports,
    )
    assert sClass == "deterministic"


# -----------------------------------------------------------------------
# Introspection script: distributional benchmark emission
# -----------------------------------------------------------------------


def test_introspection_emits_distributional_for_stochastic_npy(tmp_path):
    """Generated script labels and emits distributional metrics."""
    import numpy as np
    sPath = str(tmp_path / "samples.npy")
    np.random.seed(7)
    daSamples = np.random.normal(loc=2.0, scale=0.5, size=512)
    np.save(sPath, daSamples)
    sScript = _fsBuildIntrospectionScript(
        ["samples.npy"], str(tmp_path), bScriptStochastic=True,
    )
    dictNs = {}
    exec(compile(sScript, "<introspect>", "exec"), dictNs)
    listReports = dictNs["listReports"]
    setKinds = {
        d["sMetricKind"]
        for d in listReports[0]["listBenchmarks"]
    }
    assert "percentile_50" in setKinds
    assert "std" in setKinds
    assert "mean" in setKinds


def test_introspection_omits_distributional_for_deterministic(tmp_path):
    """Without script-stochastic flag, no distributional metrics emit."""
    import numpy as np
    sPath = str(tmp_path / "deterministic.npy")
    np.save(sPath, np.arange(512, dtype=float))
    sScript = _fsBuildIntrospectionScript(
        ["deterministic.npy"], str(tmp_path), bScriptStochastic=False,
    )
    dictNs = {}
    exec(compile(sScript, "<introspect>", "exec"), dictNs)
    listReports = dictNs["listReports"]
    setKinds = {
        d["sMetricKind"]
        for d in listReports[0]["listBenchmarks"]
    }
    assert "percentile_50" not in setKinds
    assert "std" not in setKinds


def test_introspection_emits_observed_cv(tmp_path):
    """Each numeric benchmark carries fObservedCv and iSampleSize."""
    import numpy as np
    sPath = str(tmp_path / "cv.npy")
    np.save(sPath, np.array([1.0, 1.1, 0.9, 1.0, 1.05]))
    sScript = _fsBuildIntrospectionScript(
        ["cv.npy"], str(tmp_path), bScriptStochastic=False,
    )
    dictNs = {}
    exec(compile(sScript, "<introspect>", "exec"), dictNs)
    listBench = dictNs["listReports"][0]["listBenchmarks"]
    assert all("iSampleSize" in d for d in listBench)
    assert all("fObservedCv" in d for d in listBench)


# -----------------------------------------------------------------------
# Aggregate access path extensions in dataLoaders
# -----------------------------------------------------------------------


def test_loader_npy_percentile_aggregates(tmp_path):
    import numpy as np
    sPath = str(tmp_path / "data.npy")
    np.save(sPath, np.arange(101, dtype=float))
    dictNs = _fdictExecTemplate()
    fP50 = dictNs["_fLoadValue"](
        "data.npy", "index:p50", str(tmp_path),
    )
    assert fP50 == 50.0
    fP5 = dictNs["_fLoadValue"](
        "data.npy", "index:p5", str(tmp_path),
    )
    assert fP5 == 5.0
    fP95 = dictNs["_fLoadValue"](
        "data.npy", "index:p95", str(tmp_path),
    )
    assert fP95 == 95.0


def test_loader_npy_std_aggregate(tmp_path):
    import numpy as np
    sPath = str(tmp_path / "data.npy")
    daValues = np.arange(101, dtype=float)
    np.save(sPath, daValues)
    dictNs = _fdictExecTemplate()
    fStd = dictNs["_fLoadValue"](
        "data.npy", "index:std", str(tmp_path),
    )
    assert abs(fStd - float(daValues.std(ddof=1))) < 1e-9


# -----------------------------------------------------------------------
# Tolerance branches in _ftolMeanFromCv / _ftolStdFromN / etc.
# -----------------------------------------------------------------------


from vaibify.gui.testGenerator import (
    _F_UNSEEDED_RTOL,
    _fbBenchmarkPassesFilter,
    _fdictAssignTolerance,
    _fdictMergeWithExistingStandards,
    _fdictGenerateSingleCategory,
    _fdictGenerateQuantitativeCategory,
    _fdictErrorResult,
    _fnAppendErrorLog,
    _ftolForStochasticKind,
    _ftExtractStepInfo,
)


def test_ftolMeanFromCv_returns_floor_when_sample_size_zero():
    """Line 445: iSampleSize <= 0 returns the floor rtol."""
    assert _ftolMeanFromCv(0.05, 0) == _F_FLOOR_RTOL


def test_ftolMeanFromCv_returns_floor_when_sample_size_negative():
    assert _ftolMeanFromCv(0.05, -3) == _F_FLOOR_RTOL


def test_ftolStdFromN_returns_floor_when_sample_size_below_two():
    """Line 459: N < 2 returns the floor rtol."""
    assert _ftolStdFromN(1) == _F_FLOOR_RTOL
    assert _ftolStdFromN(0) == _F_FLOOR_RTOL


def test_ftolForStochasticKind_returns_default_for_unknown_kind():
    """Line 499: an unrecognised sMetricKind falls through to default."""
    dictStandard = {
        "sMetricKind": "kurtosis", "iSampleSize": 100,
        "fObservedCv": 0.05, "fValue": 1.0,
    }
    fRtol = _ftolForStochasticKind(dictStandard, 0.123)
    assert fRtol == 0.123


def test_ftolForStochasticKind_dispatches_to_std_for_std_kind():
    dictStandard = {
        "sMetricKind": "std", "iSampleSize": 1000,
        "fObservedCv": 0.05, "fValue": 1.0,
    }
    fRtol = _ftolForStochasticKind(dictStandard, 0.123)
    assert fRtol != 0.123
    assert fRtol > 0


def test_fbBenchmarkPassesFilter_unknown_classification_excludes_all():
    """Line 530: an unknown sClassification rejects every benchmark."""
    dictBench = {"sMetricKind": "mean"}
    assert _fbBenchmarkPassesFilter(dictBench, "unintrospectable") is False
    assert _fbBenchmarkPassesFilter(dictBench, "garbage_label") is False


def test_fbBenchmarkPassesFilter_deterministic_admits_single_and_mean():
    assert _fbBenchmarkPassesFilter(
        {"sMetricKind": "single"}, "deterministic",
    ) is True
    assert _fbBenchmarkPassesFilter(
        {"sMetricKind": "mean"}, "deterministic",
    ) is True


def test_fdictAssignTolerance_unseeded_tags_placeholder():
    dictResult = _fdictAssignTolerance(
        {"sMetricKind": "mean", "iSampleSize": 1000, "fObservedCv": 0.1},
        "stochastic_unseeded", 1e-6,
    )
    assert dictResult["fRtol"] == _F_UNSEEDED_RTOL
    assert "Placeholder" in dictResult["sNote"]


def test_fdictMergePreservingOverrides_continues_when_no_prior_match():
    """Line 599: a benchmark not present in dictOld is left as-is."""
    dictNew = {"listStandards": [
        {"sName": "fX", "fValue": 1.0, "fRtol": 1e-6},
        {"sName": "fNew", "fValue": 2.0, "fRtol": 1e-6},
    ]}
    dictOld = {"listStandards": [
        {"sName": "fX", "fValue": 1.0, "fRtol": 0.05},
    ]}
    dictResult = _fdictMergePreservingOverrides(dictNew, dictOld)
    dictByName = {s["sName"]: s for s in dictResult["listStandards"]}
    assert dictByName["fX"]["fRtol"] == 0.05
    assert dictByName["fNew"]["fRtol"] == 1e-6


def test_fdictMergeWithExistingStandards_merges_when_prior_file_exists():
    """Line 795: existing standards on disk feed into the merge."""
    sExisting = json.dumps({"listStandards": [
        {"sName": "fX", "fValue": 1.0, "fRtol": 0.42, "sNote": "kept"},
    ]})
    mockDocker = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "vaibify.gui.testGenerator.fsReadFileFromContainer",
            lambda *args, **kwargs: sExisting,
        )
        dictResult = _fdictMergeWithExistingStandards(
            mockDocker, "cid", "/sweep/tests/quantitative_standards.json",
            {"listStandards": [
                {"sName": "fX", "fValue": 1.0, "fRtol": 1e-6},
            ]},
        )
    assert dictResult["listStandards"][0]["fRtol"] == 0.42
    assert dictResult["listStandards"][0]["sNote"] == "kept"


def test_fdictMergeWithExistingStandards_returns_input_when_no_existing():
    mockDocker = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "vaibify.gui.testGenerator.fsReadFileFromContainer",
            lambda *args, **kwargs: "",
        )
        dictInput = {"listStandards": []}
        dictResult = _fdictMergeWithExistingStandards(
            mockDocker, "cid", "/sweep/tests/quantitative_standards.json",
            dictInput,
        )
    assert dictResult is dictInput


def test_fdictMergeWithExistingStandards_returns_input_on_invalid_json():
    mockDocker = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "vaibify.gui.testGenerator.fsReadFileFromContainer",
            lambda *args, **kwargs: "{ invalid json",
        )
        dictInput = {"listStandards": []}
        dictResult = _fdictMergeWithExistingStandards(
            mockDocker, "cid", "/sweep/tests/quantitative_standards.json",
            dictInput,
        )
    assert dictResult is dictInput


def test_ftExtractStepInfo_joins_repo_root_for_relative_directory():
    """Line 293: relative sDirectory is joined with sProjectRepoPath."""
    dictWorkflow = {
        "sProjectRepoPath": "/repo",
        "listSteps": [{"sDirectory": "sweep"}],
    }
    dictStep, sDir = _ftExtractStepInfo(dictWorkflow, 0)
    assert sDir == "/repo/sweep"
    assert dictStep["sDirectory"] == "sweep"


def test_ftExtractStepInfo_preserves_absolute_directory():
    dictWorkflow = {
        "sProjectRepoPath": "/repo",
        "listSteps": [{"sDirectory": "/abs/sweep"}],
    }
    _, sDir = _ftExtractStepInfo(dictWorkflow, 0)
    assert sDir == "/abs/sweep"


def test_fdictErrorResult_returns_standard_shape():
    dictResult = _fdictErrorResult("kaboom")
    assert dictResult["sFilePath"] == ""
    assert dictResult["sError"] == "kaboom"
    assert dictResult["saCommands"] == []


def test_fnAppendErrorLog_swallows_filesystem_errors(tmp_path):
    """Lines 1051-1052: the helper must not raise when log write fails."""
    import tempfile
    import unittest.mock as _mock
    with _mock.patch(
        "tempfile.gettempdir", return_value=str(tmp_path / "missing"),
    ):
        # The directory does not exist; open() will raise FileNotFoundError
        # which the helper swallows.
        _fnAppendErrorLog("error message")  # must not raise


def test_fdictGenerateSingleCategory_returns_error_when_llm_raises():
    """Lines 993-998: LLM exception inside category generation."""
    mockDocker = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "vaibify.gui.testGenerator._fsInvokeLlm",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("llm down"),
            ),
        )
        dictResult = _fdictGenerateSingleCategory(
            mockDocker, "cid", "/sweep", "integrity",
            "data files", "scripts", "previews",
            False, None, None,
        )
    assert dictResult["sError"] == "llm down"
    assert dictResult["sFilePath"] == ""


def test_fdictGenerateQuantitativeCategory_returns_error_on_failure():
    """Lines 1023-1024: quantitative LLM exception falls through."""
    mockDocker = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "vaibify.gui.testGenerator._fsInvokeLlm",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                ValueError("bad prompt"),
            ),
        )
        dictResult = _fdictGenerateQuantitativeCategory(
            mockDocker, "cid", "/sweep",
            "data files", "scripts", "previews",
            1e-6, False, None, None,
        )
    assert dictResult["sError"] == "bad prompt"
