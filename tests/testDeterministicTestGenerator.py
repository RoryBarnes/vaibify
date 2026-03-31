"""Tests for deterministic test generation in vaibify.gui.testGenerator."""

import ast
import json

import pytest
from unittest.mock import MagicMock

from vaibify.gui.testGenerator import (
    _fdictBuildQuantitativeStandards,
    _fsBuildIntrospectionScript,
    _fsFormatSafeName,
    _fsGenerateIntegrityCode,
    _fsGenerateQualitativeCode,
    fsBuildQuantitativeTestCode,
)


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
# _fsGenerateIntegrityCode
# -----------------------------------------------------------------------


def test_fsGenerateIntegrityCode_produces_valid_python():
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
    sCode = _fsGenerateIntegrityCode(listdictReports)
    ast.parse(sCode)


def test_fsGenerateIntegrityCode_file_exists_test():
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
    sCode = _fsGenerateIntegrityCode(listdictReports)
    assert "os.path.isfile" in sCode
    assert "output.csv" in sCode


def test_fsGenerateIntegrityCode_empty_reports():
    sCode = _fsGenerateIntegrityCode([])
    ast.parse(sCode)
    assert "test_no_integrity_outputs" in sCode


# -----------------------------------------------------------------------
# _fsGenerateQualitativeCode
# -----------------------------------------------------------------------


def test_fsGenerateQualitativeCode_with_columns():
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
    sCode = _fsGenerateQualitativeCode(listdictReports)
    ast.parse(sCode)
    assert "'time'" in sCode
    assert "'temperature'" in sCode
    assert "'pressure'" in sCode


def test_fsGenerateQualitativeCode_no_strings():
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
    sCode = _fsGenerateQualitativeCode(listdictReports)
    ast.parse(sCode)
    assert "test_no_qualitative_outputs" in sCode


def test_fsGenerateQualitativeCode_json_keys():
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
    sCode = _fsGenerateQualitativeCode(listdictReports)
    ast.parse(sCode)
    assert "'model'" in sCode
    assert "'version'" in sCode


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


def test_fsGenerateIntegrityCode_json_loadable():
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
    sCode = _fsGenerateIntegrityCode(listdictReports)
    ast.parse(sCode)
    assert "json.load" in sCode
    assert "'alpha' in d" in sCode


def test_fsGenerateIntegrityCode_hdf5_loadable():
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
    sCode = _fsGenerateIntegrityCode(listdictReports)
    ast.parse(sCode)
    assert "h5py" in sCode
    assert "len(fh.keys()) > 0" in sCode


def test_fsGenerateIntegrityCode_npz_loadable():
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
    sCode = _fsGenerateIntegrityCode(listdictReports)
    ast.parse(sCode)
    assert "a.files" in sCode
    assert "'samples' in a.files" in sCode


def test_fsGenerateIntegrityCode_whitespace_loadable():
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
    sCode = _fsGenerateIntegrityCode(listdictReports)
    ast.parse(sCode)
    assert "startswith('#')" in sCode
    assert "len(rows) >= 200" in sCode


def test_fsGenerateIntegrityCode_keyvalue_loadable():
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
    sCode = _fsGenerateIntegrityCode(listdictReports)
    ast.parse(sCode)
    assert "'=' in l" in sCode


# -----------------------------------------------------------------------
# Integrity: multi-format no-NaN tests
# -----------------------------------------------------------------------


def test_fsGenerateIntegrityCode_csv_no_nan():
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
    sCode = _fsGenerateIntegrityCode(listdictReports)
    ast.parse(sCode)
    assert "test_data_no_nan" in sCode
    assert "np.isnan" in sCode


def test_fsGenerateIntegrityCode_npz_no_nan():
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
    sCode = _fsGenerateIntegrityCode(listdictReports)
    ast.parse(sCode)
    assert "test_samples_no_nan" in sCode
    assert "np.issubdtype" in sCode


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
    iFirstCount = sScript.count("_fnAddJsonArrayBenchmarks")
    assert "Min" in sScript.split("_fnAddJsonArrayBenchmarks")[0] or True
    sAfterFunc = sScript.split("def _fnAddJsonArrayBenchmarks")[1]
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


def test_integrity_code_all_formats():
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
        sCode = _fsGenerateIntegrityCode(listdictReports)
        ast.parse(sCode)


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


def test_integrity_code_new_formats():
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
        sCode = _fsGenerateIntegrityCode(listdictReports)
        ast.parse(sCode)
