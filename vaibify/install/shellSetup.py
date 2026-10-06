"""First-run shell configuration for Vaibify.

Silently configures shell completions, helper commands, and (on macOS)
the Colima Docker socket symlink.  Runs once per setup VERSION, then
writes a marker file so subsequent invocations skip all setup work.

The marker records the version of setup that wrote it, and a marker
older than ``I_SETUP_VERSION`` runs setup again. The first marker said
only "setup complete", so a machine whose setup had configured nothing
useful (the completion scripts were not yet packaged when it ran, and
fish had no script at all) could never be repaired by shipping better
scripts: the marker made every later run skip them. Raise the version
whenever setup gains a step an existing installation must receive.
Setup only ever APPENDS to a shell's configuration; it never edits or
removes a line it, or the researcher, wrote earlier.
"""

import logging
import os
import platform
import re

_MARKER_DIR = os.path.expanduser("~/.vaibify")
_MARKER_PATH = os.path.join(_MARKER_DIR, ".setup_done")
I_SETUP_VERSION = 2
_RE_MARKER_VERSION = re.compile(r"setup v(\d+)")
_DICT_COMPLETION_FILE_FOR_SHELL = {
    "bash": "vaibify.bash",
    "zsh": "vaibify.zsh",
    "fish": "vaibify.fish",
}

logger = logging.getLogger("vaibify")


def fiReadSetupVersion():
    """Return the setup version the marker records; 0 when there is none.

    A marker this function cannot parse is the original "setup
    complete" text, which is version 1 by definition.
    """
    try:
        with open(_MARKER_PATH, "r", encoding="utf-8") as fileHandle:
            sMarker = fileHandle.read().strip()
    except (OSError, IOError):
        return 0
    matchVersion = _RE_MARKER_VERSION.fullmatch(sMarker)
    return int(matchVersion.group(1)) if matchVersion else 1


def fbIsSetupComplete():
    """Return True when setup of the current version has already run."""
    return fiReadSetupVersion() >= I_SETUP_VERSION


def fnRunFirstTimeSetup():
    """Orchestrate all first-run setup steps, then write the marker.

    The marker is withheld when the completion scripts are missing from
    the installation, because that is an installation defect rather
    than a finished setup. Recording it as done made the defect
    permanent per machine: the completions shipped outside the package
    for the whole of vaibify's history, so this step configured nothing
    and then guaranteed it would never try again.

    A shell with no completion script of its own (sh, csh) is not a
    defect and does not withhold the marker.
    """
    os.makedirs(_MARKER_DIR, exist_ok=True)
    fnConfigureCompletions()
    fnConfigureHelperCommands()
    fnLinkColimaSocket()
    if not fbCompletionsArePresent():
        logger.warning(
            "Vaibify's shell completions are missing from this "
            "installation (expected in '%s'). Setup will run again "
            "on the next command.",
            _fsCompletionsDirectory(),
        )
        return
    _fnWriteMarkerFile()


def fbCompletionsArePresent():
    """Return True when the installation carries ALL its completion scripts.

    The directory alone is not evidence: a wheel that shipped two of
    the three scripts would still have the directory, and the shell
    missing its script would be told nothing.
    """
    sCompletionsDirectory = _fsCompletionsDirectory()
    return all(
        os.path.isfile(os.path.join(sCompletionsDirectory, sFileName))
        for sFileName in _DICT_COMPLETION_FILE_FOR_SHELL.values()
    )


def _fsDetectShellName():
    """Return the current shell name (e.g. 'zsh', 'bash', 'fish')."""
    sShell = os.environ.get("SHELL", "/bin/sh")
    return os.path.basename(sShell)


def _fsDetectShellRcFile(sShellName):
    """Return the RC file path for the given shell."""
    if sShellName == "zsh":
        return os.path.expanduser("~/.zshrc")
    if sShellName == "bash":
        if platform.system() == "Darwin":
            return os.path.expanduser("~/.bash_profile")
        return os.path.expanduser("~/.bashrc")
    if sShellName == "fish":
        return os.path.expanduser("~/.config/fish/config.fish")
    return ""


def _fbRcFileContainsLine(sRcPath, sNeedle):
    """Return True if *sNeedle* already appears in the RC file."""
    try:
        with open(sRcPath, "r", encoding="utf-8") as fileHandle:
            return sNeedle in fileHandle.read()
    except (OSError, IOError):
        return False


def _fnAppendToRcFile(sRcPath, sBlock):
    """Append *sBlock* to the RC file, preceded by a blank line."""
    try:
        os.makedirs(os.path.dirname(sRcPath), exist_ok=True)
        with open(sRcPath, "a", encoding="utf-8") as fileHandle:
            fileHandle.write("\n# Added by Vaibify\n")
            fileHandle.write(sBlock + "\n")
    except (OSError, IOError):
        logger.debug("Could not write to %s", sRcPath)


def _fsCompletionsDirectory():
    """Return the absolute path to the completions directory."""
    sPackageDir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(sPackageDir, "completions")


