"""CLI subcommand: vaibify build."""

import hashlib
import json
import os
import pathlib
import platform
import re
import subprocess
import sys

import click

from .configLoader import fconfigResolveProject, fsDockerDir
from .preflightChecks import fpreflightColimaVersion, fpreflightDaemon
from .preflightResult import PreflightResult, fnPrintPreflightReport


def fnBuildFromConfig(config, sDockerDir, bNoCache):
    """Invoke the Docker image builder with the loaded configuration."""
    fnBuildImage = _fImportBuildOrExit()
    fnPrepareBuildContext(config, sDockerDir)
    bEffectiveNoCache = _fbResolveNoCache(config, bNoCache)
    fnBuildImage(config, sDockerDir, bNoCache=bEffectiveNoCache)
    fnRecordBuildArgHash(config)
    fnPruneDanglingImages()


def _fImportBuildOrExit():
    """Lazy-import imageBuilder.fnBuildImage; exit cleanly on missing extra."""
    try:
        from vaibify.docker.imageBuilder import fnBuildImage
    except ImportError:
        click.echo(
            "Error: Docker support is not installed. "
            "Install with: pip install vaibify[docker]"
        )
        sys.exit(1)
    return fnBuildImage


def _fbResolveNoCache(config, bNoCache):
    """Decide whether to force --no-cache and announce the override."""
    bForceNoCache = fbBuildArgsChangedSinceLastBuild(config)
    if bForceNoCache and not bNoCache:
        click.echo(
            "[vaib] Build args changed since last build; "
            "forcing --no-cache."
        )
    return bNoCache or bForceNoCache


_S_BUILD_HASH_DIRECTORY = os.path.expanduser("~/.vaibify/cache")


def _fsBuildArgHashPath(sProjectName):
    """Return the cached build-arg hash file for sProjectName."""
    return os.path.join(
        _S_BUILD_HASH_DIRECTORY, f"{sProjectName}-arg-hash"
    )


def _fdictBuildArgInputs(config):
    """Collect ARG-affecting config values whose changes invalidate cache."""
    return {
        "PYTHON_VERSION": getattr(config, "sPythonVersion", ""),
        "BASE_IMAGE": getattr(config, "sBaseImage", ""),
        "PACKAGE_MANAGER": getattr(config, "sPackageManager", ""),
        "INSTALL_LATEX": str(
            getattr(getattr(config, "features", None), "bLatex", False)
        ).lower(),
        "INSTALL_X11": "true",
    }


def fsBuildArgHash(config):
    """Return a stable hash of ARG-affecting config values."""
    dictInputs = _fdictBuildArgInputs(config)
    sSerialized = json.dumps(dictInputs, sort_keys=True)
    return hashlib.sha256(sSerialized.encode()).hexdigest()


def fbBuildArgsChangedSinceLastBuild(config):
    """Return True iff the saved hash exists and disagrees with current."""
    sPath = _fsBuildArgHashPath(config.sProjectName)
    if not os.path.exists(sPath):
        return False
    try:
        with open(sPath, "r") as fileHandle:
            sPrevious = fileHandle.read().strip()
    except OSError:
        return False
    return sPrevious != fsBuildArgHash(config)


def fnRecordBuildArgHash(config):
    """Persist the current ARG hash so the next build can detect drift."""
    pathlib.Path(_S_BUILD_HASH_DIRECTORY).mkdir(
        parents=True, exist_ok=True,
    )
    sPath = _fsBuildArgHashPath(config.sProjectName)
    with open(sPath, "w") as fileHandle:
        fileHandle.write(fsBuildArgHash(config) + "\n")


def fnPruneDanglingImages():
    """Remove dangling Docker images left by previous builds."""
    try:
        resultPrune = subprocess.run(
            ["docker", "image", "prune", "-f"],
            capture_output=True, text=True, timeout=30,
        )
        if resultPrune.returncode == 0:
            listLines = resultPrune.stdout.strip().split("\n")
            sLastLine = listLines[-1] if listLines else ""
            if "reclaimed" in sLastLine.lower():
                click.echo(f"[vaib] {sLastLine}")
    except Exception:
        pass


