"""Project-context file policy: template, size cap, host-import jail.

The project context file — ``<projectRepo>/.vaibify/AGENTS.md`` — is
the researcher's standing instructions to the in-container agent,
versioned with the repository so it is part of the provenance record.
This module owns the pure policy around it: the canonical relative
path, the starter template, the content size cap, and the validation
jail for importing an existing context file from the host filesystem
(absolute, resolved inside the user's home, a regular file, within
the size cap — symlink escapes are resolved away by ``realpath``
before the containment check).

Everything here is FastAPI-free: helpers raise ``ValueError`` with a
user-facing message and the route layer maps them to HTTP statuses.
"""

__all__ = [
    "I_MAX_CONTEXT_CONTENT_BYTES",
    "S_CONTEXT_TEMPLATE",
    "S_PROJECT_CONTEXT_RELATIVE_PATH",
    "fsValidateHostImportFile",
    "fsReadHostImportFile",
]

import os

from vaibify.reproducibility.aiProvenanceStamp import (
    S_PROJECT_CONTEXT_RELATIVE_PATH,
)


I_MAX_CONTEXT_CONTENT_BYTES = 256 * 1024

S_CONTEXT_TEMPLATE = """# Project context

Standing instructions for the AI agent working on this project.
This file is versioned with the repository and is part of the
provenance record — keep it current.

## What this project is

(One paragraph: the scientific question, the approach, and what a
finished result looks like.)

## Data provenance

(Where the raw data comes from, how it was obtained, and any terms
attached to it.)

## Conventions

(Naming, units, coordinate systems, directory layout — whatever the
agent must follow to keep the project coherent.)

## What the agent must never touch

(The boundaries: calculations that must not change without
discussion, files that are read-only, decisions reserved for the
researcher.)
"""


def fsValidateHostImportFile(sHostPath):
    """Return the resolved host path or raise ``ValueError``.

    The jail mirrors the host-directory browser: the path must be
    absolute and its ``realpath`` must stay inside the user's home
    directory, so a symlink pointing outside the home is rejected
    after resolution, not trusted by its spelling.
    """
    if not sHostPath or not os.path.isabs(sHostPath):
        raise ValueError("Import path must be absolute.")
    sHome = os.path.expanduser("~")
    sResolved = os.path.realpath(sHostPath)
    if sResolved != sHome and not sResolved.startswith(sHome + os.sep):
        raise ValueError("Import path is outside the allowed root.")
    if not os.path.isfile(sResolved):
        raise ValueError("Import path is not a file.")
    if os.path.getsize(sResolved) > I_MAX_CONTEXT_CONTENT_BYTES:
        raise ValueError("Import file exceeds the 256 KiB size cap.")
    return sResolved


def fsReadHostImportFile(sHostPath):
    """Validate and read a host file for import; raise ``ValueError``."""
    sResolved = fsValidateHostImportFile(sHostPath)
    try:
        with open(sResolved, "r", encoding="utf-8") as fileHandle:
            return fileHandle.read()
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"Could not read import file: {error}")
