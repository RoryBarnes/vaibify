"""Direct coverage tests for vaibify.gui.dataLoaders.

Unlike testDeterministicTestGenerator.py (which exec()s the embedded
template string), these tests import fLoadValue and the internal helpers
directly from the dataLoaders module so that line coverage is credited
to the real source file.
"""

import gzip
import json
import pickle

import numpy as np
import pytest

from vaibify.gui import dataLoaders
from vaibify.gui.dataLoaders import (
    DICT_FORMAT_MAP,
    DICT_LOADERS,
    fLoadValue,
    fsReadLoaderSource,
)


# ----------------------------------------------------------------------
# _fdictParseAccessPath — syntax variants
# ----------------------------------------------------------------------


def test_parseAccessPath_empty_returns_empty_dict():
    dictResult = dataLoaders._fdictParseAccessPath("")
    assert dictResult == {}


def test_parseAccessPath_key():
    dictResult = dataLoaders._fdictParseAccessPath("key:temperature")
    assert dictResult == {"key": "temperature"}


def test_parseAccessPath_column_and_index():
    dictResult = dataLoaders._fdictParseAccessPath(
        "column:flux,index:-1",
    )
    assert dictResult["column"] == "flux"
    assert dictResult["listIndices"] == [-1]


def test_parseAccessPath_dataset():
    dictResult = dataLoaders._fdictParseAccessPath(
        "dataset:/group/data,index:0",
    )
    assert dictResult["dataset"] == "/group/data"
    assert dictResult["listIndices"] == [0]


def test_parseAccessPath_aggregate_mean():
    dictResult = dataLoaders._fdictParseAccessPath("index:mean")
    assert dictResult["sAggregate"] == "mean"


def test_parseAccessPath_aggregate_min():
    dictResult = dataLoaders._fdictParseAccessPath("index:min")
    assert dictResult["sAggregate"] == "min"


def test_parseAccessPath_aggregate_max():
    dictResult = dataLoaders._fdictParseAccessPath("index:max")
    assert dictResult["sAggregate"] == "max"


def test_parseAccessPath_hdu():
    dictResult = dataLoaders._fdictParseAccessPath(
        "hdu:1,column:flux",
    )
    assert dictResult["iHdu"] == 1


def test_parseAccessPath_section():
    dictResult = dataLoaders._fdictParseAccessPath("section:2,index:0")
    assert dictResult["iSection"] == 2


def test_parseAccessPath_multi_index():
    dictResult = dataLoaders._fdictParseAccessPath("index:2,3")
    assert dictResult["listIndices"] == [2, 3]


# ----------------------------------------------------------------------
# _fsInferFormat & dispatch
# ----------------------------------------------------------------------


def test_inferFormat_known_extension():
    assert dataLoaders._fsInferFormat("file.csv") == "csv"
    assert dataLoaders._fsInferFormat("file.NPY") == "npy"


def test_inferFormat_unknown_returns_none():
    assert dataLoaders._fsInferFormat("file.xyz") is None


def test_dict_format_map_exposed():
    assert DICT_FORMAT_MAP[".npy"] == "npy"
    assert DICT_FORMAT_MAP[".csv"] == "csv"


def test_dict_loaders_exposed():
    assert "npy" in DICT_LOADERS
    assert callable(DICT_LOADERS["npy"])


def test_fLoadValue_unsupported_format_raises(tmp_path):
    sPath = tmp_path / "x.dat"
    sPath.write_text("1 2\n")
    with pytest.raises(ValueError, match="Unsupported format"):
        fLoadValue("x.dat", "", str(tmp_path), sFormat="nosuchformat")


def test_fLoadValue_unknown_extension_falls_back_to_whitespace(tmp_path):
    sPath = tmp_path / "mystery.xyz"
    sPath.write_text("1.0 2.0\n3.0 4.0\n")
    fResult = fLoadValue("mystery.xyz", "index:-1", str(tmp_path))
    assert fResult == 3.0


# ----------------------------------------------------------------------
# _fExtractArrayValue direct coverage
# ----------------------------------------------------------------------


def test_extractArrayValue_zero_dim():
    daZero = np.array(7.5)
    fResult = dataLoaders._fExtractArrayValue(daZero, {})
    assert fResult == 7.5


def test_extractArrayValue_aggregate_min():
    daArr = np.array([3.0, 1.0, 2.0])
    fResult = dataLoaders._fExtractArrayValue(
        daArr, {"sAggregate": "min"},
    )
    assert fResult == 1.0


def test_extractArrayValue_aggregate_max():
    daArr = np.array([3.0, 1.0, 2.0])
    fResult = dataLoaders._fExtractArrayValue(
        daArr, {"sAggregate": "max"},
    )
    assert fResult == 3.0


def test_extractArrayValue_multidim_flat_single_index():
    daArr = np.array([[1.0, 2.0], [3.0, 4.0]])
    fResult = dataLoaders._fExtractArrayValue(
        daArr, {"listIndices": [2]},
    )
    assert fResult == 3.0


