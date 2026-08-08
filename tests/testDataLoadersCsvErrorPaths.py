"""Behaviour tests for the CSV loader's aggregate/index/error paths.

These exercise the streaming CSV loaders directly: column-aggregate,
positive and negative row indexing, the out-of-range and
missing/empty-column errors, and the empty-file header case.
"""

import pytest

from vaibify.gui import dataLoaders as dl


@pytest.fixture
def pathCsv(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("x,y\n1,10\n2,20\n3,30\n")
    return str(p)


# --- _fiColumnIndexOrRaise ---

def test_column_index_found(pathCsv):
    assert dl._fiColumnIndexOrRaise(["x", "y"], "y") == 1


def test_column_index_empty_name_raises():
    with pytest.raises(KeyError):
        dl._fiColumnIndexOrRaise(["x", "y"], "")


def test_column_index_missing_raises():
    with pytest.raises(KeyError):
        dl._fiColumnIndexOrRaise(["x", "y"], "z")


# --- aggregate ---

def test_csv_aggregate_mean(pathCsv):
    assert dl._ffLoadCsvAggregate(pathCsv, "y", "mean") == 20.0


def test_csv_aggregate_max(pathCsv):
    assert dl._ffLoadCsvAggregate(pathCsv, "y", "max") == 30.0


# --- positive row index ---

def test_csv_by_row_index_returns_cell(pathCsv):
    assert dl._ffLoadCsvByRowIndex(pathCsv, "y", 0) == 10.0
    assert dl._ffLoadCsvByRowIndex(pathCsv, "x", 2) == 3.0


def test_csv_by_row_index_out_of_range_raises(pathCsv):
    with pytest.raises(IndexError):
        dl._ffLoadCsvByRowIndex(pathCsv, "y", 99)


# --- negative row index ---

def test_csv_negative_row_returns_from_tail(pathCsv):
    assert dl._ffLoadCsvByRowIndex(pathCsv, "y", -1) == 30.0
    assert dl._ffLoadCsvByRowIndex(pathCsv, "y", -3) == 10.0


def test_csv_negative_row_out_of_range_raises(pathCsv):
    with pytest.raises(IndexError):
        dl._ffLoadCsvByRowIndex(pathCsv, "y", -99)


# --- empty file header handling ---

def test_reader_open_on_empty_file_yields_no_headers(tmp_path):
    pEmpty = tmp_path / "empty.csv"
    pEmpty.write_text("")
    reader, fileHandle, listHeaders = dl._ftOpenCsvReader(str(pEmpty))
    try:
        assert listHeaders == []
    finally:
        fileHandle.close()
