"""The container-side introspection reads TFRecord features, as the host loader does.

``introspectionScript`` is an f-string executed inside containers, with
its own copy of the TFRecord reading (container scripts cannot import
from the host). That copy compared each RAW serialized record with
``isinstance(..., dict)``, which was never true, so a TFRecord file
produced no benchmarks at all and nothing said so. This runs the real
generated script over a real TFRecord file.
"""

import subprocess
import sys

import pytest

from vaibify.gui.introspectionScript import (
    _flistParseIntrospectionOutput,
    _fsBuildIntrospectionScript,
)


def _flistIntrospect(tmp_path, sFileName):
    sScript = _fsBuildIntrospectionScript([sFileName], str(tmp_path))
    processRun = subprocess.run(
        [sys.executable, "-c", sScript], capture_output=True, text=True,
        timeout=120,
    )
    assert processRun.returncode == 0, processRun.stderr
    return _flistParseIntrospectionOutput(processRun.stdout)


def _fnWriteExamples(pathFile):
    from tfrecord.writer import TFRecordWriter
    writerRecord = TFRecordWriter(str(pathFile))
    writerRecord.write({"x": (1.5, "float"), "label": (b"a", "byte")})
    writerRecord.write({"x": (2.5, "float"), "label": (b"b", "byte")})
    writerRecord.close()


def testTheContainerScriptBenchmarksTfrecordFeaturesByName(tmp_path):
    pytest.importorskip("tfrecord")
    _fnWriteExamples(tmp_path / "records.tfrecord")
    [dictReport] = _flistIntrospect(tmp_path, "records.tfrecord")
    assert dictReport["tShape"] == [2]
    assert set(dictReport["listColumnNames"]) == {"x", "label"}
    listBenchmarkNames = [
        str(dictBenchmark.get("sAccessPath", dictBenchmark))
        for dictBenchmark in dictReport["listBenchmarks"]
    ]
    assert any("key:x," in sName for sName in listBenchmarkNames), (
        "no benchmark was produced for the float feature")
    assert not any("key:label," in sName for sName in listBenchmarkNames)
