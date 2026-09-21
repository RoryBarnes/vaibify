"""An overlay's failure banner points at the step's own message first.

The Claude overlay's banner said "likely a TLS or network failure" and
"resolve your build host's network path" for ANY non-zero exit of the
installer. The exit it was shown for was ``mkdir: No space left on
device`` (a live build, 2026-09-21): the daemon's disk was full, and
the banner sent the researcher to debug a network that was fine. A
banner may name workarounds; it may not assert a cause the step did
not observe.
"""

import pathlib
import re

import pytest

from vaibify import resources


_PATH_IMAGE_ROOT = pathlib.Path(str(resources.fpathContainerImageRoot()))
_LIST_OVERLAYS = sorted(_PATH_IMAGE_ROOT.glob("Dockerfile.*"))
_T_ASSERTED_CAUSES = (
    "likely a TLS or network",
    "likely a network",
    "Resolve your build host's network path",
    "Resolve this build host's network path",
)


def test_there_are_overlays_to_check():
    assert _LIST_OVERLAYS, "no overlay Dockerfiles found; the guard checks nothing"


@pytest.mark.falsification
def test_no_overlay_banner_asserts_a_network_cause_for_any_exit():
    """One node for every overlay, so the registry names one mutation.

    Kills: restoring "likely a TLS or network failure" to the Claude
    overlay's installer banner, the sentence that blamed the network
    for a full disk.
    """
    listOffenders = [
        f"{pathOverlay.name}: {sCause!r}"
        for pathOverlay in _LIST_OVERLAYS
        for sCause in _T_ASSERTED_CAUSES
        if sCause in pathOverlay.read_text(encoding="utf-8")
    ]
    assert listOffenders == [], (
        "these overlay banners assert a cause their step did not observe: "
        f"{listOffenders}"
    )


@pytest.mark.parametrize(
    "pathOverlay", _LIST_OVERLAYS, ids=[p.name for p in _LIST_OVERLAYS],
)
def test_a_banner_that_mentions_the_network_conditions_it_on_the_step(
    pathOverlay,
):
    """The workaround may name the network, but only as "if the step's
    message names it": the researcher reads the step first."""
    sText = pathOverlay.read_text(encoding="utf-8")
    for matchLine in re.finditer(r"printf '%s\\n' \"([^\"]*network[^\"]*)\"", sText):
        sLine = matchLine.group(1)
        assert "names the network" in sLine, (
            f"{pathOverlay.name} names the network without conditioning it "
            f"on the step's own message: {sLine!r}"
        )
