"""CLI subcommand: vaibify build."""

import os
import subprocess
import sys

import click

from .configLoader import fconfigResolveProject, fsDockerDir


def fnBuildFromConfig(config, sDockerDir, bNoCache):
    """Invoke the Docker image builder with the loaded configuration.

    Uses lazy import so the CLI remains usable without Docker installed.
    """
    try:
        from vaibify.docker.imageBuilder import fnBuildImage
    except ImportError:
        click.echo(
            "Error: Docker support is not installed. "
            "Install with: pip install vaibify[docker]"
        )
        sys.exit(1)
    fnPrepareBuildContext(config, sDockerDir)
    fnBuildImage(config, sDockerDir, bNoCache=bNoCache)
    fnPruneDanglingImages()


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
    fnCopyOverleafScripts(sDockerDir)
    fnCopyHostWorkflows(sDockerDir)


def fnWriteSystemPackages(config, sDockerDir):
    """Write listSystemPackages to system-packages.txt."""
    sPath = os.path.join(sDockerDir, "system-packages.txt")
    sContent = "\n".join(config.listSystemPackages) + "\n"
    _fnWriteFile(sPath, sContent)


def fnWritePythonPackages(config, sDockerDir):
    """Write listPythonPackages to requirements.txt."""
    sPath = os.path.join(sDockerDir, "requirements.txt")
    sContent = "\n".join(config.listPythonPackages) + "\n"
    _fnWriteFile(sPath, sContent)


def fnWritePipInstallFlags(config, sDockerDir):
    """Write sPipInstallFlags to pip-flags.txt."""
    sPath = os.path.join(sDockerDir, "pip-flags.txt")
    _fnWriteFile(sPath, config.sPipInstallFlags.strip() + "\n")


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


def fnCopyOverleafScripts(sDockerDir):
    """Stage overleafSync.py and latexConnector.py for the image.

    Both files run standalone inside the container at
    /usr/share/vaibify/ — no vaibify package install required.
    """
    import shutil
    import pathlib
    pathReproducibility = (
        pathlib.Path(__file__).resolve().parents[1]
        / "reproducibility"
    )
    for sFileName in ("overleafSync.py", "latexConnector.py"):
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


def fnCopyHostWorkflows(sDockerDir):
    """Copy host .vaibify/workflows/ into the build context.

    Searches the project directory for workflow JSON files and
    stages them so the Dockerfile can bake them into the image.
    The entrypoint copies them into the workspace volume on
    first start, making workflows available without manual
    file transfer.
    """
    import glob
    import shutil
    sProjectDir = _fsProjectDirectory()
    sHostWorkflows = os.path.join(
        sProjectDir, ".vaibify", "workflows",
    )
    sDestDir = os.path.join(sDockerDir, "workflows")
    if os.path.isdir(sDestDir):
        shutil.rmtree(sDestDir)
    os.makedirs(sDestDir, exist_ok=True)
    if not os.path.isdir(sHostWorkflows):
        return
    for sFile in glob.glob(os.path.join(sHostWorkflows, "*.json")):
        shutil.copy2(sFile, sDestDir)


def _fsProjectDirectory():
    """Return the host project directory for the current build."""
    from .configLoader import fsConfigPath
    return str(os.path.dirname(os.path.abspath(fsConfigPath())))


def _fnWriteFile(sPath, sContent):
    """Write string content to a file."""
    with open(sPath, "w") as fileHandle:
        fileHandle.write(sContent)


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
    click.echo(
        f"Building image {config.sProjectName}:latest ..."
    )
    fnBuildFromConfig(config, sDockerDir, bNoCache)
    click.echo("Build complete.")
    click.echo(
        "Run `vaibify stop && vaibify start` to pick up the new "
        "image in your container."
    )
