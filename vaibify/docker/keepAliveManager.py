"""Backward-compatible re-export shim.

The caffeinate keep-alive registry's canonical home is now
:mod:`vaibify.config.keepAliveManager`, sitting alongside the other
file-backed registries (container locks, session slots) so the shared
:mod:`vaibify.config.pidFileRegistry` mechanism and the uniform ``0o700``
directory permission apply to it too. This module re-exports the public
surface so any external importer of ``vaibify.docker.keepAliveManager``
keeps resolving.
"""

from vaibify.config.keepAliveManager import fnStartKeepAlive, fnStopKeepAlive


__all__ = ["fnStartKeepAlive", "fnStopKeepAlive"]
