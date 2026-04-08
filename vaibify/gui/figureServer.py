"""Fetch and serve figure files from Docker containers."""

__all__ = [
    "DICT_MIME_TYPES",
    "fbIsFigureFile",
    "fsMimeTypeForFile",
]

import os

DICT_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}


def fbIsFigureFile(sFilePath):
    """Return True if sFilePath has a recognized figure extension."""
    sExtension = os.path.splitext(sFilePath)[1].lower()
    return sExtension in DICT_MIME_TYPES


def fsMimeTypeForFile(sFilePath):
    """Return the MIME type for a figure file path."""
    sExtension = os.path.splitext(sFilePath)[1].lower()
    return DICT_MIME_TYPES.get(sExtension, "application/octet-stream")
