"""Tests for pure functions in vaibify.gui.testGenerator."""

from vaibify.gui.testGenerator import (
    fsParseGeneratedCode,
    fsTestFilePath,
    fsBuildPrompt,
)


def test_fsParseGeneratedCode_strips_fences():
    sInput = "```python\nimport pytest\n```"
    assert fsParseGeneratedCode(sInput) == "import pytest"


def test_fsParseGeneratedCode_no_fences():
    sInput = "import pytest\ndef test_foo(): pass"
    assert fsParseGeneratedCode(sInput) == sInput


def test_fsParseGeneratedCode_triple_backtick_only():
    sInput = "```\nimport os\n```"
    assert fsParseGeneratedCode(sInput) == "import os"


def test_fsTestFilePath_index_zero():
    sPath = fsTestFilePath("/workspace/dir", 0)
    assert "test_step01" in sPath
    assert sPath.endswith(".py")


def test_fsTestFilePath_index_nine():
    sPath = fsTestFilePath("/workspace/dir", 9)
    assert "test_step10" in sPath


def test_fsBuildPrompt_includes_directory():
    dictStep = {
        "saDataCommands": ["python analysis.py"],
        "saDataFiles": ["data.npy"],
    }
    sPrompt = fsBuildPrompt(
        "/workspace/dir", dictStep,
        "script content here", "",
    )
    assert "/workspace/dir" in sPrompt


# -----------------------------------------------------------------------
# Path constructors
# -----------------------------------------------------------------------


def test_fsIntegrityTestPath():
    from vaibify.gui.testGenerator import fsIntegrityTestPath
    assert fsIntegrityTestPath("/work/step01") == "/work/step01/tests/test_integrity.py"


def test_fsQualitativeTestPath():
    from vaibify.gui.testGenerator import fsQualitativeTestPath
    assert fsQualitativeTestPath("/work/step01") == "/work/step01/tests/test_qualitative.py"


def test_fsQuantitativeTestPath():
    from vaibify.gui.testGenerator import fsQuantitativeTestPath
    assert fsQuantitativeTestPath("/work/step01") == "/work/step01/tests/test_quantitative.py"


def test_fsQuantitativeStandardsPath():
    from vaibify.gui.testGenerator import fsQuantitativeStandardsPath
    assert fsQuantitativeStandardsPath("/work/step01") == "/work/step01/tests/quantitative_standards.json"


# -----------------------------------------------------------------------
# JSON parser
# -----------------------------------------------------------------------


def test_fdictParseQuantitativeJson_valid():
    from vaibify.gui.testGenerator import fdictParseQuantitativeJson
    sInput = '{"listStandards": [{"sName": "fTemp", "fValue": 288.15}]}'
    dictResult = fdictParseQuantitativeJson(sInput)
    assert dictResult["listStandards"][0]["sName"] == "fTemp"
    assert dictResult["listStandards"][0]["fValue"] == 288.15


def test_fdictParseQuantitativeJson_with_fences():
    from vaibify.gui.testGenerator import fdictParseQuantitativeJson
    sInput = '```json\n{"listStandards": [{"sName": "fFlux", "fValue": 1361.0}]}\n```'
    dictResult = fdictParseQuantitativeJson(sInput)
    assert dictResult["listStandards"][0]["fValue"] == 1361.0


def test_fdictParseQuantitativeJson_empty_list():
    from vaibify.gui.testGenerator import fdictParseQuantitativeJson
    sInput = '{"listStandards": []}'
    dictResult = fdictParseQuantitativeJson(sInput)
    assert dictResult["listStandards"] == []


# -----------------------------------------------------------------------
# Quantitative test template
# -----------------------------------------------------------------------


def test_fsBuildQuantitativeTestCode():
    from vaibify.gui.testGenerator import fsBuildQuantitativeTestCode
    sCode = fsBuildQuantitativeTestCode()
    assert "import numpy as np" in sCode
    assert "import pytest" in sCode
    assert "np.allclose" in sCode
    assert "quantitative_standards.json" in sCode
    assert "test_quantitative_benchmark" in sCode


def test_fsBuildQuantitativeTestCode_has_hdf5_loader():
    from vaibify.gui.testGenerator import fsBuildQuantitativeTestCode
    sCode = fsBuildQuantitativeTestCode()
    assert "_fLoadHdf5Value" in sCode
    assert "import h5py" in sCode


