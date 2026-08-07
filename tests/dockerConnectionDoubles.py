"""Doubles for the ``DockerConnection`` contract that answer honestly.

``ContainerRepoFiles`` reaches its connection for two BOOLEAN typed
reads, ``fbContainerPathIsFile`` and ``fbContainerPathIsDirectory``.
A bare ``MagicMock()`` answers both with a truthy ``MagicMock``, so
every path in the container appears to exist and the level gates above
go on to parse files that are not there — the failure surfaces far from
its cause, as a ``TypeError`` about JSON and a ``MagicMock``.

That is a new hazard: until the existence probes became typed reads
they were ``test -f`` through the general exec primitive, and a
``MagicMock`` connection answered them with a non-zero exit code, so
the same double reported every path ABSENT. The polarity flipped
silently for every test that passed a bare mock.
"""

from unittest.mock import MagicMock


def fconnectionDoubleWithNoContainerPaths():
    """Return a mock connection reporting every container path absent.

    Matches what a bare ``MagicMock()`` used to answer through the old
    ``test -f`` implementation, so a test written against that keeps
    asserting the same thing. Set the two return values explicitly when
    a test needs a path to exist — do not leave them as mocks.
    """
    connectionDocker = MagicMock()
    connectionDocker.fbContainerPathIsFile.return_value = False
    connectionDocker.fbContainerPathIsDirectory.return_value = False
    return connectionDocker
