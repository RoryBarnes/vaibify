"""Format branches of vaibify.gui.dataLoaders driven by real files.

Every fixture here is a tiny synthetic file written by the format's own
library into tmp_path, so the loader's parse path runs for real. The
only stub is an optional dependency made unimportable through
``sys.modules``, which is the environment boundary a container without
that package presents.
"""

import locale
import sys

import numpy as np
import pytest

from vaibify.gui.dataLoaders import ffLoadValue


TUPLE_LOCALE_CATEGORIES = (
    locale.LC_CTYPE, locale.LC_COLLATE, locale.LC_TIME,
    locale.LC_MONETARY, locale.LC_NUMERIC,
)


@pytest.fixture(autouse=True)
def fnRestoreProcessLocale():
    """Undo the process-wide locale reset some format libraries perform.

    VTK's reader and writer set LC_CTYPE to "C", after which every
    later ``open()`` without an explicit encoding decodes as ASCII in
    this worker process -- measured to fail unrelated tests that read
    UTF-8 files. The loader is exercised here, not the reset.
    """
    listSavedLocales = [
        (iCategory, locale.setlocale(iCategory))
        for iCategory in TUPLE_LOCALE_CATEGORIES
    ]
    yield
    for iCategory, sSavedLocale in listSavedLocales:
        locale.setlocale(iCategory, sSavedLocale)


def fsWriteBytes(pathDirectory, sFileName, baContent):
    """Write raw bytes to a file in pathDirectory and return its name."""
    (pathDirectory / sFileName).write_bytes(baContent)
    return sFileName


LIST_MISSING_DEPENDENCY_CASES = [
    ("sheet.xlsx", "openpyxl", "openpyxl is required"),
    ("image.fits", "astropy.io", "astropy is required to load FITS"),
    ("matrix.mat", "scipy.io", "scipy is required to load MATLAB"),
    ("table.parquet", "pyarrow.parquet", "pyarrow is required"),
    ("picture.png", "PIL", "Pillow is required"),
    ("reads.bam", "pysam", "pysam is required"),
    ("records.unf", "scipy.io", "scipy is required to load FORTRAN"),
    ("survey.sav", "pyreadstat", "pyreadstat is required to load SPSS"),
    ("survey.dta", "pyreadstat", "pyreadstat is required to load Stata"),
    ("survey.sas7bdat", "pyreadstat", "pyreadstat is required to load SAS"),
    ("frame.rds", "pyreadr", "pyreadr is required"),
    ("catalog.vot", "astropy.io.votable", "astropy is required to load VOTable"),
    ("catalog.ipac", "astropy.io", "astropy is required to load IPAC"),
    ("capture.pcap", "scapy.all", "scapy is required"),
    ("mesh.vtk", "pyvista", "pyvista is required"),
    ("weights.safetensors", "safetensors", "safetensors is required"),
    ("records.tfrecord", "tfrecord.reader", "tfrecord is required"),
]


@pytest.mark.parametrize(
    "sFileName,sModuleName,sExpectedMessage",
    LIST_MISSING_DEPENDENCY_CASES,
)
def testMissingOptionalDependencyNamesThePackage(
    tmp_path, monkeypatch, sFileName, sModuleName, sExpectedMessage,
):
    """A loader whose library is absent says which package to install."""
    fsWriteBytes(tmp_path, sFileName, b"placeholder")
    monkeypatch.setitem(sys.modules, sModuleName, None)
    with pytest.raises(ImportError, match=sExpectedMessage):
        ffLoadValue(sFileName, "", str(tmp_path))


def testHdf5ScalarDatasetReturnsItsValue(tmp_path):
    """A zero-dimensional HDF5 dataset is read without slicing."""
    h5py = pytest.importorskip("h5py")
    with h5py.File(tmp_path / "scalars.h5", "w") as fileHdf5:
        fileHdf5["scalarValue"] = np.float64(6.25)
    fValue = ffLoadValue("scalars.h5", "dataset:scalarValue", str(tmp_path))
    assert fValue == 6.25


def testTextFileWithKeyAccessDispatchesToKeyValueReport(tmp_path):
    """A .txt file addressed by key is read as a key = value report."""
    (tmp_path / "report.txt").write_text(
        "# header line\nalphaCount = 12.5\nno equals sign\nbetaCount=3\n",
    )
    assert ffLoadValue("report.txt", "key:betaCount", str(tmp_path)) == 3.0
    assert ffLoadValue("report.txt", "key:alphaCount", str(tmp_path)) == 12.5