def fnPrepareBuildContext(config, sDockerDir):
    """Generate all config-derived files in the Docker build context."""
    from vaibify.config.containerConfig import (
        fnGenerateContainerConf,
    )
    fnIncludeProjectRepo(config)
    fnGenerateContainerConf(
        config, os.path.join(sDockerDir, "container.conf")
    )
    fnWriteSystemPackages(config, sDockerDir)
    fnWritePythonPackages(config, sDockerDir)
    fnWritePipInstallFlags(config, sDockerDir)
    fnWriteBinariesEnv(config, sDockerDir)
    fnCopyDirectorScript(sDockerDir)
    fnCopyContainerScripts(sDockerDir)


_RE_APT_PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9._+-]*$")


def fnWriteSystemPackages(config, sDockerDir):
    """Write listSystemPackages to system-packages.txt."""
    fnValidateSystemPackageNames(config.listSystemPackages)
    sPath = os.path.join(sDockerDir, "system-packages.txt")
    sContent = "\n".join(config.listSystemPackages) + "\n"
    _fnWriteFile(sPath, sContent)


def fnValidateSystemPackageNames(listPackages):
    """Reject any apt-package name that violates the standard schema."""
    for sName in listPackages or []:
        if not _RE_APT_PACKAGE_NAME.match(sName or ""):
            raise ValueError(
                f"Invalid system package name: '{sName}'. "
                f"Names must match the apt schema "
                f"^[a-z0-9][a-z0-9._+-]*$. Edit `systemPackages` "
                f"in vaibify.yml."
            )


def fnWritePythonPackages(config, sDockerDir):
    """Write listPythonPackages to requirements.txt."""
    sPath = os.path.join(sDockerDir, "requirements.txt")
    sContent = "\n".join(config.listPythonPackages) + "\n"
    _fnWriteFile(sPath, sContent)


def fnWritePipInstallFlags(config, sDockerDir):
    """Write sPipInstallFlags to pip-flags.txt with --prefer-binary."""
    sPath = os.path.join(sDockerDir, "pip-flags.txt")
    sFlags = _fsEnsurePreferBinary(config.sPipInstallFlags)
    _fnWriteFile(sPath, sFlags + "\n")


_S_PREFER_BINARY_FLAG = "--prefer-binary"


def _fsEnsurePreferBinary(sFlags):
    """Prepend --prefer-binary to flags if not already present."""
    sStripped = (sFlags or "").strip()
    if _S_PREFER_BINARY_FLAG in sStripped.split():
        return sStripped
    if not sStripped:
        return _S_PREFER_BINARY_FLAG
    return f"{_S_PREFER_BINARY_FLAG} {sStripped}"


def fnWriteBinariesEnv(config, sDockerDir):
    """Write listBinaries to binaries.env as KEY=VALUE lines."""
    sPath = os.path.join(sDockerDir, "binaries.env")
    listLines = []
    for dictBinary in config.listBinaries:
        sName = dictBinary.get("name", "")
        sBinPath = dictBinary.get("path", "")
        if sName and sBinPath:
            listLines.append(f"{sName}={sBinPath}")
    sContent = "\n".join(listLines) + "\n"
    _fnWriteFile(sPath, sContent)


def fnCopyDirectorScript(sDockerDir):
    """Copy director.py into the Docker build context."""
    import shutil
    import pathlib
    sSourcePath = str(
        pathlib.Path(__file__).resolve().parents[1]
        / "gui" / "director.py"
    )
    sDestPath = os.path.join(sDockerDir, "director.py")
    shutil.copy2(sSourcePath, sDestPath)


