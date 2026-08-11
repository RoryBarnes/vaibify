"""What the launch commands promise, and what they hand you.

Three defects met in one session, all of the same shape: the command
said one thing and did another, and the dashboard could not tell the
researcher which.

* ``vaibify gui`` documented "omit to show the landing page" and
  instead built the SINGLE-PROJECT viewer with a ``/workspace``
  workspace root — a container path, on a laptop.
* On the way there it printed "Multiple projects found. Specify one
  with --project/-p:", which is a message written to ABORT: the
  resolver called ``sys.exit(1)`` and a caller caught the
  ``SystemExit`` and carried on. So a fatal error printed as noise
  before a successful start.
* Both launchers echoed a bare address. The dashboard signs in only by
  redeeming a one-time capability carried in the URL FRAGMENT, which
  is deliberately never printed — so the address on screen was the one
  string that could NOT get you in, and using it produced a dashboard
  that 401'd every call and spun forever.

The last one is why these are tested together rather than filed as
copy: the three compose into "vaibify is hanging", which is what it
looked like from the outside and is not what was happening.
"""

import pytest
from click.testing import CliRunner
from unittest.mock import patch

from vaibify.cli import main as moduleMain


@pytest.mark.falsification
def testGuiWithNoProjectLaunchesTheHubNotTheSingleProjectViewer():
    """The landing page has ONE implementation, and this is it.

    Kills: building the single-project application for a bare ``gui``,
    which is what shipped — with a container path as its workspace
    root, and a swallowed ``SystemExit`` printed on the way.
    """
    listLaunched = []
    with patch.object(
        moduleMain, "fnLaunchHub",
        lambda iPort: listLaunched.append(iPort),
    ), patch.object(
        moduleMain, "fconfigResolveProject",
        _fnFailIfCalled,
    ):
        result = CliRunner().invoke(moduleMain.fnGuiCommand, [])
    assert result.exit_code == 0, result.output
    assert listLaunched == [None], (
        "a bare 'gui' did not launch the hub; it built something else"
    )


def _fnFailIfCalled(*tArguments, **dictKeywords):
    """Stand in for a resolver a bare ``gui`` must never consult."""
    del tArguments, dictKeywords
    raise AssertionError(
        "a bare 'gui' resolved a project; the landing page is "
        "project-agnostic and must not need one"
    )


@pytest.mark.falsification
def testANamedProjectThatCannotResolveStopsTheCommand():
    """A resolver's ``sys.exit`` must end the command, not decorate it.

    ``_ftResolveGuiConfig`` caught ``SystemExit`` and continued, so the
    "Multiple projects found" message — written to abort — printed
    immediately before "Starting…". A researcher reasonably read the
    combination as a warning rather than as the failure it was.

    Kills: catching the resolver's exit and starting anyway.
    """
    def fnResolveExits(sProjectName):
        del sProjectName
        raise SystemExit(1)

    with patch.object(
        moduleMain, "fconfigResolveProject", fnResolveExits,
    ), patch.object(
        moduleMain, "_fnAnnounceAndOpen", _fnFailIfCalled,
    ):
        result = CliRunner().invoke(
            moduleMain.fnGuiCommand, ["--project", "whicheverOne"],
        )
    assert result.exit_code != 0, (
        "a project that could not be resolved still started a server: "
        + result.output
    )
    # The TYPE is the assertion, not merely the nonzero exit. A command
    # that swallowed the resolver's exit and then tripped over the None
    # it kept would also exit nonzero, and would pass a test that only
    # checked the code -- which is exactly what happened when this was
    # first written, and the mutant survived. What is being pinned is
    # that the resolver's own exit is what ends the command.
    assert isinstance(result.exception, SystemExit), (
        "the command failed for a reason other than the resolver's "
        f"refusal ({type(result.exception).__name__}), which means it "
        "carried on past a fatal error and broke somewhere downstream"
    )


@pytest.mark.falsification
def testTheAnnouncementNeverOffersTheAddressAsTheWayIn():
    """The address and the usable link are different strings.

    The capability rides the fragment precisely so it stays out of
    access logs and terminal scrollback, so the printed address cannot
    sign in — and must not be presented as though it could. Asserted on
    BOTH halves: the address still appears, because knowing the port is
    genuinely useful, and the caveat appears with it.

    Kills: printing the bare address alone, which is what sent a
    researcher to a dashboard that refused every call.
    """
    listOpened = []
    with patch.object(
        moduleMain, "_fnOpenBrowserUnlessSuppressed",
        lambda sUrl: listOpened.append(sUrl),
    ), patch.object(
        moduleMain, "_fsLaunchUrlWithCapability",
        lambda sUrl, app: sUrl + "/#bootstrap=theCapability",
    ):
        runner = CliRunner()
        with runner.isolation() as tStreams:
            moduleMain._fnAnnounceAndOpen(
                "http://127.0.0.1:8050", object(), "vaibify",
            )
            sOutput = tStreams[0].getvalue().decode("utf-8")

    assert "http://127.0.0.1:8050" in sOutput, sOutput
    assert "cannot sign in" in sOutput, (
        f"the address was offered with no caveat: {sOutput}"
    )
    assert "bootstrap=" not in sOutput, (
        "the capability was echoed to the terminal, which is what "
        f"keeping it in the fragment exists to prevent: {sOutput}"
    )
    assert listOpened == ["http://127.0.0.1:8050/#bootstrap=theCapability"], (
        "the browser was opened without the capability, so the tab it "
        "opens cannot sign in either"
    )