def test_extractArrayValue_multidim_tuple_index():
    daArr = np.array([[1.0, 2.0], [3.0, 4.0]])
    fResult = dataLoaders._fExtractArrayValue(
        daArr, {"listIndices": [1, 0]},
    )
    assert fResult == 3.0


# ----------------------------------------------------------------------
# _fExtractTabularValue / _fExtractDataframeValue
# ----------------------------------------------------------------------


def test_extractTabularValue_by_column_name():
    listRows = [["1.0", "2.0"], ["3.0", "4.0"]]
    fResult = dataLoaders._fExtractTabularValue(
        ["a", "b"], listRows, {"column": "b", "listIndices": [0]},
    )
    assert fResult == 2.0


def test_extractTabularValue_aggregate_mean():
    listRows = [["1.0", "2.0"], ["3.0", "4.0"]]
    fResult = dataLoaders._fExtractTabularValue(
        ["a", "b"], listRows, {"column": "b", "sAggregate": "mean"},
    )
    assert fResult == 3.0


def test_extractDataframeValue_by_column():
    pandas = pytest.importorskip("pandas")
    dfData = pandas.DataFrame({"flux": [1.0, 2.0, 3.0]})
    fResult = dataLoaders._fExtractDataframeValue(
        dfData, {"column": "flux", "listIndices": [0]},
    )
    assert fResult == 1.0


def test_extractDataframeValue_aggregate():
    pandas = pytest.importorskip("pandas")
    dfData = pandas.DataFrame({"flux": [1.0, 2.0, 3.0]})
    fResult = dataLoaders._fExtractDataframeValue(
        dfData, {"column": "flux", "sAggregate": "mean"},
    )
    assert fResult == 2.0


def test_extractDataframeValue_missing_column_raises():
    pandas = pytest.importorskip("pandas")
    dfData = pandas.DataFrame({"x": [1.0]})
    with pytest.raises(ValueError, match="Failed to access"):
        dataLoaders._fExtractDataframeValue(
            dfData, {"column": "missing"}, "/tmp/x",
        )


# ----------------------------------------------------------------------
# _fNavigateJsonValue
# ----------------------------------------------------------------------


def test_navigateJsonValue_plain_key():
    fResult = dataLoaders._fNavigateJsonValue(
        {"fMass": 5.0}, {"key": "fMass"},
    )
    assert fResult == 5.0


def test_navigateJsonValue_nested():
    fResult = dataLoaders._fNavigateJsonValue(
        {"outer": {"inner": 9.0}}, {"key": "outer.inner"},
    )
    assert fResult == 9.0


def test_navigateJsonValue_list_aggregate():
    fResult = dataLoaders._fNavigateJsonValue(
        {"daValues": [1.0, 2.0, 3.0]},
        {"key": "daValues", "sAggregate": "mean"},
    )
    assert fResult == 2.0


def test_navigateJsonValue_indexing():
    fResult = dataLoaders._fNavigateJsonValue(
        {"daValues": [10.0, 20.0, 30.0]},
        {"key": "daValues", "listIndices": [1]},
    )
    assert fResult == 20.0


# ----------------------------------------------------------------------
# _fbIsNumericToken and filter helpers
# ----------------------------------------------------------------------


def test_isNumericToken_true():
    assert dataLoaders._fbIsNumericToken("3.14") is True


def test_isNumericToken_false():
    assert dataLoaders._fbIsNumericToken("foo") is False


def test_filterDataLines_strips_blanks_and_comments():
    listLines = ["# header\n", "  \n", "data\n", "# c\n", "more\n"]
    listResult = dataLoaders._flistFilterDataLines(listLines)
    assert listResult == ["data\n", "more\n"]


def test_splitHeaderAndData_detects_header():
    listResult = dataLoaders._ftSplitHeaderAndData(
        ["name value\n", "a 1\n", "b 2\n"],
    )
    assert listResult[0] == "name value\n"
    assert len(listResult[1]) == 2


def test_splitHeaderAndData_all_numeric_no_header():
    listResult = dataLoaders._ftSplitHeaderAndData(
        ["1 2\n", "3 4\n"],
    )
    assert listResult[0] == ""
    assert len(listResult[1]) == 2


def test_splitHeaderAndData_empty():
    assert dataLoaders._ftSplitHeaderAndData([]) == ("", [])


# ----------------------------------------------------------------------
# Format-specific loaders — write a real file then load
# ----------------------------------------------------------------------


def test_loader_npy_index(tmp_path):
    np.save(str(tmp_path / "data.npy"), np.array([10.0, 20.0, 30.0]))
    assert fLoadValue("data.npy", "index:0", str(tmp_path)) == 10.0


def test_loader_npy_aggregate(tmp_path):
    np.save(str(tmp_path / "data.npy"), np.array([2.0, 4.0, 6.0]))
    assert fLoadValue("data.npy", "index:mean", str(tmp_path)) == 4.0


