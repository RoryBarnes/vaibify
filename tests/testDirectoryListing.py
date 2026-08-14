"""The directory listing behind the Files tab and the Logs list.

It used to be one ``find -maxdepth 1 -printf '%y %p'`` piped to
``sort``. ``-printf`` is a GNU findutils extension that BSD find
rejects with "unknown primary or operator", so on a macOS host project
the command produced nothing -- and because it redirected stderr away
and discarded the exit code, "the listing failed" and "the directory is
empty" arrived as the same answer. A populated project rendered as
"Empty directory".

Both halves are tested here, and both are mode-symmetric: the host leg
against a REAL directory on this machine, the container leg against a
double whose arbitrary-exec primitive RAISES. The exec assertion is
what makes the kill portable -- a test that merely listed a directory
would pass on a Linux runner with GNU find whether or not the fix is
present, which is the platform-dependent green this repo has paid for
before.
"""

import os

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui.pipelineServer import flistQueryDirectory
from vaibify.host import hostScratch
from vaibify.host.hostConnection import HostConnection


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndScratch(tmp_path, monkeypatch):
    """Redirect the journal, locks, and scratch roots to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"),
    )


@pytest.fixture()
def tProjectAndConnection(tmp_path):
    """Return (sProjectRoot, HostConnection) over a temp project."""
    sProjectRoot = str(tmp_path / "project")
    os.makedirs(sProjectRoot)
    connection = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sProjectRoot,
    )
    return sProjectRoot, connection


class ConnectionRefusingEveryCommand:
    """A container leg that answers the two typed reads and nothing else.

    ``ftResultExecuteCommand`` raises rather than returning, because
    the claim under test is that a directory listing composes no
    command at all. A double that answered it would let the pre-fix
    implementation pass and report coverage for the defect it missed.
    """

    def __init__(self, dictEntriesByDirectory, setDirectoryPaths):
        self.dictEntriesByDirectory = dictEntriesByDirectory
        self.setDirectoryPaths = setDirectoryPaths

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        raise AssertionError(
            f"a directory listing ran a command: {sCommand!r}"
        )

    def flistDirectoryEntries(self, sContainerId, sDirectoryPath):
        if sDirectoryPath not in self.dictEntriesByDirectory:
            raise FileNotFoundError(sDirectoryPath)
        return sorted(self.dictEntriesByDirectory[sDirectoryPath])

    def flistContainerDirectoriesExist(self, sContainerId, listPaths):
        return [sPath in self.setDirectoryPaths for sPath in listPaths]


class TestTheHostLegListsARealDirectory:

    def testAHostProjectListsItsOwnFilesAndSubdirectories(
        self, tProjectAndConnection,
    ):
        """The defect verbatim: a populated host project is not empty."""
        sProjectRoot, connection = tProjectAndConnection
        os.makedirs(os.path.join(sProjectRoot, "QuickCheck"))
        with open(
            os.path.join(sProjectRoot, "vaibify.yml"), "w",
            encoding="utf-8",
        ) as fileConfig:
            fileConfig.write("name: trial\n")

        listEntries = flistQueryDirectory(
            connection, "host-listing-proj", sProjectRoot,
        )

        dictByName = {
            dictEntry["sName"]: dictEntry for dictEntry in listEntries
        }
        assert set(dictByName) == {"QuickCheck", "vaibify.yml"}
        assert dictByName["QuickCheck"]["bIsDirectory"] is True
        assert dictByName["vaibify.yml"]["bIsDirectory"] is False
        assert dictByName["vaibify.yml"]["sPath"] == os.path.join(
            sProjectRoot, "vaibify.yml",
        )

    def testANameCarryingASpaceSurvivesTheListing(
        self, tProjectAndConnection,
    ):
        """No shell means no word splitting to get wrong."""
        sProjectRoot, connection = tProjectAndConnection
        os.makedirs(os.path.join(sProjectRoot, "MCMC 512 Chains"))

        listEntries = flistQueryDirectory(
            connection, "host-listing-proj", sProjectRoot,
        )

        assert [dictEntry["sName"] for dictEntry in listEntries] == [
            "MCMC 512 Chains",
        ]
        assert listEntries[0]["bIsDirectory"] is True

    def testAnEmptyHostDirectoryIsStillEmpty(
        self, tProjectAndConnection,
    ):
        """The answer the failure case must stay distinguishable from."""
        sProjectRoot, connection = tProjectAndConnection
        os.makedirs(os.path.join(sProjectRoot, "Nothing"))

        assert flistQueryDirectory(
            connection, "host-listing-proj",
            os.path.join(sProjectRoot, "Nothing"),
        ) == []

    @pytest.mark.falsification
    def testAnUnreadableHostDirectoryRaisesRatherThanReadingEmpty(
        self, tProjectAndConnection,
    ):
        """Kills: a listing failure that answers with an empty list.

        This is the half of the defect that outlived the GNU-only
        primary. Emptiness is a claim about the researcher's project;
        a failed read is a claim about vaibify. They must not be the
        same value, on either leg.
        """
        sProjectRoot, connection = tProjectAndConnection

        with pytest.raises(FileNotFoundError):
            flistQueryDirectory(
                connection, "host-listing-proj",
                os.path.join(sProjectRoot, "NeverCreated"),
            )


class TestTheContainerLegComposesNoCommand:

    @pytest.mark.falsification
    def testAListingReachesNoArbitraryExecutionPrimitive(self):
        """Kills: composing ``find``/``ls`` text and running it.

        The container leg is the direction that always worked, so it
        is the direction whose regression would be invisible: GNU find
        is present in the image and on every Linux runner. Asserting
        the PRIMITIVE rather than the answer is what makes this kill
        the same on both platforms.
        """
        connection = ConnectionRefusingEveryCommand(
            {"/workspace/repo": ["Plot", "numbers.json"]},
            {"/workspace/repo/Plot"},
        )

        listEntries = flistQueryDirectory(
            connection, "container-abc", "/workspace/repo",
        )

        assert listEntries == [
            {
                "sName": "Plot",
                "sPath": "/workspace/repo/Plot",
                "bIsDirectory": True,
            },
            {
                "sName": "numbers.json",
                "sPath": "/workspace/repo/numbers.json",
                "bIsDirectory": False,
            },
        ]

    def testAMissingContainerDirectoryRaisesToo(self):
        """The mode-symmetric half of the raise-do-not-shrug rule."""
        connection = ConnectionRefusingEveryCommand({}, set())

        with pytest.raises(FileNotFoundError):
            flistQueryDirectory(
                connection, "container-abc", "/workspace/gone",
            )