def fnWriteFitsTable(pathFile):
    """Write a FITS file: an empty primary HDU and one binary table."""
    from astropy.io import fits
    from astropy.table import Table
    tableData = Table({"alpha": [1.0, 2.0, 3.0], "beta": [10.0, 20.0, 30.0]})
    listHdus = fits.HDUList([fits.PrimaryHDU(), fits.BinTableHDU(tableData)])
    listHdus.writeto(pathFile, overwrite=True)


def testFitsTableColumnIsSelectedByName(tmp_path):
    """A column of a FITS table HDU is read, indexed by the second index."""
    pytest.importorskip("astropy")
    fnWriteFitsTable(tmp_path / "table.fits")
    fValue = ffLoadValue(
        "table.fits", "hdu:1,column:beta,index:0,2", str(tmp_path),
    )
    assert fValue == 30.0


def testFitsHduWithoutDataRaisesNamingTheHdu(tmp_path):
    """An empty primary HDU is refused rather than read as zero."""
    pytest.importorskip("astropy")
    fnWriteFitsTable(tmp_path / "table.fits")
    with pytest.raises(ValueError, match="HDU 0 has no data"):
        ffLoadValue("table.fits", "hdu:0", str(tmp_path))


def testMatlabVersionSevenPointThreeIsRefusedWithPath(tmp_path):
    """An HDF5-based .mat file cannot be read by loadmat and says so."""
    pytest.importorskip("scipy")
    baHeader = b"MATLAB 7.3 MAT-file".ljust(124, b" ") + b"\x00\x02IM"
    fsWriteBytes(tmp_path, "matrix.mat", baHeader + b"\x00" * 400)
    with pytest.raises(ValueError, match="Failed to load .*matrix.mat as matlab"):
        ffLoadValue("matrix.mat", "", str(tmp_path))


def testMatlabMissingFileIsWrappedAsLoadFailure(tmp_path):
    """A missing .mat file is reported as a load failure naming the file."""
    pytest.importorskip("scipy")
    with pytest.raises(ValueError, match="absent.mat as matlab"):
        ffLoadValue("absent.mat", "", str(tmp_path))


@pytest.mark.parametrize(
    "sFileName,sFormatWord,sLibrary",
    [
        ("table.parquet", "parquet", "pyarrow"),
        ("picture.png", "image", "PIL"),
        ("weights.safetensors", "safetensors", "safetensors"),
        ("survey.sav", "spss", "pyreadstat"),
        ("survey.dta", "stata", "pyreadstat"),
        ("survey.sas7bdat", "sas", "pyreadstat"),
        ("frame.rds", "rdata", "pyreadr"),
        ("capture.pcap", "pcap", "scapy"),
        ("records.tfrecord", "tfrecord", "tfrecord"),
    ],
)
def testCorruptFileIsReportedAsLoadFailureOfItsFormat(
    tmp_path, sFileName, sFormatWord, sLibrary,
):
    """Garbage bytes surface as a ValueError naming the file and format."""
    pytest.importorskip(sLibrary)
    fsWriteBytes(tmp_path, sFileName, b"garbage bytes, not a real file")
    with pytest.raises(
        ValueError, match=f"Failed to load .*{sFileName} as {sFormatWord}",
    ):
        ffLoadValue(sFileName, "", str(tmp_path))


def fnWriteAstropyTable(pathFile, sFormat):
    """Write a two-column astropy table in the given format."""
    from astropy.table import Table
    tableData = Table({"alpha": [1.0, 2.0], "beta": [10.0, 20.0]})
    tableData.write(pathFile, format=sFormat, overwrite=True)


@pytest.mark.parametrize(
    "sFileName,sFormat,sFormatWord",
    [("catalog.vot", "votable", "votable"), ("catalog.ipac", "ipac", "ipac")],
)
def testAstropyTableMissingColumnIsAnAccessFailure(
    tmp_path, sFileName, sFormat, sFormatWord,
):
    """Asking for an absent column names the access, not the load."""
    pytest.importorskip("astropy")
    fnWriteAstropyTable(tmp_path / sFileName, sFormat)
    assert ffLoadValue(sFileName, "column:beta,index:-1", str(tmp_path)) == 20.0
    with pytest.raises(
        ValueError, match=f"Failed to access {sFormatWord} column",
    ):
        ffLoadValue(sFileName, "column:gamma", str(tmp_path))


def testVotableMalformedXmlIsALoadFailure(tmp_path):
    """Truncated XML is reported as a votable load failure."""
    pytest.importorskip("astropy")
    (tmp_path / "broken.vot").write_text("<notvot")
    with pytest.raises(ValueError, match="broken.vot as votable"):
        ffLoadValue("broken.vot", "", str(tmp_path))


