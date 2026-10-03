"""The mutation scanner exempts imports that exist only for the type checker.

Source: ``tools/generateMutationInventory.py`` (``_fsetFindTypeOnlyImports``).

A string annotation for a process handle names ``Popen``, and pyflakes
rightly wants the name imported; but ``from subprocess import Popen`` is
an acquisition of the process-launch capability, and each one costs a
reviewed disposition. An import under ``if TYPE_CHECKING:`` never runs,
so it acquires nothing. The exemption is narrow on purpose -- it is a
static source convention, not a runtime security boundary -- and every
way of making the guard true, or the import run, must keep being counted.
The cases below are each of those ways.
"""

import ast
import importlib.util
import pathlib
import textwrap

import pytest

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def moduleGenerator():
    pathTool = PATH_REPOSITORY / "tools" / "generateMutationInventory.py"
    spec = importlib.util.spec_from_file_location(
        "generateMutationInventoryTypeChecking", pathTool,
    )
    moduleTool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(moduleTool)
    return moduleTool


def _flistScanAcquisitionNames(moduleGenerator, sSource):
    sDedented = textwrap.dedent(sSource)
    visitor = moduleGenerator._VisitorCallSites("synthetic.py", sDedented)
    visitor.fnCollect(ast.parse(sDedented))
    return [
        dictAcquisition["sCapabilityName"]
        for dictAcquisition in visitor.listAcquisitions
    ]


# ---------------------------------------------------------------------
# The two accepted spellings are exempt
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testBothAcceptedSpellingsOfTheGuardAreExempt(moduleGenerator):
    """``if TYPE_CHECKING:`` and ``if typing.TYPE_CHECKING:`` both count.

    Kills: dropping the exemption, which puts the string annotation's
    import back in the record as an unreviewed process-launch
    acquisition.
    """
    assert _flistScanAcquisitionNames(moduleGenerator, """
        from typing import TYPE_CHECKING, Optional
        if TYPE_CHECKING:
            from subprocess import Popen
        handle: Optional["Popen"] = None
    """) == []
    assert _flistScanAcquisitionNames(moduleGenerator, """
        import typing
        if typing.TYPE_CHECKING:
            import subprocess
        def fnWatch(handle: "subprocess.Popen") -> None:
            return None
    """) == []


def testAnImportOutsideAnyGuardIsStillCounted(moduleGenerator):
    assert _flistScanAcquisitionNames(moduleGenerator, """
        from typing import TYPE_CHECKING
        from subprocess import Popen
        if TYPE_CHECKING:
            from subprocess import Popen as AlsoPopen
    """) == ["subprocess.Popen"]


# ---------------------------------------------------------------------
# Every way to make the guard true, or to run the import, is counted
# ---------------------------------------------------------------------


def testAGuardThatIsNotTheFlagIsStillCounted(moduleGenerator):
    assert _flistScanAcquisitionNames(moduleGenerator, """
        from typing import TYPE_CHECKING
        if False:
            from subprocess import Popen
    """) == ["subprocess.Popen"]


@pytest.mark.falsification
def testAnImportUnderAnUnrelatedNamedGuardIsStillCounted(moduleGenerator):
    """A guard that merely LOOKS like a flag (``if DEBUG:``) exempts nothing.

    Kills: accepting any bare name as the guard, which lets a test-only
    switch hide an import that runs.
    """
    assert _flistScanAcquisitionNames(moduleGenerator, """
        from typing import TYPE_CHECKING
        DEBUG = True
        if DEBUG:
            from subprocess import Popen
    """) == ["subprocess.Popen"]


@pytest.mark.falsification
def testTheElseBranchOfTheGuardIsStillCounted(moduleGenerator):
    """The ``else:`` branch RUNS when the flag is false, which it is.

    Kills: exempting the whole ``if``, else-branch included.
    """
    assert _flistScanAcquisitionNames(moduleGenerator, """
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            pass
        else:
            from subprocess import Popen
    """) == ["subprocess.Popen"]


