"""What an environment's NAME owns, in one list deletion and rename share.

An environment's name keys everything vaibify keeps for it: the Docker
container, two named volumes, every image tag in the repository named
after it, and a handful of host files under ``~/.vaibify``. Deleting an
environment and renaming one must agree on that set exactly -- a rename
that forgot a file would leave it keyed by a name nothing answers to
any more, and a deletion that forgot one would leave it behind -- so
the set is named here, once.

The container is named by the environment's ``sContainerName`` and the
image tags by ``imageBuilder.flistProjectImageReferences``, which
already answer for the daemon; this module names the volumes and the
host files, whose rules live in their own modules and are only
COLLECTED here.
"""

__all__ = [
    "flistHostFilesOwnedBy",
    "flistVolumeNamesOwnedBy",
]

from vaibify.cli.commandBuild import fsBuildArgHashPathFor
from vaibify.config import abandonmentAudit, operationJournal
from vaibify.config.containerConfig import fsRepositoryListPathFor
from vaibify.config.imageOrigins import fsOriginRecordPathFor
from vaibify.config.keepAliveManager import fsPidFilePathFor
from vaibify.docker import volumeManager


# (what the file is, how its path is derived from the name)
_T_HOST_FILE_KINDS = (
    ("operation journal", operationJournal.fsJournalPathFor),
    ("abandonment audit", abandonmentAudit.fsAbandonmentAuditPathFor),
    ("image origin record", fsOriginRecordPathFor),
    ("repository list", fsRepositoryListPathFor),
    ("build-argument hash", fsBuildArgHashPathFor),
    ("keep-alive record", fsPidFilePathFor),
)


def flistVolumeNamesOwnedBy(sName):
    """Return the names of the named volumes an environment owns."""
    return [
        volumeManager.fsWorkspaceVolumeNameForProject(sName),
        volumeManager.fsCredentialsVolumeNameForProject(sName),
    ]


def flistHostFilesOwnedBy(sName):
    """Return ``(sKind, sPath)`` for every host file the name keys.

    Paths are returned whether or not the file exists; the caller
    decides what absence means. A kind whose naming rule refuses this
    name (a repository list or an origin record for a host sandbox
    whose name carries a space) cannot exist for it and is left out.
    """
    listFiles = []
    for sKind, fsPathFor in _T_HOST_FILE_KINDS:
        try:
            listFiles.append((sKind, fsPathFor(sName)))
        except ValueError:
            continue
    return listFiles