def testIpacMalformedHeaderIsALoadFailure(tmp_path, monkeypatch):
    """Whatever astropy raises while reading is reported as an ipac failure.

    Which headers astropy rejects differs between its releases, so the
    reader is made to reject this one; the wrapping is what is pinned.
    """
    astropyAscii = pytest.importorskip("astropy.io.ascii")

    def fnReadThatRejectsTheHeader(*listArguments, **dictKeywords):
        raise ValueError("header row could not be parsed")

    monkeypatch.setattr(astropyAscii, "read", fnReadThatRejectsTheHeader)
    (tmp_path / "broken.ipac").write_text("|||\n garbage")
    with pytest.raises(ValueError, match="broken.ipac as ipac"):
        ffLoadValue("broken.ipac", "", str(tmp_path))


def testBamTemplateLengthAndMappingQualityAreRead(tmp_path):
    """The tlen key reads template lengths; the default reads MAPQ."""
    pysam = pytest.importorskip("pysam")
    dictHeader = {"HD": {"VN": "1.0"}, "SQ": [{"LN": 1000, "SN": "chrA"}]}
    with pysam.AlignmentFile(
        str(tmp_path / "reads.bam"), "wb", header=dictHeader,
    ) as fileOut:
        for iRead, (iQuality, iLength) in enumerate([(30, 150), (40, -200)]):
            alignedSegment = pysam.AlignedSegment()
            alignedSegment.query_name = f"read{iRead}"
            alignedSegment.query_sequence = "ACGT"
            alignedSegment.reference_id = 0
            alignedSegment.reference_start = 10 + iRead
            alignedSegment.mapping_quality = iQuality
            alignedSegment.cigar = ((0, 4),)
            alignedSegment.template_length = iLength
            alignedSegment.query_qualities = pysam.qualitystring_to_array("IIII")
            fileOut.write(alignedSegment)
    assert ffLoadValue("reads.bam", "key:tlen,index:-1", str(tmp_path)) == -200.0
    assert ffLoadValue("reads.bam", "index:mean", str(tmp_path)) == 35.0


def testFortranRecordsAreSelectedByKeyAsRecordNumber(tmp_path):
    """A numeric key picks the record; the index picks the element."""
    scipyIo = pytest.importorskip("scipy.io")
    fileFortran = scipyIo.FortranFile(str(tmp_path / "records.unf"), "w")
    fileFortran.write_record(np.array([1.0, 2.0, 3.0]))
    fileFortran.write_record(np.array([4.0, 5.0]))
    fileFortran.close()
    assert ffLoadValue("records.unf", "key:1,index:0", str(tmp_path)) == 4.0
    assert ffLoadValue("records.unf", "index:-1", str(tmp_path)) == 3.0


def testFortranMissingAndEmptyFilesAreRefused(tmp_path):
    """A missing file and a record-free file each raise a clear error."""
    pytest.importorskip("scipy.io")
    with pytest.raises(ValueError, match="absent.unf as fortran"):
        ffLoadValue("absent.unf", "", str(tmp_path))
    fsWriteBytes(tmp_path, "empty.unf", b"")
    with pytest.raises(ValueError, match="No records found"):
        ffLoadValue("empty.unf", "", str(tmp_path))


DICT_FRAME_COLUMNS = {"alpha": [1.5, 2.5], "beta": [3.0, 4.0]}


def testSpssAndStataFilesAreReadThroughTheDataframePath(tmp_path):
    """A written .sav and .dta round-trip a column value and aggregate."""
    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")
    dfData = pandas.DataFrame(DICT_FRAME_COLUMNS)
    pyreadstat.write_sav(dfData, str(tmp_path / "survey.sav"))
    pyreadstat.write_dta(dfData, str(tmp_path / "survey.dta"))
    assert ffLoadValue("survey.sav", "column:beta,index:0", str(tmp_path)) == 3.0
    assert ffLoadValue(
        "survey.dta", "column:alpha,index:mean", str(tmp_path),
    ) == 2.0


def testRdsFrameIsReadByColumn(tmp_path):
    """An .rds data frame is read through its single unnamed object."""
    pyreadr = pytest.importorskip("pyreadr")
    pandas = pytest.importorskip("pandas")
    pyreadr.write_rds(
        str(tmp_path / "frame.rds"), pandas.DataFrame(DICT_FRAME_COLUMNS),
    )
    assert ffLoadValue("frame.rds", "column:beta,index:-1", str(tmp_path)) == 4.0


