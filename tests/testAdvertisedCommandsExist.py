"""Every `vaibify <command>` the product tells a user to run must exist.

The container disk banner told researchers to "Run `vaibify clean`",
a command that was never registered, so the one remedy it offered
failed at the terminal. A remedy that names a missing command reads as
help and delivers an error. This test checks every backticked
`vaibify <command>` in the dashboard and the Python sources against the
commands the CLI actually registers.
"""

import pathlib
import re

from vaibify.cli.main import main as groupCli


PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PAT_ADVERTISED_COMMAND = re.compile(r"`vaibify ([a-z][a-z-]*)")
T_SCANNED_SUFFIXES = (".py", ".js", ".html")


def _flistProductFiles():
    """Return the product sources whose text can reach a user."""
    pathProduct = PATH_REPOSITORY / "vaibify"
    return sorted(
        pathFile for pathFile in pathProduct.rglob("*")
        if pathFile.suffix in T_SCANNED_SUFFIXES and pathFile.is_file()
    )


def testEveryAdvertisedCommandIsRegistered():
    setRegistered = set(groupCli.commands)
    listMissing = []
    for pathFile in _flistProductFiles():
        sText = pathFile.read_text(encoding="utf-8", errors="ignore")
        for matchCommand in PAT_ADVERTISED_COMMAND.finditer(sText):
            if matchCommand.group(1) not in setRegistered:
                listMissing.append(
                    f"{pathFile.relative_to(PATH_REPOSITORY)}: "
                    f"vaibify {matchCommand.group(1)}"
                )
    assert listMissing == [], (
        "These messages tell the user to run a vaibify command that "
        "does not exist: " + "; ".join(listMissing)
    )
