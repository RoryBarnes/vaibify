"""The container image's shells must run in a UTF-8 locale.

The dashboard terminal is a UTF-8 transport end to end: xterm.js
decodes the bytes the hub relays unchanged from the pty. A shell whose
locale is POSIX sees the same bytes as single-column characters, so
bash's line editor counts a three-byte curly quote as three columns
where the browser paints one. Its cursor model then disagrees with the
screen by rows, not just columns, and a pasted line that wraps is drawn
one row off, with history recall painting over the rows above it.

Measured 2026-09-13 against the real image, a real docker exec through
the hub's own API calls, and the shipped xterm.js build: POSIX locale
plus one pasted curly quote garbled the pane; C.UTF-8 with the same
paste rendered correctly; POSIX with a pure-ASCII paste rendered
correctly. The pty width equalled xterm's columns throughout, so the
width was never the cause.

The oracle here is independent of the Dockerfile: the transport's
encoding is fixed by xterm.js, which decodes UTF-8 and nothing else.
"""

import os
import re

import pytest


_S_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_S_DOCKERFILE = os.path.join(
    _S_REPO_ROOT, "vaibify", "containerImage", "Dockerfile",
)

_REGEX_ENV_ASSIGNMENT = re.compile(
    r'^\s*ENV\s+([A-Za-z_][A-Za-z0-9_]*)[=\s]\s*"?([^"\s]*)"?',
)


def _fdictCollectImageEnvironment(sDockerfileText):
    """Return the ENV assignments of a Dockerfile, last one winning."""
    dictEnvironment = {}
    for sLine in sDockerfileText.splitlines():
        matchAssignment = _REGEX_ENV_ASSIGNMENT.match(sLine)
        if matchAssignment:
            dictEnvironment[matchAssignment.group(1)] = (
                matchAssignment.group(2)
            )
    return dictEnvironment


def _fbNamesUtfEightCharset(sLocale):
    """True when a locale value such as ``C.UTF-8`` selects UTF-8."""
    return bool(re.search(r"utf-?8", sLocale, re.IGNORECASE))


@pytest.mark.falsification
def test_the_image_gives_every_shell_a_utf_eight_locale():
    """LANG in the image must name a UTF-8 locale.

    Kills: Delete the ``ENV LANG=C.UTF-8`` line from the Dockerfile,
    which returns the image to the POSIX locale under which a pasted
    curly quote garbles the terminal pane.
    """
    with open(_S_DOCKERFILE, "r") as fileDockerfile:
        dictEnvironment = _fdictCollectImageEnvironment(
            fileDockerfile.read(),
        )
    sLocale = dictEnvironment.get("LC_ALL") or dictEnvironment.get("LANG", "")
    assert _fbNamesUtfEightCharset(sLocale), (
        "The image declares no UTF-8 locale (LANG=%r). The dashboard "
        "terminal relays UTF-8 bytes unchanged, and a POSIX-locale bash "
        "miscounts every multibyte character, so wrapped input lines are "
        "redrawn on the wrong row." % sLocale
    )


def test_the_environment_parser_takes_the_last_assignment():
    """A later ENV overrides an earlier one, as Docker itself resolves."""
    dictEnvironment = _fdictCollectImageEnvironment(
        'ENV LANG=POSIX\nRUN true\nENV LANG="C.UTF-8"\nENV PATH /bin\n',
    )
    assert dictEnvironment == {"LANG": "C.UTF-8", "PATH": "/bin"}


def test_a_charset_check_reads_only_the_charset():
    """Any locale naming UTF-8 passes; a bare POSIX or C value fails."""
    assert _fbNamesUtfEightCharset("C.UTF-8")
    assert _fbNamesUtfEightCharset("en_US.utf8")
    assert not _fbNamesUtfEightCharset("POSIX")
    assert not _fbNamesUtfEightCharset("")
