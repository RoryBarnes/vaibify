"""Locator for the data trees that ship inside the vaibify package.

Vaibify needs two directories of non-Python files at runtime: the
project templates that ``vaibify init`` copies, and the Docker build
context that ``vaibify build`` sends to the daemon. Both used to live
at the repository root and be reached with
``Path(__file__).resolve().parents[2]``, which is the repository root
only when vaibify is running from a checkout. From an installed wheel
that expression yields ``site-packages``, so ``vaibify init`` reported
"No templates found" and the Docker-context lookup resolved onto
``site-packages/docker`` -- the Docker SDK's own source directory,
which exists, so an ``is_dir()`` check passed and the wrong tree would
have been offered to the daemon.

Both trees therefore live inside the package now, and this module is
the only place that names them. ``importlib.resources`` resolves them
identically from a checkout, an editable install, and a wheel.

The trees are read-only: a wheel may be installed into a directory the
user cannot write, and writing into ``site-packages`` would leak one
project's build into the next one's. Callers that need to modify a
copy stage it elsewhere first -- see ``fpathStageBuildContext`` in
``vaibify.cli.commandBuild``.
"""

import shutil
from importlib import resources
from pathlib import Path


__all__ = [
    "fpathPackagedTree",
    "fnRequirePackagedTree",
    "fpathTemplatesRoot",
    "fpathContainerImageRoot",
    "fnCopyPackagedTree",
]


S_TEMPLATES_TREE = "templates"
S_CONTAINER_IMAGE_TREE = "containerImage"


def fpathPackagedTree(sTreeName):
    """Return the path a data tree occupies inside the package.

    Existence is not checked here, so module-level constants can be
    built from this without an import-time failure. Call
    ``fnRequirePackagedTree`` at the point of use.
    """
    return Path(str(resources.files("vaibify"))) / sTreeName


def fnRequirePackagedTree(pathTree, sTreeName):
    """Raise FileNotFoundError if a packaged data tree is absent.

    An absent tree means the distribution was built without its
    package data, not that the user did anything wrong, so the message
    says so -- otherwise a researcher hunts for a missing directory
    inside their own project.
    """
    if pathTree.is_dir():
        return
    raise FileNotFoundError(
        f"The vaibify installation is missing its '{sTreeName}' "
        f"directory (looked in '{pathTree}'). The installed package "
        f"was built without its data files; reinstall vaibify from a "
        f"release wheel, or from a source checkout with "
        f"'pip install -e .'."
    )


def fpathTemplatesRoot():
    """Return the verified path to the shipped project templates."""
    pathTree = fpathPackagedTree(S_TEMPLATES_TREE)
    fnRequirePackagedTree(pathTree, S_TEMPLATES_TREE)
    return pathTree


def fpathContainerImageRoot():
    """Return the verified path to the shipped Docker build context."""
    pathTree = fpathPackagedTree(S_CONTAINER_IMAGE_TREE)
    fnRequirePackagedTree(pathTree, S_CONTAINER_IMAGE_TREE)
    return pathTree


def fnCopyPackagedTree(pathSource, pathDestination):
    """Replace ``pathDestination`` with a fresh copy of ``pathSource``.

    The destination is removed first so a file deleted from the
    packaged tree cannot survive in a previously populated staging
    directory; a stale leftover in a Docker build context is invisible
    in the resulting image and very hard to trace back.
    """
    if pathDestination.exists():
        shutil.rmtree(str(pathDestination))
    pathDestination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(pathSource), str(pathDestination))