def testPcapPacketLengthsAreTheValues(tmp_path):
    """Each packet contributes its byte length, in capture order."""
    pytest.importorskip("scapy")
    from scapy.all import Ether, IP, wrpcap
    wrpcap(str(tmp_path / "capture.pcap"), [Ether() / IP(), Ether() / IP() / b"xxxx"])
    fFirst = ffLoadValue("capture.pcap", "index:0", str(tmp_path))
    fLast = ffLoadValue("capture.pcap", "index:-1", str(tmp_path))
    assert fLast - fFirst == 4.0


def testVtkPointArrayIsReadByNameAndByDefault(tmp_path):
    """The first array is the default; a named array can be aggregated."""
    pyvista = pytest.importorskip("pyvista")
    meshData = pyvista.PolyData(
        np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
    )
    meshData.point_data["valueArray"] = np.array([7.0, 8.0, 9.0])
    meshData.save(str(tmp_path / "mesh.vtk"))
    assert ffLoadValue("mesh.vtk", "index:1", str(tmp_path)) == 8.0
    assert ffLoadValue(
        "mesh.vtk", "key:valueArray,index:max", str(tmp_path),
    ) == 9.0
    with pytest.raises(ValueError, match="Failed to access vtk array"):
        ffLoadValue("mesh.vtk", "key:absentArray", str(tmp_path))


def fnWriteTfrecord(pathFile, dictFeatures):
    """Write one TFRecord example holding dictFeatures."""
    from tfrecord.writer import TFRecordWriter
    writerRecord = TFRecordWriter(str(pathFile))
    writerRecord.write(dictFeatures)
    writerRecord.close()


def testTfrecordWithoutKeyCountsRecordBytes(tmp_path):
    """Without a key, each record contributes its serialized length."""
    pytest.importorskip("tfrecord")
    fnWriteTfrecord(tmp_path / "records.tfrecord", {"x": (b"abc", "byte")})
    fValue = ffLoadValue("records.tfrecord", "index:0", str(tmp_path))
    assert fValue > 0.0
    assert fValue == int(fValue)


def testTfrecordFeatureIsReadByKey(tmp_path):
    """A float feature addressed by key returns the stored value.

    The key names a FEATURE, which only a decoded example has: indexing
    the raw serialized record by name raised on every access.
    """
    pytest.importorskip("tfrecord")
    fnWriteTfrecord(tmp_path / "records.tfrecord", {"x": (1.5, "float")})
    assert ffLoadValue("records.tfrecord", "key:x,index:0", str(tmp_path)) == 1.5


def fnWriteTwoTfrecords(pathFile):
    from tfrecord.writer import TFRecordWriter
    writerRecord = TFRecordWriter(str(pathFile))
    writerRecord.write({"x": (1.5, "float"), "n": (7, "int"),
                        "v": ([1.0, 2.0, 3.0], "float")})
    writerRecord.write({"x": (2.5, "float"), "n": (9, "int"),
                        "v": ([4.0, 5.0, 6.0], "float")})
    writerRecord.close()


def testTfrecordScalarFeaturesAcrossRecordsAreAggregated(tmp_path):
    pytest.importorskip("tfrecord")
    fnWriteTwoTfrecords(tmp_path / "records.tfrecord")
    assert ffLoadValue(
        "records.tfrecord", "key:x,index:1", str(tmp_path)) == 2.5
    assert ffLoadValue(
        "records.tfrecord", "key:n,index:max", str(tmp_path)) == 9.0


def testTfrecordVectorFeaturesAreFlattenedAcrossRecords(tmp_path):
    pytest.importorskip("tfrecord")
    fnWriteTwoTfrecords(tmp_path / "records.tfrecord")
    assert ffLoadValue(
        "records.tfrecord", "key:v,index:4", str(tmp_path)) == 5.0
    assert ffLoadValue(
        "records.tfrecord", "key:v,index:max", str(tmp_path)) == 6.0


def testTfrecordMissingFeatureIsANamedError(tmp_path):
    pytest.importorskip("tfrecord")
    fnWriteTwoTfrecords(tmp_path / "records.tfrecord")
    with pytest.raises(ValueError, match="Failed to access tfrecord key"):
        ffLoadValue("records.tfrecord", "key:absent,index:0", str(tmp_path))


def testTfrecordByteFeatureIsNotANumber(tmp_path):
    pytest.importorskip("tfrecord")
    fnWriteTfrecord(tmp_path / "records.tfrecord", {"b": (b"abc", "byte")})
    with pytest.raises(ValueError, match="Failed to access tfrecord key"):
        ffLoadValue("records.tfrecord", "key:b,index:0", str(tmp_path))
