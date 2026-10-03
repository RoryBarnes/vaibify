"""Shared git hardening config for every host-side git invocation.

Single source of truth for the ``-c`` flags that every host-side
``git`` call in vaibify must carry. Lifts what was previously
duplicated as ``LIST_GIT_HARDENING_CONFIG`` in ``gui.gitStatus``,
``_LIST_GIT_HARDENING_CONFIG`` in ``reproducibility.overleafMirror``,
and ``_LIST_GITHUB_HARDENING_CONFIG`` in ``gui.syncDispatcher`` into
one list so the flag set cannot drift per service.

Attacks these flags defend against:

- ``protocol.file.allow=never`` rejects ``file://`` and plain-path
  transports (e.g. in a hostile ``.gitmodules``).
- ``protocol.ext.allow=never`` rejects the ``ext::`` transport, which
  runs an arbitrary command: ``git ls-remote 'ext::sh -c ...'``, or a
  repository's own ``url.<base>.insteadOf`` rewriting an ordinary URL
  to one. It is stated explicitly because ``protocol.allow=user`` sets
  the default policy for every protocol that has no policy of its own,
  and that default OVERRIDES git's built-in "never" for ``ext``
  (measured with git 2.50.1: with ``protocol.allow=user`` alone the
  command runs; an explicit ``protocol.ext.allow=never`` beats both
  that and a global ``protocol.allow=always``).
- ``protocol.allow=user`` keeps every other protocol from being used
  when git itself runs a nested fetch on the user's behalf
  (``GIT_PROTOCOL_FROM_USER=0``, as a submodule update does): measured,
  ``http`` is refused there under ``user`` and allowed under git's
  default.
- ``core.symlinks=false`` prevents a checked-out symlink from
  redirecting a subsequent write outside the working tree.
- ``submodule.recurse=false`` disables implicit submodule recursion.

``reproducibility.overleafSync`` keeps its own local copy because it
is shipped into the container as a standalone script and cannot
import from the ``vaibify`` package; see that module's docstring.

A call that asks a REMOTE a question and needs no repository at all
(``git ls-remote``) must not read one either: a repository's own
``.git/config`` can name an ssh command, a proxy command or a credential
helper, and git reads the repository it finds in the working directory.
:func:`fcontextOpenHermeticGitInvocation` supplies an empty working
directory and an environment that selects no repository.
"""

import contextlib
import os
import tempfile

__all__ = [
    "LIST_GIT_CREDENTIAL_ISOLATION_CONFIG",
    "LIST_GIT_HARDENING_CONFIG",
    "fcontextOpenHermeticGitInvocation",
    "fdictScrubRepositorySelection",
]


LIST_GIT_HARDENING_CONFIG = [
    "-c", "protocol.file.allow=never",
    "-c", "protocol.allow=user",
    "-c", "protocol.ext.allow=never",
    "-c", "core.symlinks=false",
    "-c", "submodule.recurse=false",
]


# Credential isolation for host-side git calls that authenticate with a
# vaibify-managed token. The empty value RESETS the credential-helper
# list inherited from the system/global gitconfig (e.g. macOS
# ``osxkeychain``), so only a helper configured AFTER this flag — or
# the ``GIT_ASKPASS`` script — can answer. Without it, an ambient
# keychain entry for the remote host silently masks the managed token:
# clones and verifies authenticate while the "connected?" probe of the
# managed slot honestly reports disconnected, and a live validation of
# a newly entered token validates the ambient credential instead of
# the token being stored.
#
# Deliberately NOT merged into ``LIST_GIT_HARDENING_CONFIG``: the
# container push composes its explicit credential-helper ``-c`` args
# BEFORE the hardening list, and ``-c`` flags apply in order — a reset
# appearing after the explicit helper would disable it. Prepend this
# list ahead of any explicit credential configuration.
LIST_GIT_CREDENTIAL_ISOLATION_CONFIG = [
    "-c", "credential.helper=",
]


# Environment variables that tell git WHICH repository to read, or inject
# configuration as if it were a ``-c`` flag. The researcher's own
# ``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` are deliberately kept: they
# name the config files the researcher chose, and dropping them would
# silently change which credentials and rewrites apply.
_SET_REPOSITORY_SELECTING_VARIABLES = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
})
_T_CONFIG_INJECTION_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def fdictScrubRepositorySelection(dictEnvironment):
    """Return a copy of ``dictEnvironment`` that selects no repository.

    Drops ``GIT_DIR`` and the other variables that point git at one, and
    the ones that inject configuration (``GIT_CONFIG_PARAMETERS``,
    ``GIT_CONFIG_COUNT`` with its ``KEY_n``/``VALUE_n`` pairs). A process
    started from a git hook or another tool inherits them, and git would
    then read a repository nobody chose for this call.
    """
    return {
        sKey: sValue for sKey, sValue in dictEnvironment.items()
        if sKey not in _SET_REPOSITORY_SELECTING_VARIABLES
        and not sKey.startswith(_T_CONFIG_INJECTION_PREFIXES)
    }


@contextlib.contextmanager
def fcontextOpenHermeticGitInvocation(dictEnvironment=None):
    """Yield ``(sWorkingDirectory, dictEnvironment)`` for a repository-free call.

    The directory is a fresh, empty, private one, so git has no
    repository to discover there, and ``GIT_CEILING_DIRECTORIES`` names
    its parent so a temporary directory that happens to sit inside a
    repository cannot be climbed out of into that repository's config.
    The environment is ``dictEnvironment`` (default: the process's own)
    with every repository-selecting variable removed. The directory is
    removed on exit.
    """
    dictBase = os.environ if dictEnvironment is None else dictEnvironment
    with tempfile.TemporaryDirectory(
        prefix="vaibifyGit",
    ) as sWorkingDirectory:
        dictScrubbed = fdictScrubRepositorySelection(dictBase)
        dictScrubbed["GIT_CEILING_DIRECTORIES"] = os.path.dirname(
            os.path.realpath(sWorkingDirectory),
        )
        yield sWorkingDirectory, dictScrubbed
