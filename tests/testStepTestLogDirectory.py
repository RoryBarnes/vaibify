"""A step's test log is written even where ``.vaibify/logs`` does not exist yet.

``.vaibify/logs/`` is gitignored, so a fresh clone has none, and neither
does the shadow copy a Level 3 verification exports -- that copy carries
only what git enumerates. The pipeline runner creates the directory for
its own run log, but under the workflow's ``sProjectRepoPath``; on the
dashboard's shadow lane that path names the LIVE repository, while the
step tests run (and log) under the shadow copy's root. Every step's test
log in a reader's verification was therefore lost behind a "No such file
or directory" warning (2026-10-12).

The connection below runs the writer's real shell command against a
real directory, so the assertion is about a file on disk, not about the
text of a command.
"""

import pytest

from tests.testRerunVerifiesWhatItRan import LocalShellContainer
from vaibify.gui.pipelineTestRunner import _fnWriteTestLog


def _fnRunAsync(coroutine):
    import asyncio
    return asyncio.run(coroutine)


@pytest.mark.falsification
def test_a_step_test_log_lands_in_a_clone_with_no_logs_directory(tmp_path):
    """The test log is written into a repository that has no logs directory.

    Kills: the test-log writer composing the logs path without
    creating the directory, so the append fails and the log is lost.
    """
    pathRepository = tmp_path / "shadowCopy" / "projectRepo"
    pathRepository.mkdir(parents=True)
    assert not (pathRepository / ".vaibify").exists()

    _fnRunAsync(_fnWriteTestLog(
        LocalShellContainer(), "shadowContainerName", 1,
        ["first line of test output", "second line"], str(pathRepository),
    ))

    listLogs = sorted((pathRepository / ".vaibify" / "logs").glob(
        "test_Step01_*.log"))
    assert len(listLogs) == 1, (
        "the step's test log was not written: the writer assumed a "
        "logs directory that a fresh clone and a shadow copy lack"
    )
    assert listLogs[0].read_text() == (
        "first line of test output\nsecond line\n"
    )
