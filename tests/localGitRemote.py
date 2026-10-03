"""A real git push to a local bare repository, under the production flags.

The dispatcher's push command carries ``protocol.file.allow=never``,
which rightly refuses a plain-path remote, and these tests execute that
exact command against real git. They used to reach the bare repository
through git's ``ext::`` transport, which runs a command and was allowed
under the same policy -- that is, they leaned on the very hole the
hardening list now closes (``protocol.ext.allow=never``).

The remote is now an ``ssh://`` URL, a transport the production flags
allow, and the ssh command is a tiny script that runs the remote command
locally. Real git's ssh transport code runs end to end (pack
negotiation, ``receive-pack``, ref update); only the network hop is
replaced, so nothing touches the network and no flag is weakened.
"""

import os
import stat

S_FAKE_SSH_SCRIPT = """#!/bin/sh
# Stand-in for ssh: git passes the host, then the remote command.
shift
exec sh -c "$(printf '%s' "$1" | sed 's/^git-\\(receive-pack\\|upload-pack\\)/git \\1/')"
"""


def fsWriteFakeSsh(sDirectory):
    """Write the stand-in ssh script into ``sDirectory``; return its path."""
    sPath = os.path.join(str(sDirectory), "fakeSsh.sh")
    with open(sPath, "w") as fileScript:
        fileScript.write(S_FAKE_SSH_SCRIPT)
    os.chmod(sPath, os.stat(sPath).st_mode | stat.S_IXUSR)
    return sPath


def fsSshUrlForLocalRepository(sRepositoryPath):
    """Return the ``ssh://`` URL that the stand-in resolves to a path."""
    return "ssh://localhost" + os.path.abspath(sRepositoryPath)


def fnUseFakeSshForThisTest(monkeypatch, sDirectory):
    """Point git's ssh at the stand-in for the rest of the test.

    ``GIT_SSH_VARIANT=simple`` stops git probing the command with
    ``-G`` and makes it send exactly ``host command``.
    """
    monkeypatch.setenv("GIT_SSH_COMMAND", fsWriteFakeSsh(sDirectory))
    monkeypatch.setenv("GIT_SSH_VARIANT", "simple")
