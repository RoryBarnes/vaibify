"""One project tree, read through both legs of the confined reads.

The container leg is a fixed program run in a real subprocess; the host
leg is Python in this process. Both answer the same question about the
same files, and ``fdictOutcome`` reduces an answer to one comparable
value, so a divergence between the legs is an assertion rather than a
surprise in production.

Outcomes: ``("ok", bytes)``, ``("refused", message)``,
``("missing", message)`` or ``("failed", message)``.
"""

import os
import subprocess
import sys

from vaibify.docker import confinedRead
from vaibify.docker.confinedRead import ContainerReadRefusedError
from vaibify.host import hostConfinedRead

I_MEBIBYTE = 1 << 20
I_PROGRAM_TIMEOUT_SECONDS = 30
BA_BIG = bytes(range(256)) * 12288 + b"seventeen bytes!!"


def fsRealPath(pathAny):
    return os.path.realpath(str(pathAny))


def fnWriteFile(sPath, baContent):
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileOut:
        fileOut.write(baContent)


def fdictBuildProject(pathTmp):
    """Create the shared tree; return ``{"sRoot", "sOutside"}`` (real paths)."""
    sRoot = fsRealPath(pathTmp) + "/project"
    sOutside = fsRealPath(pathTmp) + "/outside"
    os.makedirs(sRoot)
    os.makedirs(sOutside)
    fnWriteFile(sOutside + "/secret.txt", b"secret")
    fnWriteFile(sRoot + "/plain.txt", b"hello")
    fnWriteFile(sRoot + "/big.bin", BA_BIG)
    fnWriteFile(sRoot + "/empty.txt", b"")
    fnWriteFile(sRoot + "/sub/nested/file.txt", b"nested")
    os.makedirs(sRoot + "/emptydir")
    os.symlink("plain.txt", sRoot + "/linkToPlain")
    os.symlink("chain2", sRoot + "/chain1")
    os.symlink("chain3", sRoot + "/chain2")
    os.symlink("plain.txt", sRoot + "/chain3")
    for iIndex in range(10):
        os.symlink(f"longchain{iIndex + 1}", f"{sRoot}/longchain{iIndex}")
    os.symlink("plain.txt", sRoot + "/longchain10")
    os.symlink(sRoot + "/plain.txt", sRoot + "/absoluteLink")
    os.symlink(sOutside + "/secret.txt", sRoot + "/escapeLink")
    os.symlink("../outside/secret.txt", sRoot + "/dotdotEscape")
    os.symlink("sub", sRoot + "/linkedDir")
    os.symlink("linkedDir/nested/file.txt", sRoot + "/linkThroughLinkedDir")
    os.symlink(sOutside, sRoot + "/outsideDirLink")
    os.mkfifo(sRoot + "/fifo")
    return {"sRoot": sRoot, "sOutside": sOutside}


def _ftRunProgram(sProgram):
    resultProc = subprocess.run(
        [sys.executable, "-c", sProgram], capture_output=True,
        timeout=I_PROGRAM_TIMEOUT_SECONDS,
    )
    return (resultProc.returncode, resultProc.stdout,
            resultProc.stderr.decode("utf-8", errors="replace"))


def _ftOutcomeOfContainerProgram(sProgram):
    iExit, baOut, sErr = _ftRunProgram(sProgram)
    sMessage = sErr.strip().splitlines()[-1] if sErr.strip() else ""
    if iExit == 0:
        return ("ok", baOut)
    if iExit == confinedRead.I_NOT_FOUND_EXIT_CODE:
        return ("missing", sMessage)
    if iExit == confinedRead.I_REFUSED_EXIT_CODE:
        return ("refused", sMessage)
    return ("failed", sMessage)


def _ftOutcomeOfHostGenerator(fnMakeGenerator):
    try:
        return ("ok", b"".join(fnMakeGenerator()))
    except FileNotFoundError as error:
        return ("missing", str(error))
    except ContainerReadRefusedError as error:
        return ("refused", str(error))
    except OSError as error:
        return ("failed", str(error))


def _ftOutcomeOfRenderedProgram(fsRender, sPath, sRoot):
    """A path the renderer itself refuses is a refusal, as at the connection."""
    try:
        sProgram = fsRender(sPath, sRoot)
    except ValueError as error:
        return ("refused", str(error))
    return _ftOutcomeOfContainerProgram(sProgram)


class ContainerLeg:
    sName = "container"

    @staticmethod
    def ftReadFile(sRoot, sPath):
        return _ftOutcomeOfRenderedProgram(
            confinedRead.fsRenderConfinedReadProgram, sPath, sRoot)

    @staticmethod
    def ftReadArchive(sRoot, sPath):
        return _ftOutcomeOfRenderedProgram(
            confinedRead.fsRenderConfinedArchiveProgram, sPath, sRoot)


class HostLeg:
    sName = "host"

    @staticmethod
    def ftReadFile(sRoot, sPath):
        return _ftOutcomeOfHostGenerator(
            lambda: hostConfinedRead.fiterStreamFileInsideRoot(sRoot, sPath))

    @staticmethod
    def ftReadArchive(sRoot, sPath):
        return _ftOutcomeOfHostGenerator(
            lambda: hostConfinedRead.fiterStreamDirectoryAsTar(sRoot, sPath))


LIST_LEGS = [ContainerLeg, HostLeg]