def fnCopyContainerScripts(sDockerDir):
    """Stage the reproducibility modules that ship into the image.

    Each of these runs inside the container at /usr/share/vaibify/
    without a vaibify package install; they import from each other
    as flat top-level names. Add new ship-ins to the tuple below
    and to the ``COPY`` block in ``docker/Dockerfile``.
    """
    import shutil
    import pathlib
    pathReproducibility = (
        pathlib.Path(__file__).resolve().parents[1]
        / "reproducibility"
    )
    for sFileName in (
        "overleafSync.py", "latexConnector.py", "zenodoClient.py",
    ):
        sSourcePath = str(pathReproducibility / sFileName)
        sDestPath = os.path.join(sDockerDir, sFileName)
        shutil.copy2(sSourcePath, sDestPath)


def fnIncludeProjectRepo(config):
    """Add the project directory as a reference repo if not listed.

    Detects the git remote and branch of the project directory
    and appends it to the repository list so the entrypoint
    clones it into the container automatically.
    """
    sProjectDir = _fsProjectDirectory()
    sUrl = _fsGitRemoteUrl(sProjectDir)
    if not sUrl:
        return
    if _fbRepoAlreadyListed(config, sUrl):
        return
    sName = _fsRepoNameFromUrl(sUrl)
    sBranch = _fsGitBranch(sProjectDir)
    config.listRepositories.append({
        "name": sName, "url": sUrl,
        "branch": sBranch, "installMethod": "reference",
    })