def test_loader_npy_corrupt_raises(tmp_path):
    (tmp_path / "bad.npy").write_bytes(b"not a numpy file")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.npy", "index:0", str(tmp_path))


def test_loader_npz_named_key(tmp_path):
    np.savez(
        str(tmp_path / "a.npz"),
        daTemp=np.array([100.0, 200.0, 300.0]),
    )
    fResult = fLoadValue(
        "a.npz", "key:daTemp,index:2", str(tmp_path),
    )
    assert fResult == 300.0


def test_loader_npz_default_first_key(tmp_path):
    np.savez(
        str(tmp_path / "a.npz"), onlyArr=np.array([5.0, 6.0]),
    )
    fResult = fLoadValue("a.npz", "index:0", str(tmp_path))
    assert fResult == 5.0


def test_loader_npz_corrupt_raises(tmp_path):
    (tmp_path / "bad.npz").write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.npz", "index:0", str(tmp_path))


def test_loader_json_scalar(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps({"fMass": 5.972e24}))
    fResult = fLoadValue("d.json", "key:fMass", str(tmp_path))
    assert fResult == 5.972e24


def test_loader_json_nested_key(tmp_path):
    (tmp_path / "d.json").write_text(
        json.dumps({"outer": {"inner": 3.0}}),
    )
    fResult = fLoadValue("d.json", "key:outer.inner", str(tmp_path))
    assert fResult == 3.0


def test_loader_json_corrupt_raises(tmp_path):
    (tmp_path / "bad.json").write_text("{not valid json")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.json", "key:x", str(tmp_path))


def test_loader_json_missing_key_raises(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps({"a": 1}))
    with pytest.raises(ValueError, match="Failed to access"):
        fLoadValue("d.json", "key:missing", str(tmp_path))


def test_loader_csv_dictreader(tmp_path):
    sPath = tmp_path / "r.csv"
    sPath.write_text("time,flux\n0,1.0\n1,2.5\n2,5.0\n")
    fResult = fLoadValue(
        "r.csv", "column:flux,index:-1", str(tmp_path),
    )
    assert fResult == 5.0


def test_loader_csv_aggregate(tmp_path):
    sPath = tmp_path / "r.csv"
    sPath.write_text("x,y\n1,2\n3,4\n5,6\n")
    fResult = fLoadValue(
        "r.csv", "column:y,index:mean", str(tmp_path),
    )
    assert fResult == 4.0


def test_loader_csv_missing_column_raises(tmp_path):
    (tmp_path / "r.csv").write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="Failed to access"):
        fLoadValue("r.csv", "column:missing,index:0", str(tmp_path))


def test_loader_whitespace_headerless(tmp_path):
    sPath = tmp_path / "data.dat"
    sPath.write_text("1.0 2.0 3.0\n4.0 5.0 6.0\n")
    fResult = fLoadValue("data.dat", "index:-1,0", str(tmp_path))
    assert fResult == 4.0


def test_loader_whitespace_with_header(tmp_path):
    sPath = tmp_path / "data.dat"
    sPath.write_text("time flux\n0 1.5\n1 2.5\n")
    fResult = fLoadValue(
        "data.dat", "column:flux,index:-1", str(tmp_path),
    )
    assert fResult == 2.5


def test_loader_whitespace_aggregate_mean(tmp_path):
    sPath = tmp_path / "data.dat"
    sPath.write_text("time flux\n0 2.0\n1 4.0\n")
    fResult = fLoadValue(
        "data.dat", "column:flux,index:mean", str(tmp_path),
    )
    assert fResult == 3.0


def test_loader_whitespace_missing_column(tmp_path):
    sPath = tmp_path / "data.dat"
    sPath.write_text("time flux\n0 1.0\n")
    with pytest.raises(ValueError, match="not found"):
        fLoadValue(
            "data.dat", "column:missing,index:0", str(tmp_path),
        )


def test_loader_keyvalue(tmp_path):
    sPath = tmp_path / "params.txt"
    sPath.write_text(
        "# comment\nfMass = 5.972e24\nfRadius = 6.371e6\n",
    )
    fResult = fLoadValue(
        "params.txt", "key:fMass", str(tmp_path),
        sFormat="keyvalue",
    )
    assert fResult == 5.972e24


def test_loader_keyvalue_missing_key_raises(tmp_path):
    (tmp_path / "p.txt").write_text("a = 1\n")
    with pytest.raises(KeyError):
        fLoadValue(
            "p.txt", "key:missing", str(tmp_path),
            sFormat="keyvalue",
        )


def test_loader_jsonl_with_key(tmp_path):
    sPath = tmp_path / "events.jsonl"
    sPath.write_text(
        '{"flux": 1.5}\n{"flux": 2.5}\n{"flux": 3.5}\n',
    )
    fResult = fLoadValue(
        "events.jsonl", "key:flux,index:0", str(tmp_path),
    )
    assert fResult == 1.5


