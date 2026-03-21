"""Tests for vaibify.gui.figureServer."""

from vaibify.gui.figureServer import fbIsFigureFile, fsMimeTypeForFile


def test_fbIsFigureFile_pdf():
    assert fbIsFigureFile("plot.pdf") is True


def test_fbIsFigureFile_png():
    assert fbIsFigureFile("chart.png") is True


def test_fbIsFigureFile_svg():
    assert fbIsFigureFile("diagram.svg") is True


def test_fbIsFigureFile_jpg():
    assert fbIsFigureFile("photo.jpg") is True


def test_fbIsFigureFile_txt_rejected():
    assert fbIsFigureFile("notes.txt") is False


def test_fbIsFigureFile_case_insensitive():
    assert fbIsFigureFile("PLOT.PDF") is True


def test_fsMimeTypeForFile_pdf():
    assert fsMimeTypeForFile("a.pdf") == "application/pdf"


def test_fsMimeTypeForFile_png():
    assert fsMimeTypeForFile("a.png") == "image/png"


def test_fsMimeTypeForFile_svg():
    assert fsMimeTypeForFile("a.svg") == "image/svg+xml"


def test_fsMimeTypeForFile_json_unknown():
    assert fsMimeTypeForFile("a.json") == "application/octet-stream"


def test_fsMimeTypeForFile_no_extension():
    assert fsMimeTypeForFile("noext") == "application/octet-stream"
