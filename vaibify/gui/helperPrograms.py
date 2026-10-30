"""Which interpreter runs a vaibify-authored helper program.

Vaibify writes small programs and runs them through the connection: an
introspection pass over a step's outputs, a Zenodo archive build, an
Overleaf push, a LaTeX include list. They are vaibify's programs, not
the researcher's, and they import what vaibify depends on -- numpy
through the loaders, ``keyring``, ``requests``, and ``vaibify`` itself.

Inside a container that is ``python3``, which the image builds with
those dependencies installed. On the host it is emphatically NOT
``python3``: that is whatever the researcher's ``PATH`` resolves, and
on a Mac with a science stack in a virtual environment it is routinely
the system interpreter, with none of vaibify's dependencies in it. The
first host project ever to generate a test failed on exactly this --
``ModuleNotFoundError: No module named 'numpy'`` -- from an
interpreter the researcher does not use for anything.

The interpreter vaibify is *running on* has every one of those
dependencies by construction, because vaibify declares them and is
installed there. So on the host, a vaibify-authored program runs under
``sys.executable``.

**This is not the interpreter a STEP runs under.** A step's command is
the researcher's own text -- ``python3 makeNumbers.py`` -- naming the
environment they built for their science. That one is theirs and stays
theirs; nothing here touches it. The line is authorship: vaibify's
programs run on vaibify's interpreter, the researcher's run on
theirs.

**In a container the interpreter runs isolated** (``python3 -I``,
:mod:`vaibify.docker.execLane`): the user site, the working directory
and every ``PYTHON*`` variable belong to the in-container agent, and a
program whose answer the hub trusts must not import from them. The
image installs vaibify's dependencies in the system site-packages as
root, which ``-I`` leaves importable. The host interpreter is not
isolated: there the program and the researcher share one machine.

**Programs that import only the standard library spell the same
isolated interpreter in a container**, through the constant, so the
rule has one spelling to check.
"""

__all__ = ["fsResolveHelperInterpreter"]

import sys

from vaibify.config.registryManager import fbIsHostProject
from vaibify.docker.execLane import S_HUB_PYTHON_COMMAND

from .pipelineUtils import fsShellQuote


def fsResolveHelperInterpreter(sResourceId):
    """Return the shell word that invokes this resource's interpreter.

    Quoted, because on the host it is a filesystem path the researcher
    chose -- a virtual environment under a directory with a space in
    its name is ordinary, and the result is composed into ``bash -c``
    text.
    """
    if not fbIsHostProject(sResourceId):
        return S_HUB_PYTHON_COMMAND
    return fsShellQuote(sys.executable)