@pytest.mark.falsification
def testAReboundFlagIsStillCounted(moduleGenerator):
    """``TYPE_CHECKING = True`` makes the guarded import execute.

    Measured by the second review: with that line before the guard the
    import runs. A flag bound anywhere but by its one import is untrusted.

    Kills: trusting the flag when it is bound more than once.
    """
    assert _flistScanAcquisitionNames(moduleGenerator, """
        from typing import TYPE_CHECKING
        TYPE_CHECKING = True
        if TYPE_CHECKING:
            from subprocess import Popen
    """) == ["subprocess.Popen"]
    assert _flistScanAcquisitionNames(moduleGenerator, """
        import typing
        typing.TYPE_CHECKING = True
        if typing.TYPE_CHECKING:
            import subprocess
    """) == ["subprocess"]


def testAShadowedTypingModuleIsStillCounted(moduleGenerator):
    assert _flistScanAcquisitionNames(moduleGenerator, """
        import typing
        typing = object()
        if typing.TYPE_CHECKING:
            import subprocess
    """) == ["subprocess"]


@pytest.mark.parametrize("sPreamble, sGuard", [
    ("from typing import TYPE_CHECKING as TC", "TC"),
    ("import typing as t", "t.TYPE_CHECKING"),
])
def testAnAliasedFlagIsStillCounted(moduleGenerator, sPreamble, sGuard):
    assert _flistScanAcquisitionNames(
        moduleGenerator,
        f"{sPreamble}\nif {sGuard}:\n    import subprocess\n",
    ) == ["subprocess"]


@pytest.mark.falsification
def testAFlagFromAnotherModuleIsStillCounted(moduleGenerator):
    """``from othermodule import TYPE_CHECKING`` is not ``typing``'s flag.

    Kills: trusting the NAME ``TYPE_CHECKING`` whatever module it came
    from, which lets any module that defines one exempt its imports.
    """
    assert _flistScanAcquisitionNames(moduleGenerator, """
        from othermodule import TYPE_CHECKING
        if TYPE_CHECKING:
            import subprocess
    """) == ["subprocess"]


@pytest.mark.falsification
def testAGuardInsideAFunctionIsStillCounted(moduleGenerator):
    """Only a MODULE-level guard is recognized.

    Kills: finding guards anywhere in the tree, which exempts an import
    inside a function that runs when the function is called.
    """
    assert _flistScanAcquisitionNames(moduleGenerator, """
        from typing import TYPE_CHECKING
        def fnLaunch():
            if TYPE_CHECKING:
                import subprocess
    """) == ["subprocess"]


@pytest.mark.falsification
def testAnImportWhoseNameIsUsedAtRuntimeIsStillCounted(moduleGenerator):
    """An import standing in for a runtime use is not type-only.

    Kills: exempting every import under the guard whether or not its
    name is loaded outside an annotation.
    """
    assert _flistScanAcquisitionNames(moduleGenerator, """
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            from subprocess import Popen
        def fnLaunch(saCommand):
            return Popen(saCommand)
    """) == ["subprocess.Popen"]


def testAnImportBesideATypeOnlyOneIsDecidedOnItsOwn(moduleGenerator):
    """Two imports in one guard: the type-only one is exempt, the other not."""
    listNames = _flistScanAcquisitionNames(moduleGenerator, """
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            from subprocess import Popen
            import subprocess
        def fnRun(saCommand):
            return subprocess.run(saCommand)
        handle: "Popen"
    """)
    assert "subprocess.Popen" not in listNames
    assert "subprocess" in listNames


def testTheShippedModuleKeepsItsHandleImportOutOfTheRecord(moduleGenerator):
    """The real use: ``startReservation`` annotates a process handle."""
    pathModule = (
        PATH_REPOSITORY / "vaibify" / "gui" / "startReservation.py"
    )
    sSource = pathModule.read_text(encoding="utf-8")
    assert "from subprocess import Popen" in sSource
    assert _flistScanAcquisitionNames(moduleGenerator, sSource) == []
