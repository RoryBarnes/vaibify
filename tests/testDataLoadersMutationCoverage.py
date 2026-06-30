"""Mutation-coverage tests for vaibify.gui.dataLoaders.

Each test closes a specific coverage hole found by mutation testing:
a surviving mutant in dataLoaders.py that the existing suite did not
kill. Every test asserts the CORRECT (unmutated) behaviour and is
constructed with DISTINCT first/last values so that an index
transposition or header/data misclassification is observable.
"""

import numpy as np
import pytest

from vaibify.gui import dataLoaders
from vaibify.gui.dataLoaders import fLoadValue

pytestmark = pytest.mark.falsification


# ----------------------------------------------------------------------
# Hole 1: _fExtractArrayValue default index must be [-1] (last), not [0].
# ----------------------------------------------------------------------


def test_extractArrayValue_default_index_is_last_element(tmp_path):
    """Kills: _fExtractArrayValue: default index [-1] -> [0]"""
    np.save(str(tmp_path / "data.npy"), np.array([10.0, 20.0, 30.0]))
    fResult = fLoadValue("data.npy", "", str(tmp_path))
    assert fResult == 30.0


# ----------------------------------------------------------------------
# Hole 2: _ftSplitHeaderAndData uses all() (mixed first line is header),
# not any() (which would misclassify it as data).
# ----------------------------------------------------------------------


def test_splitHeaderAndData_mixed_first_line_treated_as_header():
    """Kills: _ftSplitHeaderAndData: all(_fbIsNumericToken) -> any(_fbIsNumericToken)"""
    tResult = dataLoaders._ftSplitHeaderAndData(
        ["name 1\n", "a 2\n", "b 3\n"],
    )
    assert tResult == ("name 1\n", ["a 2\n", "b 3\n"])


# ----------------------------------------------------------------------
# Hole 3: _fLoadCsvNegativeRow returns dequeTail[0] (the requested
# negative row), not dequeTail[-1] (always the last row).
# ----------------------------------------------------------------------


def test_loadCsvNegativeRow_index_minus_two_is_second_to_last(tmp_path):
    """Kills: _fLoadCsvNegativeRow: dequeTail[0] -> dequeTail[-1]"""
    listLines = ["a,b\n"] + [f"{i},{i * 2}\n" for i in range(10)]
    (tmp_path / "t.csv").write_text("".join(listLines))
    fResult = fLoadValue("t.csv", "column:b,index:-2", str(tmp_path))
    assert fResult == 16.0


# ----------------------------------------------------------------------
# Hole 4: _fLoadCsvByRowIndex routes only iIndex < 0 to the tail path;
# index:0 must read the first data row, not crash.
# ----------------------------------------------------------------------


def test_loadCsvByRowIndex_index_zero_returns_first_row(tmp_path):
    """Kills: _fLoadCsvByRowIndex: iIndex < 0 -> iIndex <= 0"""
    (tmp_path / "r.csv").write_text("time,flux\n0,1.0\n1,2.5\n")
    fResult = fLoadValue("r.csv", "column:flux,index:0", str(tmp_path))
    assert fResult == 1.0


# ----------------------------------------------------------------------
# Hole 5: _fExtractHdf5Value adds np.prod(shape) for negative flat
# indices (iFlat += ...), so index:-1 on a 10x10 grid maps to 99.
# ----------------------------------------------------------------------


def test_extractHdf5Value_negative_flat_index_maps_to_last(tmp_path):
    """Kills: _fExtractHdf5Value: iFlat += np.prod(tShape) -> iFlat -= np.prod(tShape)"""
    h5py = pytest.importorskip("h5py")
    sPath = tmp_path / "grid.h5"
    with h5py.File(str(sPath), "w") as fileHdf5:
        fileHdf5.create_dataset("g", data=np.arange(100).reshape(10, 10))
    fResult = fLoadValue("grid.h5", "dataset:g,index:-1", str(tmp_path))
    assert fResult == 99.0


# ----------------------------------------------------------------------
# Hole 6: _fLoadFitsValue selects listIndices[1] when len > 1, so a
# two-component index:0,2 reads element 2 of the flattened HDU data.
# ----------------------------------------------------------------------


def test_loadFitsValue_two_component_index_selects_second(tmp_path):
    """Kills: _fLoadFitsValue: len(listIndices) > 1 -> > 2"""
    pytest.importorskip("astropy")
    from astropy.io import fits as fitsLib
    sPath = tmp_path / "a.fits"
    fitsLib.PrimaryHDU(
        data=np.array([1.0, 2.0, 3.0, 4.0]),
    ).writeto(str(sPath))
    fResult = fLoadValue("a.fits", "hdu:0,index:0,2", str(tmp_path))
    assert fResult == 3.0
