"""Judging whether a stranded container entry can go back to host mode.

vaibify 0.1.0 converted a host project the moment the researcher
pressed Convert and obtain: the registry entry became a container, the
clone's ``vaibify.yml`` gained the runtime fields, and only then did the
download start. When the download failed, nothing turned the entry
back. The route that does so now asks three questions here: is this
entry stranded (it obtains its image, nothing admits a start, and the
daemon holds no image for it); is the committed ``vaibify.yml`` on
hand to restore; and are the conversion's own edits the ONLY difference
between the working file and the committed one. The last decides
whether the file is restored silently or shown to the researcher
first, which is the ruling of 2026-10-13.
"""

__all__ = [
    "HostModeReturnRefusedError",
    "fbOnlyTheConversionsEdits",
    "fdictJudgeReturnToHostMode",
    "fnRestoreCommittedConfig",
]

import os

from vaibify.config.durableFile import fnWriteFileDurably


class HostModeReturnRefusedError(Exception):
    """This entry is not one that can be returned to host mode."""


def fdictJudgeReturnToHostMode(dictProject, connectionDocker):
    """Return the two configurations and whether only the conversion edited them.

    Raises :class:`HostModeReturnRefusedError` for an entry that is not
    stranded: one that builds its image, one admitted by an origin
    record, one whose image the daemon holds, or one whose committed
    configuration cannot be read.
    """
    import yaml
    from vaibify.config.imageOrigins import fdictReadOriginRecord
    from vaibify.config.registryManager import fbProjectImageIsObtained
    from vaibify.reproducibility.reproductionSource import fsReadCommittedFileOrNone
    sName = dictProject.get("sName") or ""
    if dictProject.get("sMode", "container") != "container" or (
        not fbProjectImageIsObtained(dictProject)
    ):
        raise HostModeReturnRefusedError(
            f"'{sName}' is not a container that obtains the author's image, "
            "so there is nothing to return from."
        )
    if fdictReadOriginRecord(sName) is not None:
        raise HostModeReturnRefusedError(
            f"'{sName}' obtained its image already; start it, or choose "
            "Re-obtain the pinned image from its tile."
        )
    _fnRefuseWhileAnImageExists(sName, connectionDocker)
    sRelativePath = os.path.relpath(dictProject["sConfigPath"], dictProject["sDirectory"])
    sCommitted = (
        None if sRelativePath.startswith(os.pardir)
        else fsReadCommittedFileOrNone(dictProject["sDirectory"], sRelativePath)
    )
    if sCommitted is None:
        raise HostModeReturnRefusedError(
            "The author's vaibify.yml is not committed in this clone, so it "
            "cannot be restored automatically. Restore the file yourself "
            "(for example `git checkout -- vaibify.yml`), then try again."
        )
    with open(dictProject["sConfigPath"], "r", encoding="utf-8") as fileConfig:
        sWorking = fileConfig.read()
    try:
        dictWorking = yaml.safe_load(sWorking) or {}
        dictCommitted = yaml.safe_load(sCommitted) or {}
    except yaml.YAMLError:
        dictWorking, dictCommitted = {}, None
    return {
        "sWorkingConfig": sWorking,
        "sCommittedConfig": sCommitted,
        "bOnlyTheConversionsEdits": fbOnlyTheConversionsEdits(dictWorking, dictCommitted),
    }


def _fnRefuseWhileAnImageExists(sName, connectionDocker):
    """An entry whose image the daemon holds is not stranded; it can start."""
    from vaibify.config.connectionAvailability import fbDockerReachable
    from vaibify.docker import disposableContainer
    if not fbDockerReachable(connectionDocker):
        raise HostModeReturnRefusedError(
            "Docker could not be asked whether an image exists for "
            f"'{sName}'. Start Docker, then try again."
        )
    if disposableContainer.fdictInspectImage(
        disposableContainer.fdockerCreateDisposableClient(), f"{sName}:latest",
    ) is not None:
        raise HostModeReturnRefusedError(
            f"An image exists for '{sName}', so it is not stranded; start "
            "it, or choose Re-obtain the pinned image from its tile."
        )


def fbOnlyTheConversionsEdits(dictWorking, dictCommitted):
    """True when the working file differs from the committed one only in what a conversion writes.

    A conversion writes the runtime keys and the agents' feature
    switches (install and auto-update) and nothing else, so stripping
    those from both files must leave them equal. An unparseable
    committed file can be compared with nothing and answers False.
    """
    from vaibify.docker.imageBuilder import T_AGENT_OVERLAY_NAMES
    from vaibify.gui.pinnedEnvironmentConversion import T_RUNTIME_YAML_KEYS
    if not isinstance(dictCommitted, dict) or not isinstance(dictWorking, dict):
        return False
    setAgentKeys = set(T_AGENT_OVERLAY_NAMES) | {
        f"{sAgent}AutoUpdate" for sAgent in T_AGENT_OVERLAY_NAMES
    }

    def fdictStripped(dictConfig):
        dictStripped = {
            sKey: jsonValue for sKey, jsonValue in dictConfig.items()
            if sKey not in T_RUNTIME_YAML_KEYS and sKey != "features"
        }
        dictFeatures = dictConfig.get("features") or {}
        dictStripped["features"] = {
            sKey: jsonValue for sKey, jsonValue in dictFeatures.items()
            if sKey not in setAgentKeys
        } if isinstance(dictFeatures, dict) else dictFeatures
        return dictStripped

    return fdictStripped(dictWorking) == fdictStripped(dictCommitted)


def fnRestoreCommittedConfig(dictProject, sCommittedConfig):
    """Write the committed bytes back over the working file, durably."""
    fnWriteFileDurably(
        dictProject["sConfigPath"], sCommittedConfig.encode("utf-8"), iMode=0o644,
    )
