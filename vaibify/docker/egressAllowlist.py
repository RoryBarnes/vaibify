"""What an allowlist-mode project container may reach, and why each entry.

The base allowlist is COMPUTED from the project's own configuration,
never typed in whole by the researcher, so every host on it has a
reason stated here: the enabled agents' login and API hosts, GitHub
(by ruling: a researcher is not kept from any repository they normally
reach), the package indexes the image's package manager uses, the npm
registry when an agent auto-updates, every repository the project
clones, and the researcher's own ``allowedHosts``. Zenodo, Overleaf and
arXiv are absent on purpose: their operations run on the host since the
publishing tokens left the container.

A host the agent needs that is not here appears in the dashboard's
refused list, where the researcher can allow it (ruling D8). The
secondary agents (OpenCode, Cline, OpenHands, Pi) get only the hosts
their own login needs (ruling D9); the model providers they are
configured for show up in the refused list the same way.
"""

__all__ = [
    "DICT_AGENT_HOSTS",
    "T_CONDA_HOSTS",
    "T_GITHUB_HOSTS",
    "T_NPM_HOSTS",
    "T_PYPI_HOSTS",
    "fbIsAllowedHostEntry",
    "flistRepositoryHosts",
    "ftComputeAllowlist",
    "ftSplitHostEntry",
]

import ipaddress
import re
from urllib.parse import urlsplit

from .egressProxy import fnValidateAllowlistOrRaise

# Per enabled agent: the hosts its login and its API traffic need. Claude
# Code's telemetry and crash-report hosts are not here because the
# container runs with CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1.
DICT_AGENT_HOSTS = {
    # Claude Code: the API, the subscription login and the console.
    "bClaude": ("api.anthropic.com", "claude.ai", "platform.claude.com",
                "console.anthropic.com"),
    # Codex: the ChatGPT backend the council already allows, the API
    # and the device-login endpoint.
    "bCodex": ("chatgpt.com", "api.openai.com", "auth.openai.com"),
    # Gemini CLI: the Gemini API, Google sign-in and the Cloud Code
    # endpoint its subscription login uses.
    "bGemini": ("generativelanguage.googleapis.com", "oauth2.googleapis.com",
                "accounts.google.com", "cloudcode-pa.googleapis.com"),
    # Antigravity: Google sign-in plus the same Gemini endpoints.
    "bAntigravity": ("antigravity.google", "accounts.google.com",
                     "oauth2.googleapis.com", "cloudcode-pa.googleapis.com",
                     "generativelanguage.googleapis.com"),
    # Ruling D9: login hosts only for the four below; the providers they
    # are configured for appear in the refused list.
    "bOpenCode": ("opencode.ai",),
    "bCline": ("app.cline.bot", "api.cline.bot"),
    "bOpenHands": ("app.all-hands.dev",),
    # Pi signs in to no service of its own; it reaches only providers.
    "bPi": (),
}
# By ruling: a researcher is not kept from any repository they normally
# reach, and these are the hosts a clone, a push, a release download and
# a raw file fetch touch.
T_GITHUB_HOSTS = ("github.com", "api.github.com", "codeload.github.com",
                  "objects.githubusercontent.com", "raw.githubusercontent.com")
T_PYPI_HOSTS = ("pypi.org", "files.pythonhosted.org")
T_CONDA_HOSTS = ("conda.anaconda.org", "repo.anaconda.com")
# The agents install and update themselves from npm.
T_NPM_HOSTS = ("registry.npmjs.org",)
I_DEFAULT_PORT = 443
_RE_HOST_ENTRY = re.compile(
    r"^(?P<host>[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)(:(?P<port>\d{1,5}))?$"
)
_RE_SCP_LIKE_GIT = re.compile(r"^[A-Za-z0-9_.-]+@(?P<host>[A-Za-z0-9.-]+):")


def ftSplitHostEntry(sEntry):
    """Return ``(sHost, iPort)`` for ``host`` or ``host:port``; raises ValueError.

    A raw address is refused here, as the proxy refuses it as a target:
    a name is what the allowlist judges.
    """
    matchEntry = _RE_HOST_ENTRY.match(str(sEntry or "").strip().lower())
    if not matchEntry:
        raise ValueError(f"not a host or host:port entry: {sEntry!r}")
    sHost = matchEntry.group("host")
    try:
        ipaddress.ip_address(sHost)
    except ValueError:
        pass
    else:
        raise ValueError(f"{sEntry!r} is a raw address; allow a host name")
    iPort = int(matchEntry.group("port") or I_DEFAULT_PORT)
    if not 0 < iPort < 65536:
        raise ValueError(f"port out of range in {sEntry!r}")
    return sHost, iPort


def fbIsAllowedHostEntry(sEntry):
    """True when the entry is a well-formed ``host`` or ``host:port``."""
    try:
        ftSplitHostEntry(sEntry)
    except ValueError:
        return False
    return True


def flistRepositoryHosts(listRepositories):
    """Return the host of every repository URL a project clones."""
    listHosts = []
    for dictRepository in listRepositories or []:
        sUrl = str((dictRepository or {}).get("url") or "")
        matchScpLike = _RE_SCP_LIKE_GIT.match(sUrl)
        sHost = (matchScpLike.group("host") if matchScpLike
                 else (urlsplit(sUrl).hostname or ""))
        if sHost and sHost.lower() not in listHosts:
            listHosts.append(sHost.lower())
    return listHosts


def _flistAgentHosts(dictFeatures):
    listHosts = []
    for sFeature, tHosts in DICT_AGENT_HOSTS.items():
        if dictFeatures.get(sFeature, False):
            listHosts.extend(tHosts)
    return listHosts


def _fbAnyAgentAutoUpdates(dictFeatures):
    return any(
        dictFeatures.get(sFeature, False) and dictFeatures.get(sFeature + "AutoUpdate", True)
        for sFeature in DICT_AGENT_HOSTS
    )


def ftComputeAllowlist(config):
    """Return ``(listHosts, listPorts)`` for a project, validated for the proxy."""
    # The feature flags as a mapping: a dataclass or a namespace alike.
    dictFeatures = dict(vars(getattr(config, "features", None) or object()))
    setHosts = set(T_GITHUB_HOSTS) | set(T_PYPI_HOSTS)
    setPorts = {I_DEFAULT_PORT}
    setHosts.update(_flistAgentHosts(dictFeatures))
    if _fbAnyAgentAutoUpdates(dictFeatures):
        setHosts.update(T_NPM_HOSTS)
    if getattr(config, "sPackageManager", "pip") in ("conda", "mamba"):
        setHosts.update(T_CONDA_HOSTS)
    setHosts.update(flistRepositoryHosts(getattr(config, "listRepositories", [])))
    for sEntry in getattr(config, "listAllowedHosts", []) or []:
        sHost, iPort = ftSplitHostEntry(sEntry)
        setHosts.add(sHost)
        setPorts.add(iPort)
    listHosts, listPorts = sorted(setHosts), sorted(setPorts)
    fnValidateAllowlistOrRaise(listHosts, listPorts, {})
    return listHosts, listPorts
