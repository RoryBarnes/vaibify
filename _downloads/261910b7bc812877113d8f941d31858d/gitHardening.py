"""Shared git hardening config for every host-side git invocation.

Single source of truth for the ``-c`` flags that every host-side
``git`` call in vaibify must carry. Lifts what was previously
duplicated as ``LIST_GIT_HARDENING_CONFIG`` in ``gui.gitStatus``,
``_LIST_GIT_HARDENING_CONFIG`` in ``reproducibility.overleafMirror``,
and ``_LIST_GITHUB_HARDENING_CONFIG`` in ``gui.syncDispatcher`` into
one list so the flag set cannot drift per service.

Attacks these flags defend against:

- ``protocol.file.allow=never`` + ``protocol.allow=user`` reject
  ``file://`` transports (e.g. in a hostile ``.gitmodules``).
- ``core.symlinks=false`` prevents a checked-out symlink from
  redirecting a subsequent write outside the working tree.
- ``submodule.recurse=false`` disables implicit submodule recursion.

``reproducibility.overleafSync`` keeps its own local copy because it
is shipped into the container as a standalone script and cannot
import from the ``vaibify`` package; see that module's docstring.
"""

__all__ = [
    "LIST_GIT_CREDENTIAL_ISOLATION_CONFIG",
    "LIST_GIT_HARDENING_CONFIG",
]


LIST_GIT_HARDENING_CONFIG = [
    "-c", "protocol.file.allow=never",
    "-c", "protocol.allow=user",
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