def fnConfigureCompletions():
    """Source the appropriate tab-completion script in the RC file."""
    try:
        _fnConfigureCompletionsInner()
    except Exception:
        logger.debug("Completion setup skipped", exc_info=True)


def ftInspectCompletionWiring():
    """Return (shell name, rc file, completion script, bSourced) for $SHELL.

    READS only, and is the one place that decides what "wired" means,
    so that setup (which appends the missing line) and ``vaibify
    doctor`` (which only reports it) cannot disagree. The rc file and
    the script are empty strings for a shell vaibify has no script for.
    """
    sShellName = _fsDetectShellName()
    sCompletionFile = _fsCompletionPathForShell(sShellName)
    sRcPath = _fsDetectShellRcFile(sShellName)
    bSourced = bool(sCompletionFile and sRcPath) and _fbRcFileContainsLine(
        sRcPath, sCompletionFile,
    )
    return sShellName, sRcPath, sCompletionFile, bSourced


def fsBuildCompletionSourceLine(sShellName, sCompletionFile):
    """Return the line that loads a completion script, in the shell's syntax.

    fish does not read ``[ ... ] && .``: its conditional is ``test``
    and its sourcing verb is ``source``.
    """
    if sShellName == "fish":
        return (
            f'test -f "{sCompletionFile}"; '
            f'and source "{sCompletionFile}"'
        )
    return f'[ -f "{sCompletionFile}" ] && . "{sCompletionFile}"'


def _fnConfigureCompletionsInner():
    """Detect shell, locate completion file, append source line."""
    sShellName, sRcPath, sCompletionFile, bSourced = (
        ftInspectCompletionWiring()
    )
    if not sCompletionFile or not sRcPath or bSourced:
        return
    _fnAppendToRcFile(
        sRcPath, fsBuildCompletionSourceLine(sShellName, sCompletionFile),
    )


def _fsCompletionPathForShell(sShellName):
    """Return the completion file path if it exists, else empty string."""
    sCompletionsDir = _fsCompletionsDirectory()
    sFileName = _DICT_COMPLETION_FILE_FOR_SHELL.get(sShellName, "")
    if not sFileName:
        return ""
    sFullPath = os.path.join(sCompletionsDir, sFileName)
    if not os.path.isfile(sFullPath):
        return ""
    return sFullPath


def fnConfigureHelperCommands():
    """Create shell aliases for vaibify connect, push and pull.

    The names here are the ones the completion scripts register
    against; they were renamed once already and the completions were
    left pointing at the retired spellings, so the two must be
    changed together.
    """
    try:
        _fnConfigureHelperCommandsInner()
    except Exception:
        logger.debug("Helper command setup skipped", exc_info=True)


def _fnConfigureHelperCommandsInner():
    """Append helper aliases to the shell RC file."""
    sShellName = _fsDetectShellName()
    sRcPath = _fsDetectShellRcFile(sShellName)
    if not sRcPath:
        return
    if _fbRcFileContainsLine(sRcPath, "vaibify_connect"):
        return
    sAliases = _fsHelperAliasBlock(sShellName)
    _fnAppendToRcFile(sRcPath, sAliases)


def _fsHelperAliasBlock(sShellName):
    """Return the alias block appropriate for the shell."""
    if sShellName == "fish":
        return (
            "alias vaibify_connect 'vaibify connect'\n"
            "alias vaibify_push 'vaibify push'\n"
            "alias vaibify_pull 'vaibify pull'\n"
            "alias vaib_connect 'vaib connect'\n"
            "alias vaib_push 'vaib push'\n"
            "alias vaib_pull 'vaib pull'"
        )
    return (
        "alias vaibify_connect='vaibify connect'\n"
        "alias vaibify_push='vaibify push'\n"
        "alias vaibify_pull='vaibify pull'\n"
        "alias vaib_connect='vaib connect'\n"
        "alias vaib_push='vaib push'\n"
        "alias vaib_pull='vaib pull'"
    )


def fnLinkColimaSocket():
    """On macOS, symlink the Colima socket to /var/run/docker.sock."""
    try:
        _fnLinkColimaSocketInner()
    except Exception:
        logger.debug("Colima socket link skipped", exc_info=True)


def _fnLinkColimaSocketInner():
    """Attempt the symlink only when safe to do so without sudo."""
    if platform.system() != "Darwin":
        return
    sStandardSocket = "/var/run/docker.sock"
    if os.path.exists(sStandardSocket):
        return
    if not os.access("/var/run", os.W_OK):
        return
    sColimaSocket = os.path.expanduser(
        "~/.colima/default/docker.sock"
    )
    if not os.path.exists(sColimaSocket):
        return
    os.symlink(sColimaSocket, sStandardSocket)


def _fnWriteMarkerFile():
    """Write the marker file that prevents re-running setup."""
    try:
        with open(_MARKER_PATH, "w", encoding="utf-8") as fileHandle:
            fileHandle.write(f"setup v{I_SETUP_VERSION}\n")
    except (OSError, IOError):
        logger.debug("Could not write marker file %s", _MARKER_PATH)