def _fsGitRemoteUrl(sDirectory):
    """Return the git remote origin URL, or empty string."""
    import subprocess
    try:
        resultProcess = subprocess.run(
            ["git", "-C", sDirectory, "remote",
             "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if resultProcess.returncode == 0:
            return resultProcess.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _fsGitBranch(sDirectory):
    """Return the current git branch, defaulting to main."""
    import subprocess
    try:
        resultProcess = subprocess.run(
            ["git", "-C", sDirectory, "rev-parse",
             "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if resultProcess.returncode == 0:
            return resultProcess.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "main"


def _fsRepoNameFromUrl(sUrl):
    """Extract the repository name from a git URL."""
    sName = sUrl.rstrip("/").rsplit("/", 1)[-1]
    if sName.endswith(".git"):
        sName = sName[:-4]
    return sName


def _fbRepoAlreadyListed(config, sUrl):
    """Return True if the URL is already in the repo list."""
    sNormalized = sUrl.rstrip("/")
    for dictRepo in config.listRepositories:
        sExisting = dictRepo.get("url", "").rstrip("/")
        if sExisting == sNormalized:
            return True
    return False


def _fsProjectDirectory():
    """Return the host project directory for the current build."""
    from .configLoader import fsConfigPath
    return str(os.path.dirname(os.path.abspath(fsConfigPath())))


def _fnWriteFile(sPath, sContent):
    """Write string content to a file."""
    with open(sPath, "w") as fileHandle:
        fileHandle.write(sContent)


def _fnHandleBuildError(error):
    """Print a clean error message for build failures and exit."""
    if isinstance(error, RuntimeError):
        _fnEmitRuntimeBuildError(error)
    elif isinstance(error, (FileNotFoundError, OSError)):
        click.echo(
            f"Error: Build context preparation failed: {error}",
            err=True,
        )
    elif isinstance(error, ValueError):
        click.echo(f"Error: {error}", err=True)
    else:
        click.echo(f"Error: Build failed: {error}", err=True)
    sys.exit(1)


def _fnEmitRuntimeBuildError(error):
    """Print the user-facing Docker build error with a classification hint."""
    sBaseMessage = "Error: Docker build failed."
    sErrorText = str(error)
    sStderrTail = getattr(error, "sStderrTail", "")
    sClassification = _fsClassifyBuildError(
        sErrorText + "\n" + sStderrTail
    )
    sHint = _fsBuildErrorHint(sClassification)
    if sHint:
        click.echo(f"{sBaseMessage} {sHint}", err=True)
    else:
        click.echo(sBaseMessage, err=True)


_LIST_BUILD_ERROR_PATTERNS = [
    ("docker-hub-rate-limit", ("toomanyrequests", "pull rate limit")),
    ("manifest-not-found", (
        "manifest unknown",
        "not found: manifest",
        "manifest for ",
    )),
    ("network-tls", (
        "error:0a00010b",
        "ssleoferror",
        "ssl: ",
        "tls handshake",
        "tls or network failure",
    )),
    ("pip-source-build", (
        "gcc: error:",
        "error: invalid command 'bdist_wheel'",
        "failed building wheel for",
        "error: command '",
    )),
    ("oom", ("exit 137", "killed signal 9")),
]


def _fsClassifyBuildError(sErrorText):
    """Return a classification key for a known build-error pattern."""
    if not sErrorText:
        return ""
    sLower = sErrorText.lower()
    for sKey, tPatterns in _LIST_BUILD_ERROR_PATTERNS:
        if any(sPattern in sLower for sPattern in tPatterns):
            return sKey
    return ""


_DICT_BUILD_ERROR_HINTS = {
    "docker-hub-rate-limit": (
        "(Docker Hub rate-limited this IP. Wait 6 hours, run "
        "`docker login`, or set `baseImage` in vaibify.yml to a mirror.)"
    ),
    "manifest-not-found": (
        "(Base image not found in registry. Check `baseImage` in "
        "vaibify.yml.)"
    ),
    "network-tls": (
        "(Network or TLS failure during build. See the failing "
        "component above; retry, or check your proxy/DNS/MTU settings.)"
    ),
    "pip-source-build": (
        "(A pip package failed to build from source. `--prefer-binary` "
        "is already applied; check requirements.txt for the offending "
        "package. Workarounds: pin a different version that has a "
        "prebuilt wheel, or add the missing system dependency to "
        "systemPackages in vaibify.yml.)"
    ),
    "oom": (
        "(exit 137 -- likely OOM. Increase Docker VM memory, e.g. "
        "`colima stop && colima start --memory 6`.)"
    ),
}


def _fsBuildErrorHint(sClassification):
    """Return a remediation hint string for a classification key."""
    return _DICT_BUILD_ERROR_HINTS.get(sClassification, "")


_DICT_ARCH_NORMALIZATION = {
    "aarch64": "arm64",
    "arm64": "arm64",
    "x86_64": "amd64",
    "amd64": "amd64",
}


def _fsNormalizeArch(sArch):
    """Map raw arch labels to a canonical 'arm64'/'amd64' form."""
    return _DICT_ARCH_NORMALIZATION.get((sArch or "").strip().lower(), "")


def fsHostArch():
    """Return the canonical host architecture, '' if unrecognized."""
    return _fsNormalizeArch(platform.machine())


def fsDockerVmArch():
    """Return the canonical Docker VM architecture, '' on any error."""
    try:
        resultProcess = subprocess.run(
            ["docker", "info", "--format", "{{.Architecture}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if resultProcess.returncode != 0:
        return ""
    return _fsNormalizeArch(resultProcess.stdout)


def _fsArchRemediation():
    """Return remediation text for an arch mismatch (Colima-aware)."""
    from vaibify.docker.dockerContext import fbColimaActive
    if fbColimaActive():
        return "Run `colima delete && colima start --arch aarch64`."
    return (
        "Recreate your Docker VM/context with an aarch64 (arm64) "
        "architecture to avoid QEMU emulation."
    )


def _fpreflightArchGpuFail():
    """Return a fail-level PreflightResult for arm64 host with GPU feature."""
    return PreflightResult(
        sName="arch-gpu",
        sLevel="fail",
        sMessage=(
            "NVIDIA CUDA images are amd64-only; the GPU feature is "
            "not supported on Apple Silicon."
        ),
        sRemediation="Disable in vaibify.yml: `features: { gpu: false }`.",
    )


def _fpreflightArchQemuWarn(sHost, sVm):
    """Return a warn-level PreflightResult for QEMU-emulated builds."""
    return PreflightResult(
        sName="arch-mismatch",
        sLevel="warn",
        sMessage=(
            f"Host arch {sHost} differs from Docker VM arch {sVm}. "
            "Build will use QEMU emulation (5-10x slower)."
        ),
        sRemediation=_fsArchRemediation(),
    )


def _flistArchMismatchResults(config, sHost, sVm):
    """Build PreflightResult list for an arm64 host paired with amd64 VM."""
    if getattr(getattr(config, "features", None), "bGpu", False):
        return [_fpreflightArchGpuFail()]
    return [_fpreflightArchQemuWarn(sHost, sVm)]


def _fpreflightArch(config):
    """Return list of PreflightResult records for arch checks."""
    sHost = fsHostArch()
    sVm = fsDockerVmArch()
    if not sHost or not sVm:
        return []
    if sHost == "arm64" and sVm == "amd64":
        return _flistArchMismatchResults(config, sHost, sVm)
    return []


_I_DOCKER_DISK_WARN_BYTES = 50 * (2 ** 30)


def _fdiDockerDfBytes():
    """Return total bytes used reported by `docker system df`, or -1."""
    try:
        resultProcess = subprocess.run(
            ["docker", "system", "df", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1
    if resultProcess.returncode != 0:
        return -1
    return _fiSumDfSizeBytes(resultProcess.stdout)


def _fiParseDfRowBytes(sLine):
    """Parse one docker-df JSON row to bytes; -1 on any error."""
    try:
        dictRow = json.loads(sLine)
    except (ValueError, TypeError):
        return -1
    return _fiParseHumanSize(str(dictRow.get("Size", "")).strip())


def _fiSumDfSizeBytes(sJsonLines):
    """Sum Size across docker-df JSON rows; -1 on parse failure or empty."""
    iTotalBytes = 0
    bAnyParsed = False
    for sLine in (sJsonLines or "").splitlines():
        sLine = sLine.strip()
        if not sLine:
            continue
        iBytes = _fiParseDfRowBytes(sLine)
        if iBytes < 0:
            return -1
        iTotalBytes += iBytes
        bAnyParsed = True
    return iTotalBytes if bAnyParsed else -1


_DICT_SIZE_SUFFIX_MULTIPLIER = {
    "B": 1,
    "KB": 1000, "K": 1000,
    "MB": 1000 ** 2, "M": 1000 ** 2,
    "GB": 1000 ** 3, "G": 1000 ** 3,
    "TB": 1000 ** 4, "T": 1000 ** 4,
    "KIB": 1024, "MIB": 1024 ** 2,
    "GIB": 1024 ** 3, "TIB": 1024 ** 4,
}


def _fiParseHumanSize(sSize):
    """Parse '1.2GB'-style strings to bytes; -1 on parse failure."""
    sUpper = (sSize or "").upper().strip()
    if not sUpper:
        return 0
    for sSuffix in sorted(_DICT_SIZE_SUFFIX_MULTIPLIER, key=len, reverse=True):
        if sUpper.endswith(sSuffix):
            sNumber = sUpper[:-len(sSuffix)].strip()
            try:
                fValue = float(sNumber)
            except ValueError:
                return -1
            return int(fValue * _DICT_SIZE_SUFFIX_MULTIPLIER[sSuffix])
    try:
        return int(float(sUpper))
    except ValueError:
        return -1


def _fsDiskRemediation():
    """Return remediation text for a near-full Docker VM disk."""
    from vaibify.docker.dockerContext import fbColimaActive
    sBase = "Run `docker system prune -af` to reclaim space."
    if fbColimaActive():
        return (
            f"{sBase} If still tight, grow the VM with "
            "`colima stop && colima start --disk 100`."
        )
    return sBase


def _fpreflightDiskWarn(iBytes):
    """Return a warn-level PreflightResult for high Docker disk usage."""
    fGigabytes = iBytes / (2 ** 30)
    return PreflightResult(
        sName="docker-disk",
        sLevel="warn",
        sMessage=(
            f"Docker is using {fGigabytes:.1f} GB of images/volumes; "
            "the VM may run out of space mid-build."
        ),
        sRemediation=_fsDiskRemediation(),
    )


def _fpreflightDisk():
    """Return list of PreflightResult records for Docker disk usage."""
    iBytes = _fdiDockerDfBytes()
    if iBytes < 0:
        return [PreflightResult(
            sName="docker-disk",
            sLevel="info",
            sMessage="Could not assess Docker disk usage.",
        )]
    if iBytes >= _I_DOCKER_DISK_WARN_BYTES:
        return [_fpreflightDiskWarn(iBytes)]
    return []


_I_DOCKER_MEMORY_MIN_BYTES = 4 * (2 ** 30)


def _fiDockerVmMemoryBytes():
    """Return Docker VM total memory in bytes, or -1 on any error."""
    try:
        resultProcess = subprocess.run(
            ["docker", "info", "--format", "{{.MemTotal}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1
    if resultProcess.returncode != 0:
        return -1
    sValue = (resultProcess.stdout or "").strip()
    try:
        return int(sValue)
    except ValueError:
        return -1


def _fsMemoryRemediation():
    """Return remediation text for low Docker VM memory."""
    from vaibify.docker.dockerContext import fbColimaActive
    if fbColimaActive():
        return "Run `colima stop && colima start --memory 6`."
    return "Increase the memory allocation of your Docker VM."


def _fpreflightMemory():
    """Return list of PreflightResult records for Docker VM memory."""
    iBytes = _fiDockerVmMemoryBytes()
    if iBytes < 0:
        return []
    if iBytes < _I_DOCKER_MEMORY_MIN_BYTES:
        fGigabytes = iBytes / (2 ** 30)
        return [PreflightResult(
            sName="docker-memory",
            sLevel="warn",
            sMessage=(
                f"Docker VM has {fGigabytes:.1f} GB RAM. Builds with "
                "heavy Python packages may OOM (exit 137)."
            ),
            sRemediation=_fsMemoryRemediation(),
        )]
    return []


def flistRunBuildPreflight(config):
    """Return ordered list of PreflightResult records for `vaibify build`."""
    listResults = [fpreflightDaemon("build")]
    if any(r.sLevel == "fail" and r.sName == "docker-daemon"
           for r in listResults):
        return listResults
    listResults.extend(_fpreflightArch(config))
    listResults.extend(_fpreflightDisk())
    listResults.extend(_fpreflightMemory())
    resultColimaVersion = fpreflightColimaVersion()
    if resultColimaVersion is not None:
        listResults.append(resultColimaVersion)
    return listResults


def _fnPrintWarningsIfAny(listResults):
    """Print warn-level results so users see them before the build."""
    listWarnings = [r for r in listResults if r.sLevel == "warn"]
    if not listWarnings:
        return
    fnPrintPreflightReport(listWarnings)


def _fnEnforceBuildPreflight(config):
    """Run pre-flight checks; exit on fail, print warnings on warn."""
    listPreflight = flistRunBuildPreflight(config)
    if any(r.sLevel == "fail" for r in listPreflight):
        fnPrintPreflightReport(listPreflight)
        sys.exit(1)
    _fnPrintWarningsIfAny(listPreflight)


@click.command("build")
@click.option(
    "--no-cache",
    "bNoCache",
    is_flag=True,
    default=False,
    help="Build the image without using Docker cache.",
)
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name (omit if in a project directory "
    "or only one project exists).",
)
def build(bNoCache, sProjectName):
    """Build the Vaibify Docker image from vaibify.yml."""
    config = fconfigResolveProject(sProjectName)
    sDockerDir = fsDockerDir()
    _fnEnforceBuildPreflight(config)
    click.echo(f"Building image {config.sProjectName}:latest ...")
    try:
        fnBuildFromConfig(config, sDockerDir, bNoCache)
    except (RuntimeError, FileNotFoundError, OSError,
            ValueError) as error:
        _fnHandleBuildError(error)
    click.echo("Build complete.")
    click.echo(
        "Run `vaibify stop && vaibify start` to pick up the new "
        "image in your container."
    )