def test_loader_jsonl_aggregate(tmp_path):
    sPath = tmp_path / "events.jsonl"
    sPath.write_text('{"v":1}\n{"v":2}\n{"v":3}\n')
    fResult = fLoadValue(
        "events.jsonl", "key:v,index:mean", str(tmp_path),
    )
    assert fResult == 2.0


def test_loader_jsonl_corrupt_raises(tmp_path):
    (tmp_path / "bad.jsonl").write_text("not json\n")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.jsonl", "key:x", str(tmp_path))


def test_loader_fasta_first_length(tmp_path):
    sPath = tmp_path / "seqs.fasta"
    sPath.write_text(">gene1\nACGT\n>gene2\nACGTACGT\n")
    fResult = fLoadValue("seqs.fasta", "index:0", str(tmp_path))
    assert fResult == 4.0


def test_loader_fasta_aggregate_mean(tmp_path):
    sPath = tmp_path / "seqs.fasta"
    sPath.write_text(">a\nAA\n>b\nAAAA\n")
    fResult = fLoadValue("seqs.fasta", "index:mean", str(tmp_path))
    assert fResult == 3.0


def test_loader_fastq_length(tmp_path):
    sPath = tmp_path / "reads.fastq"
    sPath.write_text("@r1\nACGT\n+\nIIII\n@r2\nACGTAC\n+\nIIIIII\n")
    fResult = fLoadValue(
        "reads.fastq", "key:length,index:0", str(tmp_path),
    )
    assert fResult == 4.0


def test_loader_fastq_quality(tmp_path):
    sPath = tmp_path / "reads.fastq"
    sPath.write_text("@r1\nACGT\n+\nIIII\n")
    fResult = fLoadValue(
        "reads.fastq", "key:quality,index:0", str(tmp_path),
    )
    assert fResult == float(ord("I") - 33)


def test_loader_vcf(tmp_path):
    sPath = tmp_path / "v.vcf"
    sPath.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\t.\tA\tT\t30.0\tPASS\t.\n"
        "chr1\t200\t.\tG\tC\t45.0\tPASS\t.\n",
    )
    fResult = fLoadValue("v.vcf", "column:POS,index:0", str(tmp_path))
    assert fResult == 100.0


def test_loader_bed(tmp_path):
    sPath = tmp_path / "r.bed"
    sPath.write_text(
        "chr1\t100\t200\tgene1\t500\t+\n"
        "chr2\t300\t400\tgene2\t600\t-\n",
    )
    fResult = fLoadValue(
        "r.bed", "column:chromStart,index:0", str(tmp_path),
    )
    assert fResult == 100.0


def test_loader_gff(tmp_path):
    sPath = tmp_path / "a.gff"
    sPath.write_text(
        "chr1\tvaibify\tgene\t100\t500\t0.5\t+\t.\tID=g\n",
    )
    fResult = fLoadValue(
        "a.gff", "column:start,index:0", str(tmp_path),
    )
    assert fResult == 100.0


def test_loader_sam(tmp_path):
    sPath = tmp_path / "a.sam"
    sPath.write_text(
        "@HD\tVN:1.6\n"
        "read1\t0\tchr1\t100\t60\t4M\t*\t0\t0\tACGT\tIIII\n",
    )
    fResult = fLoadValue(
        "a.sam", "column:POS,index:0", str(tmp_path),
    )
    assert fResult == 100.0


def test_loader_syslog_line_count(tmp_path):
    sPath = tmp_path / "evt.log"
    sPath.write_text("a\nb\nc\n")
    fResult = fLoadValue("evt.log", "index:0", str(tmp_path))
    assert fResult == 3.0


def test_loader_cef_record_count(tmp_path):
    sPath = tmp_path / "a.cef"
    sPath.write_text(
        "CEF:0|v|p|1|1|a|5|\nCEF:0|v|p|1|2|a|3|\n",
    )
    fResult = fLoadValue("a.cef", "index:0", str(tmp_path))
    assert fResult == 2.0


def test_loader_fixedwidth_explicit_format(tmp_path):
    sPath = tmp_path / "table.dat"
    sPath.write_text("1.0 2.0\n3.0 4.0\n")
    fResult = fLoadValue(
        "table.dat", "index:-1", str(tmp_path),
        sFormat="fixedwidth",
    )
    assert fResult == 3.0


def test_loader_multitable_section(tmp_path):
    sPath = tmp_path / "m.dat"
    sPath.write_text("1.0 2.0\n3.0 4.0\n\n5.0 6.0\n7.0 8.0\n")
    fResult = fLoadValue(
        "m.dat", "section:1,index:0,0", str(tmp_path),
        sFormat="multitable",
    )
    assert fResult == 5.0


def test_loader_hdf5(tmp_path):
    h5py = pytest.importorskip("h5py")
    sPath = tmp_path / "a.h5"
    with h5py.File(str(sPath), "w") as fh:
        fh.create_dataset("tmp", data=[288.15, 290.0, 300.0])
    fResult = fLoadValue(
        "a.h5", "dataset:tmp,index:0", str(tmp_path),
    )
    assert abs(fResult - 288.15) < 1e-10


