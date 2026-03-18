"""Generic credential abstraction with ephemeral file support.

Secrets are never stored in environment variables, shell history, or
configuration files.  Retrieval methods delegate to established
credential managers (gh auth, OS keyring, Docker secrets).
"""

import os
import stat
import subprocess
import tempfile
from pathlib import Path


_VALID_METHODS = {"gh_auth", "keyring", "docker_secret"}


def fsRetrieveSecret(sName, sMethod):
    """Retrieve a secret via the named method (gh_auth|keyring|docker_secret)."""
    _fnValidateMethod(sMethod)
    dictDispatch = {
        "gh_auth": lambda sN: _fsRetrieveViaGhAuth(),
        "keyring": _fsRetrieveViaKeyring,
        "docker_secret": _fsRetrieveViaDockerSecret,
    }
    return dictDispatch[sMethod](sName)


def _fnValidateMethod(sMethod):
    """Raise ValueError if the method is not supported."""
    if sMethod not in _VALID_METHODS:
        raise ValueError(
            f"Unknown secret method '{sMethod}'. "
            f"Valid methods: {sorted(_VALID_METHODS)}"
        )


def _fsRetrieveViaGhAuth():
    """Run gh auth token and return the output."""
    try:
        resultProcess = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "The 'gh' CLI is not installed or not on PATH. "
            "Install it from https://cli.github.com/"
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"gh auth token failed (exit {error.returncode}). "
            "Run 'gh auth login' first."
        )
    return resultProcess.stdout.strip()


def _fsRetrieveViaKeyring(sName):
    """Retrieve a secret from the OS keyring."""
    try:
        import keyring
    except ImportError:
        raise ImportError(
            "The 'keyring' package is not installed. "
            "Install with: pip install vaibify[keyring]"
        )
    sValue = keyring.get_password("vaibify", sName)
    if sValue is None:
        raise KeyError(
            f"No keyring entry found for secret '{sName}' "
            "under service 'vaibify'."
        )
    return sValue


def _fsRetrieveViaDockerSecret(sName):
    """Read a Docker secret from /run/secrets/{name}."""
    pathSecret = Path(f"/run/secrets/{sName}")
    if not pathSecret.exists():
        raise FileNotFoundError(
            f"Docker secret not found: '{pathSecret}'"
        )
    return pathSecret.read_text().strip()


def fsMountSecret(sName, sMethod):
    """Retrieve a secret and write it to an ephemeral file (mode 600).

    Parameters
    ----------
    sName : str
        Logical name of the secret.
    sMethod : str
        Retrieval method passed to fsRetrieveSecret.

    Returns
    -------
    str
        Absolute path to the ephemeral file containing the secret.
    """
    sValue = fsRetrieveSecret(sName, sMethod)
    return _fsWriteEphemeralFile(sName, sValue)


def _fsGetTempDirectory():
    """Return a temp directory that Docker can reliably bind-mount."""
    import platform
    if platform.system() == "Darwin":
        return "/tmp"
    return None


def _fsWriteEphemeralFile(sName, sValue):
    """Write a value to a temp file with restrictive permissions."""
    iFileDescriptor, sFilePath = tempfile.mkstemp(
        prefix=f"vc_secret_{sName}_", suffix=".tmp",
        dir=_fsGetTempDirectory(),
    )
    try:
        os.fchmod(iFileDescriptor, stat.S_IRUSR | stat.S_IWUSR)
        os.write(iFileDescriptor, sValue.encode("utf-8"))
    finally:
        os.close(iFileDescriptor)
    return sFilePath


def fnCleanupSecretFiles(listPaths):
    """Remove ephemeral secret files.

    Parameters
    ----------
    listPaths : list of str
        File paths to remove. Missing files are silently skipped.
    """
    for sPath in listPaths:
        _fnRemoveFileIfExists(sPath)


def _fnRemoveFileIfExists(sPath):
    """Remove a single file if it exists, silently skip otherwise."""
    try:
        os.remove(sPath)
    except FileNotFoundError:
        pass


def flistPrepareDockerSecretArgs(listSecrets):
    """Build docker run arguments that mount each secret as a file.

    Parameters
    ----------
    listSecrets : list of dict
        Each dict has keys: "name" and "method".

    Returns
    -------
    list of str
        Docker CLI arguments (e.g. ["-v", "/tmp/file:/run/secrets/name"]).
    """
    listArgs = []
    for dictSecret in listSecrets:
        listArgs.extend(
            _flistBuildSingleSecretArgs(dictSecret)
        )
    return listArgs


def _flistBuildSingleSecretArgs(dictSecret):
    """Mount one secret and return its docker -v arguments."""
    sName = dictSecret["name"]
    sMethod = dictSecret["method"]
    sHostPath = fsMountSecret(sName, sMethod)
    sContainerPath = f"/run/secrets/{sName}"
    return ["-v", f"{sHostPath}:{sContainerPath}:ro"]
