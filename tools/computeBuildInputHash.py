#!/usr/bin/env python3
"""Print a content hash over everything that can change the image.

Lane 2 pulls an immutable CI image by digest instead of rebuilding per
run. That is only sound if the tag is keyed on *every* input: a hash
over the Dockerfile and requirements alone would happily reuse a stale
image after an entrypoint or shipped-CLI change.

Two rules make the key honest.

**Hash the sources, never the generated copies.** ``vaibify build``
writes ``director.py``, ``overleafSync.py``, ``docs-staged/*`` and
friends into the build context. Since 2026-07-27 those land in a
per-project staging copy under ``~/.vaibify/build/``, not in the
repository, so they are not reachable from here at all -- and even
before that they were gitignored, absent on a fresh clone and stale on
a developer machine. Hashing an artifact that may not have been
regenerated reintroduces exactly the staleness the key exists to
prevent, so the generator's *inputs* are hashed instead.

**Read the input list from the generator.** ``T_STAGED_DOCS`` and
``T_CONTAINER_SCRIPT_SOURCES`` are imported from ``commandBuild``
rather than restated here. A second copy of that list is how a newly
staged file starts shipping without changing the image key.

    python tools/computeBuildInputHash.py
    python tools/computeBuildInputHash.py --list
"""

import argparse
import hashlib
import pathlib
import sys


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from vaibify.cli.commandBuild import (  # noqa: E402
    T_CONTAINER_SCRIPT_SOURCES,
    T_STAGED_DOCS,
)


# Tracked files in the build context. Globs, resolved at run time, so a
# new overlay or skill file is picked up without editing this list.
T_BUILD_CONTEXT_GLOBS = (
    "vaibify/containerImage/Dockerfile*",
    "vaibify/containerImage/entrypoint.sh",
    "vaibify/containerImage/checkIsolation.sh",
    "vaibify/containerImage/vaibifyDo.py",
    "vaibify/containerImage/craftGuide.md",
    "vaibify/containerImage/overlays/**/*",
    "vaibify/containerImage/skills/**/*",
)

# The code that assembles the build context. A change here can change
# the image even when no file above moves.
T_GENERATOR_SOURCES = (
    "vaibify/cli/commandBuild.py",
    "vaibify/docker/imageBuilder.py",
)


def flistBuildInputPaths():
    """Return every repo-relative build input, sorted and deduplicated."""
    setPaths = set()
    for sGlob in T_BUILD_CONTEXT_GLOBS:
        for pathMatch in REPO.glob(sGlob):
            if pathMatch.is_file():
                setPaths.add(pathMatch.relative_to(REPO).as_posix())
    for sRelative in T_GENERATOR_SOURCES:
        setPaths.add(sRelative)
    for sFileName in T_CONTAINER_SCRIPT_SOURCES:
        setPaths.add(f"vaibify/reproducibility/{sFileName}")
    # ``vaibify/gui/director.py`` was keyed here while the build staged
    # it. It is no longer staged (it could not start in the container),
    # so it is no longer a build input and keying it would make an
    # unrelated host-side edit invalidate every image.
    for sRelSource, _sDestName in T_STAGED_DOCS:
        setPaths.add(sRelSource)
    return sorted(setPaths)


def fsComputeBuildInputHash():
    """Return the hex digest over all build inputs.

    The path is hashed alongside the bytes, so moving a file changes
    the key even when its contents do not. A listed-but-absent file
    contributes a distinct marker rather than being skipped silently --
    a vanished input is a change.
    """
    hasher = hashlib.sha256()
    for sRelative in flistBuildInputPaths():
        hasher.update(sRelative.encode("utf-8"))
        pathInput = REPO / sRelative
        if not pathInput.is_file():
            hasher.update(b"\0<absent>")
            continue
        hasher.update(b"\0")
        hasher.update(pathInput.read_bytes())
    return hasher.hexdigest()


def main():
    """Print the hash, or the file list behind it."""
    parser = argparse.ArgumentParser(
        description="Content hash over every Docker build input.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Print the input paths instead of the hash.",
    )
    args = parser.parse_args()
    if args.list:
        print("\n".join(flistBuildInputPaths()))
        return
    print(fsComputeBuildInputHash())


if __name__ == "__main__":
    main()