def test_loader_hdf5_missing_dataset_raises(tmp_path):
    h5py = pytest.importorskip("h5py")
    sPath = tmp_path / "a.h5"
    with h5py.File(str(sPath), "w") as fh:
        fh.create_dataset("x", data=[1.0])
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("a.h5", "dataset:missing,index:0", str(tmp_path))


def test_loader_excel(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    sPath = tmp_path / "a.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["time", "flux"])
    ws.append([0.0, 1.5])
    ws.append([1.0, 2.5])
    wb.save(str(sPath))
    fResult = fLoadValue(
        "a.xlsx", "column:flux,index:-1", str(tmp_path),
    )
    assert fResult == 2.5


def test_loader_fits(tmp_path):
    pytest.importorskip("astropy")
    from astropy.io import fits as fitsLib
    sPath = tmp_path / "a.fits"
    hdu = fitsLib.PrimaryHDU(data=np.array([1.0, 2.0, 3.0]))
    hdu.writeto(str(sPath))
    fResult = fLoadValue(
        "a.fits", "hdu:0,index:0", str(tmp_path),
    )
    assert fResult == 1.0


def test_loader_fits_aggregate(tmp_path):
    pytest.importorskip("astropy")
    from astropy.io import fits as fitsLib
    sPath = tmp_path / "a.fits"
    hdu = fitsLib.PrimaryHDU(data=np.array([2.0, 4.0, 6.0]))
    hdu.writeto(str(sPath))
    fResult = fLoadValue(
        "a.fits", "hdu:0,index:mean", str(tmp_path),
    )
    assert fResult == 4.0


def test_loader_fits_missing_raises(tmp_path):
    pytest.importorskip("astropy")
    (tmp_path / "bad.fits").write_bytes(b"not a fits file")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.fits", "hdu:0,index:0", str(tmp_path))


def test_loader_matlab(tmp_path):
    pytest.importorskip("scipy")
    from scipy.io import savemat
    sPath = tmp_path / "a.mat"
    savemat(str(sPath), {"daTemps": np.array([100.0, 200.0, 300.0])})
    fResult = fLoadValue(
        "a.mat", "key:daTemps,index:0", str(tmp_path),
    )
    assert fResult == 100.0


def test_loader_matlab_default_key(tmp_path):
    pytest.importorskip("scipy")
    from scipy.io import savemat
    sPath = tmp_path / "a.mat"
    savemat(str(sPath), {"x": np.array([7.0, 8.0])})
    fResult = fLoadValue("a.mat", "index:0", str(tmp_path))
    assert fResult == 7.0


def test_loader_parquet(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    import pyarrow as pa
    sPath = tmp_path / "a.parquet"
    table = pa.table({"flux": [1.5, 2.5, 3.5]})
    pq.write_table(table, str(sPath))
    fResult = fLoadValue(
        "a.parquet", "column:flux,index:-1", str(tmp_path),
    )
    assert fResult == 3.5


def test_loader_parquet_missing_column(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    import pyarrow as pa
    sPath = tmp_path / "a.parquet"
    pq.write_table(pa.table({"flux": [1.0]}), str(sPath))
    with pytest.raises(ValueError, match="Failed to access"):
        fLoadValue(
            "a.parquet", "column:missing,index:0", str(tmp_path),
        )


def test_loader_image(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    sPath = tmp_path / "img.png"
    Image.new("L", (3, 3), color=128).save(str(sPath))
    fResult = fLoadValue("img.png", "index:0", str(tmp_path))
    assert fResult == 128.0


def test_loader_votable(tmp_path):
    pytest.importorskip("astropy")
    from astropy.io.votable.tree import (
        VOTableFile, Resource, Table, Field,
    )
    sPath = tmp_path / "a.vot"
    votable = VOTableFile()
    resource = Resource()
    votable.resources.append(resource)
    table = Table(votable)
    resource.tables.append(table)
    table.fields.extend([Field(votable, name="flux", datatype="double")])
    table.create_arrays(3)
    table.array["flux"] = [1.0, 2.0, 3.0]
    votable.to_xml(str(sPath))
    fResult = fLoadValue(
        "a.vot", "column:flux,index:0", str(tmp_path),
    )
    assert fResult == 1.0


def test_loader_ipac(tmp_path):
    pytest.importorskip("astropy")
    from astropy.io import ascii as astropyAscii
    from astropy.table import Table as AstropyTable
    sPath = tmp_path / "a.ipac"
    table = AstropyTable({"flux": [1.0, 2.0, 3.0]})
    astropyAscii.write(table, str(sPath), format="ipac")
    fResult = fLoadValue(
        "a.ipac", "column:flux,index:0", str(tmp_path),
    )
    assert fResult == 1.0


def test_loader_safetensors(tmp_path):
    safetensors = pytest.importorskip("safetensors")
    from safetensors.numpy import save_file
    sPath = tmp_path / "a.safetensors"
    save_file({"daVals": np.array([10.0, 20.0, 30.0])}, str(sPath))
    fResult = fLoadValue(
        "a.safetensors", "key:daVals,index:0", str(tmp_path),
    )
    assert fResult == 10.0


def test_loader_safetensors_default_key(tmp_path):
    safetensors = pytest.importorskip("safetensors")
    from safetensors.numpy import save_file
    sPath = tmp_path / "a.safetensors"
    save_file({"v": np.array([5.0])}, str(sPath))
    fResult = fLoadValue(
        "a.safetensors", "index:0", str(tmp_path),
    )
    assert fResult == 5.0


def test_loader_spss_ipexception_is_wrapped(tmp_path):
    pytest.importorskip("pyreadstat")
    (tmp_path / "bad.sav").write_bytes(b"not a sav")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.sav", "column:x,index:0", str(tmp_path))


def test_loader_stata_error_is_wrapped(tmp_path):
    pytest.importorskip("pyreadstat")
    (tmp_path / "bad.dta").write_bytes(b"not a dta")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.dta", "column:x,index:0", str(tmp_path))


def test_loader_sas_error_is_wrapped(tmp_path):
    pytest.importorskip("pyreadstat")
    (tmp_path / "bad.sas7bdat").write_bytes(b"not sas")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue(
            "bad.sas7bdat", "column:x,index:0", str(tmp_path),
        )


def test_loader_rdata_error_is_wrapped(tmp_path):
    pytest.importorskip("pyreadr")
    (tmp_path / "bad.rds").write_bytes(b"not an rdata file")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.rds", "column:x,index:0", str(tmp_path))


def test_loader_fortran_error_is_wrapped(tmp_path):
    pytest.importorskip("scipy")
    (tmp_path / "empty.unf").write_bytes(b"")
    with pytest.raises(ValueError):
        fLoadValue("empty.unf", "index:0", str(tmp_path))


def test_loader_pcap_error_is_wrapped(tmp_path):
    pytest.importorskip("scapy")
    (tmp_path / "bad.pcap").write_bytes(b"not a pcap")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.pcap", "index:0", str(tmp_path))


def test_loader_vtk_error_is_wrapped(tmp_path, monkeypatch):
    """Mocked pyvista.read() — calling it for real here resets VTK's
    global locale to US-ASCII, which breaks subsequent tests that rely
    on UTF-8 file reads.
    """
    pytest.importorskip("pyvista")
    import pyvista as _pyvista

    def _fnRaise(sPath):
        raise RuntimeError("Unrecognized file type")

    monkeypatch.setattr(_pyvista, "read", _fnRaise)
    (tmp_path / "bad.vtk").write_bytes(b"not vtk")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.vtk", "key:x,index:0", str(tmp_path))


def test_loader_cgns_reads_hdf5_dataset(tmp_path):
    h5py = pytest.importorskip("h5py")
    sPath = tmp_path / "a.cgns"
    with h5py.File(str(sPath), "w") as fh:
        fh.create_dataset("pressure", data=[1.0, 2.0, 3.0])
    fResult = fLoadValue(
        "a.cgns", "dataset:pressure,index:0", str(tmp_path),
    )
    assert fResult == 1.0


def test_loader_cgns_missing_dataset(tmp_path):
    h5py = pytest.importorskip("h5py")
    sPath = tmp_path / "a.cgns"
    with h5py.File(str(sPath), "w") as fh:
        fh.create_dataset("x", data=[1.0])
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue(
            "a.cgns", "dataset:missing,index:0", str(tmp_path),
        )


def test_loader_tfrecord_error_is_wrapped(tmp_path):
    pytest.importorskip("tfrecord")
    (tmp_path / "bad.tfrecord").write_bytes(b"\x00")
    with pytest.raises(ValueError):
        fLoadValue("bad.tfrecord", "key:x,index:0", str(tmp_path))


def test_loader_bam_error_is_wrapped(tmp_path):
    pytest.importorskip("pysam")
    (tmp_path / "bad.bam").write_bytes(b"not bam")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.bam", "key:mapq,index:0", str(tmp_path))


# ----------------------------------------------------------------------
# fsReadLoaderSource — embedded source extraction
# ----------------------------------------------------------------------


def test_fsReadLoaderSource_has_markers_and_functions():
    sSource = fsReadLoaderSource()
    assert "def _fLoadValue" in sSource
    assert "_DICT_LOADERS" in sSource
    assert "# -- begin loader source" not in sSource
    assert "# -- end loader source" not in sSource


def test_fsReadLoaderSource_is_syntactically_valid():
    import ast
    sSource = fsReadLoaderSource()
    ast.parse(sSource)


# ----------------------------------------------------------------------
# Extra edge cases to lift coverage on common branches
# ----------------------------------------------------------------------


def test_extractTabularValue_no_header_uses_first_col():
    listRows = [["1.0", "2.0"], ["3.0", "4.0"]]
    fResult = dataLoaders._fExtractTabularValue(
        [], listRows, {"column": "x", "listIndices": [0]},
    )
    assert fResult == 1.0


def test_navigateJsonValue_list_indexing_via_key_string():
    fResult = dataLoaders._fNavigateJsonValue(
        {"daValues": [[1.0, 2.0], [3.0, 4.0]]},
        {"key": "daValues.0", "listIndices": [1]},
    )
    assert fResult == 2.0


def test_loader_keyvalue_skips_lines_without_equals(tmp_path):
    sPath = tmp_path / "params.txt"
    sPath.write_text(
        "# header\n\n"
        "no_equals_here\n"
        "fMass = 7.5\n",
    )
    fResult = fLoadValue(
        "params.txt", "key:fMass", str(tmp_path),
        sFormat="keyvalue",
    )
    assert fResult == 7.5


def test_loader_jsonl_records_are_bare_numbers(tmp_path):
    sPath = tmp_path / "events.jsonl"
    sPath.write_text("1.5\n2.5\n3.5\n")
    fResult = fLoadValue("events.jsonl", "index:0", str(tmp_path))
    assert fResult == 1.5


def test_loader_jsonl_missing_key_raises(tmp_path):
    sPath = tmp_path / "events.jsonl"
    sPath.write_text('{"a": 1}\n')
    with pytest.raises(ValueError, match="Failed to access"):
        fLoadValue(
            "events.jsonl", "key:missing,index:0", str(tmp_path),
        )


def test_loader_excel_load_error_wrapped(tmp_path):
    pytest.importorskip("openpyxl")
    (tmp_path / "bad.xlsx").write_bytes(b"not a real xlsx")
    with pytest.raises(ValueError, match="Failed to load"):
        fLoadValue("bad.xlsx", "column:a,index:0", str(tmp_path))


def test_loader_excel_missing_column_raises(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    sPath = tmp_path / "a.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["x", "y"])
    ws.append([1.0, 2.0])
    wb.save(str(sPath))
    with pytest.raises(ValueError, match="Failed to access"):
        fLoadValue(
            "a.xlsx", "column:missing,index:0", str(tmp_path),
        )


def test_loader_matlab_missing_key_raises(tmp_path):
    pytest.importorskip("scipy")
    from scipy.io import savemat
    sPath = tmp_path / "a.mat"
    savemat(str(sPath), {"only_one": np.array([1.0])})
    with pytest.raises(ValueError, match="Failed to access"):
        fLoadValue("a.mat", "key:missing,index:0", str(tmp_path))


def test_loader_bam_success_with_one_read(tmp_path):
    """A valid BAM file with one aligned read is introspected for MAPQ."""
    pysam = pytest.importorskip("pysam")
    sPath = tmp_path / "a.bam"
    dictHeader = {
        "HD": {"VN": "1.6"},
        "SQ": [{"LN": 1000, "SN": "chr1"}],
    }
    with pysam.AlignmentFile(
        str(sPath), "wb", header=dictHeader,
    ) as fh:
        read = pysam.AlignedSegment(fh.header)
        read.query_name = "r1"
        read.query_sequence = "ACGT"
        read.flag = 0
        read.reference_id = 0
        read.reference_start = 0
        read.mapping_quality = 42
        read.cigar = [(0, 4)]
        read.query_qualities = pysam.qualitystring_to_array("IIII")
        fh.write(read)
    fResult = fLoadValue(
        "a.bam", "key:mapq,index:0", str(tmp_path),
    )
    assert fResult == 42.0


# ----------------------------------------------------------------------
# _fApplyAggregate: std and percentile branches (lines 160-166)
# ----------------------------------------------------------------------


def test_fApplyAggregate_std_uses_sample_std():
    """Line 160-161: std branch returns ddof=1 standard deviation."""
    daValues = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    fResult = dataLoaders._fApplyAggregate(daValues, "std")
    assert abs(fResult - float(daValues.std(ddof=1))) < 1e-12


def test_fApplyAggregate_percentile_branches(tmp_path):
    """Lines 162-165: percentile aggregates dispatch to np.percentile."""
    daValues = np.arange(101, dtype=float)
    for sName, fExpected in [
        ("p5", 5.0), ("p25", 25.0),
        ("p50", 50.0), ("p75", 75.0), ("p95", 95.0),
    ]:
        fActual = dataLoaders._fApplyAggregate(daValues, sName)
        assert abs(fActual - fExpected) < 1e-9


def test_fApplyAggregate_unknown_raises():
    """Line 166: an unknown aggregate name raises ValueError."""
    daValues = np.array([1.0, 2.0])
    with pytest.raises(ValueError, match="Unknown aggregate"):
        dataLoaders._fApplyAggregate(daValues, "kurtosis")


# ----------------------------------------------------------------------
# JSON loader: re-parse failure on doubly-serialised junk (lines 340-341)
# ----------------------------------------------------------------------


def test_fLoadJsonValue_raises_when_doubly_serialised_inner_invalid(
    tmp_path,
):
    """A top-level JSON string whose inner content is not JSON raises."""
    sPath = str(tmp_path / "bad.json")
    with open(sPath, "w", encoding="utf-8") as fh:
        # The outer string parses but the inner re-parse fails.
        json.dump("this is not nested json", fh)
    with pytest.raises(ValueError, match="re-parse"):
        fLoadValue("bad.json", "key:x", str(tmp_path))


# ----------------------------------------------------------------------
# CSV loader: malformed file raises csv.Error → ValueError (363-364)
# ----------------------------------------------------------------------


def test_fLoadCsvValue_raises_value_error_when_csv_unreadable(tmp_path):
    """An OSError during open should be raised as ValueError after wrapping.

    A directory-as-file triggers IsADirectoryError, which the loader
    will not catch. csv.Error is hard to provoke without nul bytes; we
    simulate that path through monkeypatching csv.DictReader instead.
    """
    import csv
    import unittest.mock as _mock
    sPath = tmp_path / "readme.csv"
    sPath.write_text("a,b\n1,2\n", encoding="utf-8")

    def fnReaderRaises(*args, **kwargs):
        raise csv.Error("badly formed csv")

    with _mock.patch("csv.DictReader", side_effect=fnReaderRaises):
        with pytest.raises(ValueError, match="csv"):
            fLoadValue("readme.csv", "column:a,index:0", str(tmp_path))


# ----------------------------------------------------------------------
# Whitespace loader: OSError on read raises ValueError (lines 403-404)
# ----------------------------------------------------------------------


def test_fLoadWhitespaceValue_raises_value_error_on_oserror(tmp_path):
    """An OSError while reading the whitespace file becomes ValueError."""
    sPath = tmp_path / "ws.dat"
    sPath.write_text("1 2 3\n4 5 6\n", encoding="utf-8")
    import unittest.mock as _mock

    fOriginalOpen = open

    def fnRaisingOpen(sFile, *args, **kwargs):
        if str(sFile).endswith("ws.dat") and "encoding" in kwargs:
            raise OSError("simulated read failure")
        return fOriginalOpen(sFile, *args, **kwargs)

    with _mock.patch("builtins.open", side_effect=fnRaisingOpen):
        with pytest.raises(ValueError, match="whitespace"):
            fLoadValue("ws.dat", "column:c1,index:0", str(tmp_path))


# ----------------------------------------------------------------------
# Fixed-width loader: empty file raises (line 699)
# ----------------------------------------------------------------------


def test_fLoadFixedwidthValue_raises_on_empty_file(tmp_path):
    """An empty fixed-width file raises ValueError immediately."""
    sPath = tmp_path / "empty.fwf"
    sPath.write_text("\n  \n", encoding="utf-8")
    with pytest.raises(ValueError, match="Empty fixed-width"):
        dataLoaders._fLoadFixedwidthValue(str(sPath), {})


# ----------------------------------------------------------------------
# Multitable loader: aggregate branch (lines 727-728)
# ----------------------------------------------------------------------


def test_fLoadMultitableValue_aggregate_branch(tmp_path):
    """sAggregate over a multi-table column dispatches to _fApplyAggregate."""
    sPath = tmp_path / "tables.txt"
    sPath.write_text(
        "name value\nalpha 1\nbeta 2\ngamma 3\n",
        encoding="utf-8",
    )
    fResult = dataLoaders._fLoadMultitableValue(
        str(sPath),
        {"iSection": 0, "column": "value", "sAggregate": "mean"},
    )
    assert abs(fResult - 2.0) < 1e-9


# ----------------------------------------------------------------------
# Excel aggregate branch (lines 479-480)
# ----------------------------------------------------------------------


def test_fLoadExcelValue_aggregate_branch(tmp_path):
    """sAggregate over an Excel column dispatches to _fApplyAggregate."""
    openpyxl = pytest.importorskip("openpyxl")
    sPath = tmp_path / "agg.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["name", "value"])
    ws.append(["a", 1.0])
    ws.append(["b", 3.0])
    ws.append(["c", 5.0])
    wb.save(str(sPath))
    fResult = fLoadValue(
        "agg.xlsx", "column:value,index:mean", str(tmp_path),
    )
    assert abs(fResult - 3.0) < 1e-9


# ----------------------------------------------------------------------
# FASTQ aggregate branch (line 620)
# ----------------------------------------------------------------------


def test_fLoadFastqValue_aggregate_branch(tmp_path):
    """sAggregate over fastq lengths dispatches to _fApplyAggregate."""
    sPath = tmp_path / "reads.fastq"
    sPath.write_text(
        "@r1\nACGT\n+\nIIII\n@r2\nACGTACGT\n+\nIIIIIIII\n",
        encoding="utf-8",
    )
    fResult = dataLoaders._fLoadFastqValue(
        str(sPath),
        {"key": "length", "sAggregate": "mean"},
    )
    assert abs(fResult - 6.0) < 1e-9
