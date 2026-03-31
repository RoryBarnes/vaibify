"""First-run shell configuration for Vaibify.

Silently configures shell completions, helper commands, and (on macOS)
the Colima Docker socket symlink.  Runs once, then writes a marker
file so subsequent invocations skip all setup work.
"""

import logging
import os
import platform
import sys

_MARKER_DIR = os.path.expanduser("~/.vaibify")
_MARKER_PATH = os.path.join(_MARKER_DIR, ".setup_done")

logger = logging.getLogger("vaibify")


def fbIsSetupComplete():
    """Return True when first-time setup has already run."""
    return os.path.isfile(_MARKER_PATH)


def fnRunFirstTimeSetup():
    """Orchestrate all first-run setup steps, then write the marker."""
    os.makedirs(_MARKER_DIR, exist_ok=True)
    fnConfigureCompletions()
    fnConfigureHelperCommands()
    fnLinkColimaSocket()
    _fnWriteMarkerFile()


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
        with open(sRcPath, "r") as fileHandle:
            return sNeedle in fileHandle.read()
    except (OSError, IOError):
        return False


def _fnAppendToRcFile(sRcPath, sBlock):
    """Append *sBlock* to the RC file, preceded by a blank line."""
    try:
        with open(sRcPath, "a") as fileHandle:
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


def _fnConfigureCompletionsInner():
    """Detect shell, locate completion file, append source line."""
    sShellName = _fsDetectShellName()
    sCompletionFile = _fsCompletionPathForShell(sShellName)
    if not sCompletionFile:
        return
    sRcPath = _fsDetectShellRcFile(sShellName)
    if not sRcPath:
        return
    if _fbRcFileContainsLine(sRcPath, sCompletionFile):
        return
    sSourceLine = f'[ -f "{sCompletionFile}" ] && . "{sCompletionFile}"'
    _fnAppendToRcFile(sRcPath, sSourceLine)


def _fsCompletionPathForShell(sShellName):
    """Return the completion file path if it exists, else empty string."""
    sCompletionsDir = _fsCompletionsDirectory()
    dictShellFile = {"bash": "vaibify.bash", "zsh": "vaibify.zsh"}
    sFileName = dictShellFile.get(sShellName, "")
    if not sFileName:
        return ""
    sFullPath = os.path.join(sCompletionsDir, sFileName)
    if not os.path.isfile(sFullPath):
        return ""
    return sFullPath


def fnConfigureHelperCommands():
    """Create shell aliases for connect_vc, vc_push, vc_pull."""
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
        with open(_MARKER_PATH, "w") as fileHandle:
            fileHandle.write("setup complete\n")
    except (OSError, IOError):
        logger.debug("Could not write marker file %s", _MARKER_PATH)
