"""Generic credential abstraction with ephemeral file support.

Secrets are never stored in environment variables, shell history, or
configuration files.  Retrieval methods delegate to established
credential managers (gh auth, OS keyring, Docker secrets).
"""

import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path


_VALID_METHODS = {"gh_auth", "keyring", "docker_secret"}
_RE_SECRET_NAME = re.compile(r"^[a-zA-Z0-9_:/-]{1,64}$")


def fsRetrieveSecret(sName, sMethod):
    """Retrieve a secret via the named method (gh_auth|keyring|docker_secret)."""
    _fnValidateMethod(sMethod)
    _fnValidateSecretName(sName)
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


def _fnValidateSecretName(sName):
    """Raise ValueError if ``sName`` would interpolate unsafely into paths.

    Audit M6: ``sName`` flows into ``/run/secrets/{sName}`` (see
    ``_fsRetrieveViaDockerSecret``) and into keyring service names.
    Per-remote keyring slots use ``service:owner/repo`` form (see
    ``githubAuth.fsKeyringSlotFor``), so ``:`` and ``/`` are
    valid alphabet members. The path-segment guard below rejects
    ``..``, empty segments, and leading slashes so a malicious slot
    name still cannot escape the ``/run/secrets`` directory.
    """
    if not isinstance(sName, str) or not _RE_SECRET_NAME.match(sName):
        raise ValueError(
            f"Invalid secret name '{sName}'. "
            "Must match ^[a-zA-Z0-9_:/-]{1,64}$."
        )
    listParts = sName.split("/")
    if "" in listParts or ".." in listParts:
        raise ValueError(
            f"Invalid secret name '{sName}'. "
            "Path segments must be non-empty and cannot be '..'."
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
    keyringModule = _fnLoadKeyringModule()
    sValue = keyringModule.get_password("vaibify", sName)
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


def fnStoreSecret(sName, sValue, sMethod):
    """Persist a secret via the named method."""
    _fnValidateMethod(sMethod)
    _fnValidateSecretName(sName)
    if sMethod != "keyring":
        raise NotImplementedError(
            f"Storing secrets via '{sMethod}' is not supported. "
            "Use the external credential manager instead."
        )
    _fnStoreViaKeyring(sName, sValue)


def _fnStoreViaKeyring(sName, sValue):
    """Set a password in the OS keyring under service 'vaibify'."""
    keyringModule = _fnLoadKeyringModule()
    keyringModule.set_password("vaibify", sName, sValue)


def fnDeleteSecret(sName, sMethod):
    """Remove a secret via the named method; idempotent for keyring."""
    _fnValidateMethod(sMethod)
    _fnValidateSecretName(sName)
    if sMethod != "keyring":
        raise NotImplementedError(
            f"Deleting secrets via '{sMethod}' is not supported."
        )
    _fnDeleteViaKeyring(sName)


def _fnDeleteViaKeyring(sName):
    """Delete a keyring entry, suppressing the absent-entry error."""
    keyringModule = _fnLoadKeyringModule()
    from keyring.errors import PasswordDeleteError
    try:
        keyringModule.delete_password("vaibify", sName)
    except PasswordDeleteError:
        pass


def fbSecretExists(sName, sMethod):
    """Return True if a secret is available via the named method."""
    _fnValidateMethod(sMethod)
    _fnValidateSecretName(sName)
    dictProbe = {
        "keyring": _fbKeyringHasSecret,
        "gh_auth": lambda sN: _fbGhAuthAvailable(),
        "docker_secret": _fbDockerSecretExists,
    }
    return dictProbe[sMethod](sName)


def _fbKeyringHasSecret(sName):
    """Return True if the OS keyring has an entry for sName."""
    try:
        keyringModule = _fnLoadKeyringModule()
        return keyringModule.get_password("vaibify", sName) is not None
    except Exception:
        return False


def _fbGhAuthAvailable():
    """Return True if 'gh auth token' currently yields a token."""
    try:
        return bool(_fsRetrieveViaGhAuth())
    except Exception:
        return False


def _fbDockerSecretExists(sName):
    """Return True if a Docker secret file exists at /run/secrets/<name>."""
    return Path(f"/run/secrets/{sName}").exists()


def _fnLoadKeyringModule():
    """Import and return the keyring module with a helpful error."""
    try:
        import keyring
    except ImportError:
        raise ImportError(
            "The 'keyring' package is not installed. "
            "Install with: pip install vaibify[keyring]"
        )
    return keyring


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
    """Return the per-user ephemeral root for secret-bearing temp files.

    Routed through ``ephemeralStore.fsGetEphemeralRoot`` so macOS and
    Linux both land at ``~/.vaibify/tmp/`` (mode 0700). That keeps
    secret filenames out of the world-traversable ``/tmp`` and
    matches Colima's default $HOME-only file-sharing on macOS.
    """
    from .ephemeralStore import fsGetEphemeralRoot
    return fsGetEphemeralRoot()


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
