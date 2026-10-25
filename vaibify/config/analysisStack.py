"""The Python analysis stack every vaibify image carries.

Python is vaibify's default analysis language, and the tests vaibify
generates import numpy and run under pytest. An image used to install
only the packages a project named in ``vaibify.yml``, and a new project
names none, so the first tests vaibify wrote for it failed before they
ran (alpha tester, 2026-10-24). Every image now installs this stack
whether or not a project names it.

The stack joins the project's own ``pythonPackages`` in the image's
requirements list rather than riding a separate pip step, so the image
list, the repository's mirror of it and the Level 3 lock record these
packages, and pin their versions, exactly as they record a declared
one. A project that names one keeps its own requirement line, and with
it its own version bound; the stack adds only the names it does not.
"""

from vaibify.reproducibility.declaredPackages import (
    flistParseRequirementNames,
)


__all__ = [
    "T_ANALYSIS_STACK_PACKAGES",
    "flistImagePythonPackages",
]


T_ANALYSIS_STACK_PACKAGES = ("numpy", "scipy", "matplotlib", "pytest")


def flistImagePythonPackages(listDeclared):
    """Return the requirement lines an image installs for a project.

    The project's own lines come first and unchanged, followed by each
    stack package the project does not already name.
    """
    listImagePackages = list(listDeclared or [])
    setDeclaredNames = set(
        flistParseRequirementNames("\n".join(listImagePackages))
    )
    listImagePackages.extend(
        sPackage for sPackage in T_ANALYSIS_STACK_PACKAGES
        if sPackage not in setDeclaredNames
    )
    return listImagePackages
