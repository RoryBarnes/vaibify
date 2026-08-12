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

**Programs that import only the standard library keep ``python3`` in
both modes**, deliberately. A marker migration or a glob expansion
works under any interpreter, and routing them through here would
widen the change without fixing anything.
"""

__all__ = ["fsResolveHelperInterpreter"]

import sys

from vaibify.config.registryManager import fbIsHostProject

from .pipelineUtils import fsShellQuote


def fsResolveHelperInterpreter(sResourceId):
    """Return the shell word that invokes this resource's interpreter.

    Quoted, because on the host it is a filesystem path the researcher
    chose -- a virtual environment under a directory with a space in
    its name is ordinary, and the result is composed into ``bash -c``
    text.
    """
    if not fbIsHostProject(sResourceId):
        return "python3"
    return fsShellQuote(sys.executable)