def test_fsBuildQuantitativeTestCode_has_whitespace_loader():
    from vaibify.gui.testGenerator import fsBuildQuantitativeTestCode
    sCode = fsBuildQuantitativeTestCode()
    assert "_fLoadWhitespaceValue" in sCode


def test_fsBuildQuantitativeTestCode_has_format_dispatch():
    from vaibify.gui.testGenerator import fsBuildQuantitativeTestCode
    sCode = fsBuildQuantitativeTestCode()
    assert "_fsInferFormat" in sCode
    assert "sFormat" in sCode
    assert "_DICT_FORMAT_MAP" in sCode


def test_fsBuildQuantitativeTestCode_has_aggregate_support():
    from vaibify.gui.testGenerator import fsBuildQuantitativeTestCode
    sCode = fsBuildQuantitativeTestCode()
    assert "sAggregate" in sCode
    assert '"mean"' in sCode
    assert '"min"' in sCode
    assert '"max"' in sCode


# -----------------------------------------------------------------------
# Prompt builders
# -----------------------------------------------------------------------


def test_fdictParseCombinedOutput():
    from vaibify.gui.testGenerator import fdictParseCombinedOutput
    sRaw = (
        "```INTEGRITY\nimport os\n```\n"
        "```QUALITATIVE\nimport pytest\n```\n"
        '```QUANTITATIVE\n{"listStandards": []}\n```'
    )
    dictSections = fdictParseCombinedOutput(sRaw)
    assert "INTEGRITY" in dictSections
    assert "QUALITATIVE" in dictSections
    assert "QUANTITATIVE" in dictSections


# -----------------------------------------------------------------------
# fbValidatePythonSyntax
# -----------------------------------------------------------------------


def test_fbValidatePythonSyntax_valid_code():
    from vaibify.gui.testGenerator import fbValidatePythonSyntax
    fbValidatePythonSyntax("import os\nprint(os.getcwd())")


def test_fbValidatePythonSyntax_empty_string():
    from vaibify.gui.testGenerator import fbValidatePythonSyntax
    fbValidatePythonSyntax("")


def test_fbValidatePythonSyntax_invalid_raises():
    import pytest as pt
    from vaibify.gui.testGenerator import fbValidatePythonSyntax
    with pt.raises(ValueError, match="syntax error"):
        fbValidatePythonSyntax("def foo(:\n    pass")


def test_fbValidatePythonSyntax_incomplete_raises():
    import pytest as pt
    from vaibify.gui.testGenerator import fbValidatePythonSyntax
    with pt.raises(ValueError):
        fbValidatePythonSyntax("if True")


# -----------------------------------------------------------------------
# fsRepairMissingImports
# -----------------------------------------------------------------------


def test_fsRepairMissingImports_adds_os():
    from vaibify.gui.testGenerator import fsRepairMissingImports
    sCode = "sPath = os.path.join('a', 'b')"
    sResult = fsRepairMissingImports(sCode)
    assert "import os" in sResult
    assert sResult.index("import os") < sResult.index("os.path")


def test_fsRepairMissingImports_adds_json():
    from vaibify.gui.testGenerator import fsRepairMissingImports
    sCode = "dictData = json.loads('{}')"
    sResult = fsRepairMissingImports(sCode)
    assert "import json" in sResult


def test_fsRepairMissingImports_adds_csv():
    from vaibify.gui.testGenerator import fsRepairMissingImports
    sCode = "reader = csv.DictReader(fh)"
    sResult = fsRepairMissingImports(sCode)
    assert "import csv" in sResult


def test_fsRepairMissingImports_adds_multiple():
    from vaibify.gui.testGenerator import fsRepairMissingImports
    sCode = "os.path.exists('x')\njson.dumps({})"
    sResult = fsRepairMissingImports(sCode)
    assert "import os" in sResult
    assert "import json" in sResult


def test_fsRepairMissingImports_skips_already_imported():
    from vaibify.gui.testGenerator import fsRepairMissingImports
    sCode = "import os\nos.path.exists('x')"
    sResult = fsRepairMissingImports(sCode)
    assert sResult == sCode


def test_fsRepairMissingImports_returns_unchanged_on_syntax_error():
    from vaibify.gui.testGenerator import fsRepairMissingImports
    sCode = "def foo(:\n    os.path.exists('x')"
    sResult = fsRepairMissingImports(sCode)
    assert sResult == sCode


def test_fsRepairMissingImports_no_repairs_needed():
    from vaibify.gui.testGenerator import fsRepairMissingImports
    sCode = "x = 1 + 2"
    sResult = fsRepairMissingImports(sCode)
    assert sResult == sCode
