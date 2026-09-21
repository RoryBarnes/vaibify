"""`vaibify build` and the dashboard run the same configuration checks.

Two lanes build an image, and a check added to one is a check the
other's researcher does not get. The set is named once in the CLI and
the route's table is held to it here; the refusal codes are pinned
too, because the dashboard reads a bare 409 from the build route as
"a build is already running" and would attach to a refusal that
carried none.
"""

import pytest

from vaibify.cli import commandBuild
from vaibify.gui import buildRoutes


def _fsetRoutePreflights():
    return {fnCheck for fnCheck, _ in buildRoutes._flistConfigurationPreflights()}


@pytest.mark.falsification
def test_both_build_lanes_run_the_same_configuration_checks():
    """Kills: dropping a check from the route's table while the CLI
    keeps it, which is how a dashboard build stops asking a question
    the command line still asks."""
    assert _fsetRoutePreflights() == set(
        commandBuild.T_CONFIGURATION_PREFLIGHTS
    )


def test_every_configuration_check_carries_a_distinct_refusal_code():
    listCodes = [
        sRefusal for _, sRefusal in buildRoutes._flistConfigurationPreflights()
    ]
    assert all(listCodes), "a refusal with no code renders as a running build"
    assert len(set(listCodes)) == len(listCodes)


def test_the_host_scoped_disk_check_is_not_in_the_configuration_table():
    """It answers for the machine, not the project, and runs whether or
    not the project's own file can be read."""
    from vaibify.cli.daemonDiskPreflight import fpreflightDaemonFreeDisk
    assert fpreflightDaemonFreeDisk not in _fsetRoutePreflights()
    assert fpreflightDaemonFreeDisk not in commandBuild.T_CONFIGURATION_PREFLIGHTS
