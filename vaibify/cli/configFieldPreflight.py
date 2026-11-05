"""Check the vaibify.yml fields a build turns into container identity.

Three fields the wizards write are used verbatim by the image build
and were validated nowhere: ``containerUser`` becomes a ``useradd``
argument, ``pythonVersion`` is pasted into ``apt-get install
python<version>``, and ``workspaceRoot`` becomes every mount and every
path the dashboard resolves. A wrong value in any of them fails the
build minutes in, with apt's or useradd's words rather than the
field's name.

These are FORMAT checks against what the recipe can use, not taste:
each one names the field, what it holds, and what it must be. They are
deliberately not in ``fconfigFromYamlDict``: a config that fails to
LOAD vanishes from the dashboard entirely, and a project a researcher
can still see and correct is the better failure.
"""

import re

from .preflightResult import PreflightResult, S_LEVEL_FAIL, S_SCOPE_PROJECT


__all__ = [
    "S_PREFLIGHT_NAME",
    "flistDescribeInvalidFields",
    "flistDescribeUnusableConfiguration",
    "flistDescribeUnusableProjectConfiguration",
    "fpreflightConfigurationFields",
]


S_PREFLIGHT_NAME = "configuration-fields"

# useradd's own portable name rule, and root is refused separately:
# the image drops privileges to this user, so naming root would undo
# the unprivileged-user protection the entrypoint rests on.
_REGEX_CONTAINER_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# The Dockerfile installs `python${PYTHON_VERSION}`, which is an
# apt package name: major.minor, never a patch version or a "python"
# prefix.
_REGEX_PYTHON_VERSION = re.compile(r"^3\.\d{1,2}$")


def _fsDescribeContainerUser(sContainerUser):
    """Return the complaint about containerUser, or '' when it is usable."""
    if sContainerUser == "root" or not sContainerUser:
        return (
            "containerUser is 'root'; the image drops privileges to this "
            "user, so it must be an unprivileged name"
        ) if sContainerUser else "containerUser is empty"
    if not _REGEX_CONTAINER_USER.match(sContainerUser):
        return (
            f"containerUser '{sContainerUser}' is not a usable Unix user "
            "name (lower-case letter or underscore first, then letters, "
            "digits, underscore or hyphen, at most 32 characters)"
        )
    return ""


def _fsDescribePythonVersion(sPythonVersion):
    """Return the complaint about pythonVersion, or '' when it is usable."""
    if not _REGEX_PYTHON_VERSION.match(sPythonVersion or ""):
        return (
            f"pythonVersion '{sPythonVersion}' is not a major.minor version "
            "the image can install; it becomes the apt package "
            f"'python{sPythonVersion}' (write '3.12', not 'python3.12' or "
            "'3.12.1')"
        )
    return ""


def _fsDescribeWorkspaceRoot(sWorkspaceRoot):
    """Return the complaint about workspaceRoot, or '' when it is usable."""
    if not sWorkspaceRoot.startswith("/"):
        return (
            f"workspaceRoot '{sWorkspaceRoot}' is not an absolute path; it "
            "is the directory every mount and every dashboard path "
            "resolves against"
        )
    if sWorkspaceRoot.rstrip("/") == "":
        return "workspaceRoot is '/', which would mount the container root"
    return ""


def _fsDescribeX11WithNetworkIsolation(config):
    """Return the complaint about X11 forwarding on a sealed container.

    Isolation and allowlist mode both seal the container's network; a
    display channel into either would defeat the seal (ruling D4).
    """
    if not getattr(config, "bX11Forwarding", False):
        return ""
    sMode = getattr(config, "sNetworkMode", "")
    if getattr(config, "bNetworkIsolation", False) or sMode == "isolated":
        return (
            "x11Forwarding and networkIsolation are both true; a "
            "container with no network cannot reach the X server that "
            "forwarding exists to reach, and forwarding a display into "
            "a sealed container would defeat the seal. Set one of them "
            "to false"
        )
    from vaibify.config.projectConfig import fbIsSealedNetworkMode
    if fbIsSealedNetworkMode(sMode):
        return (
            f"x11Forwarding is true while networkMode is {sMode}; a "
            "display channel into a sealed container would defeat the "
            "seal. Set networkMode: open explicitly (vaibify will ask you "
            "to approve it) or x11Forwarding to false"
        )
    return ""


def _fsDescribeUnstatedNetworkMode(config):
    """Return the D1 complaint for a project file that names no mode, or ''.

    Only a loaded project configuration can be unstated: a wizard
    request never carries this field empty with that meaning (an empty
    request mode means the new-project mode), so this is asked of the
    configuration lane alone. As with the fields above, a config that
    does not CARRY the mode at all is not graded; a loaded file always
    carries it, empty when the file names none.
    """
    from vaibify.docker.containerManager import fsNetworkModeOf
    from vaibify.config.projectConfig import (
        S_NETWORK_MODE_UNSTATED, fsDescribeUnstatedNetworkMode,
    )
    if not hasattr(config, "sNetworkMode") and not hasattr(config, "bNetworkIsolation"):
        return ""
    if fsNetworkModeOf(config) != S_NETWORK_MODE_UNSTATED:
        return ""
    return fsDescribeUnstatedNetworkMode(
        getattr(config, "sProjectName", "") or "this project")


_T_FIELD_DESCRIBERS = (
    ("sContainerUser", _fsDescribeContainerUser),
    ("sPythonVersion", _fsDescribePythonVersion),
    ("sWorkspaceRoot", _fsDescribeWorkspaceRoot),
)


def flistDescribeInvalidFields(config):
    """Return one sentence per field a build cannot use as written.

    A field the config does not CARRY is not graded: every one of
    these is a dataclass field with a working default, so absence
    means the default applies and there is nothing to complain about.
    An empty value that is actually present is a complaint, because
    that is a field someone cleared.
    """
    listComplaints = []
    for sField, fnDescribe in _T_FIELD_DESCRIBERS:
        sValue = getattr(config, sField, None)
        if sValue is None:
            continue
        sComplaint = fnDescribe(sValue)
        if sComplaint:
            listComplaints.append(sComplaint)
    return listComplaints


def flistDescribeUnusableConfiguration(config):
    """Return every complaint about a config or a wizard request.

    The invalid fields, plus the one conflict that spans two of them:
    X11 forwarding asked for on a container sealed from the network.
    """
    listComplaints = flistDescribeInvalidFields(config)
    sX11Complaint = _fsDescribeX11WithNetworkIsolation(config)
    if sX11Complaint:
        listComplaints.append(sX11Complaint)
    return listComplaints


def flistDescribeUnusableProjectConfiguration(config):
    """Return every complaint about a loaded project configuration.

    The request-or-config complaints, plus the one only a file can
    carry: no network mode at all (ruling D1). A build is refused on it
    here, before an hour-long image build, with the sentence the start
    would give.
    """
    listComplaints = flistDescribeUnusableConfiguration(config)
    sUnstatedComplaint = _fsDescribeUnstatedNetworkMode(config)
    if sUnstatedComplaint:
        listComplaints.append(sUnstatedComplaint)
    return listComplaints


def fpreflightConfigurationFields(config):
    """Return a fail result for unusable fields, else None."""
    listComplaints = flistDescribeUnusableProjectConfiguration(config)
    if not listComplaints:
        return None
    return PreflightResult(
        sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_FAIL, sScope=S_SCOPE_PROJECT,
        sMessage="; ".join(listComplaints) + ".",
        sRemediation="Correct the field in vaibify.yml, then build again.",
    )
