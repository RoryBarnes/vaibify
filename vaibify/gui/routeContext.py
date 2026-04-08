"""Typed wrapper for the route handler context dictionary.

Provides attribute access with clear types so that route handlers
can use ``dictCtx.docker`` instead of ``dictCtx["docker"]``, making
dependencies explicit and enabling IDE auto-completion.

The class also acts as a dict for backward compatibility — existing
code using ``dictCtx["key"]`` continues to work unchanged.
"""

__all__ = ["RouteContext"]


class RouteContext:
    """Typed context object passed to all route handlers.

    Wraps the underlying dict so both attribute access and dict
    access work identically.  New code should prefer attributes;
    old code using bracket notation keeps working.
    """

    def __init__(self, dictRaw):
        object.__setattr__(self, "_dictRaw", dictRaw)

    # --- typed attribute access ---

    @property
    def docker(self):
        """Docker connection for executing container commands."""
        return self._dictRaw["docker"]

    @property
    def workflows(self):
        """Dict of {sContainerId: dictWorkflow} cache."""
        return self._dictRaw["workflows"]

    @property
    def paths(self):
        """Dict of {sContainerId: sWorkflowPath} cache."""
        return self._dictRaw["paths"]

    @property
    def terminals(self):
        """Dict of {sSessionId: TerminalSession} cache."""
        return self._dictRaw["terminals"]

    @property
    def containerUsers(self):
        """Dict of {sContainerId: sUsername} cache."""
        return self._dictRaw["containerUsers"]

    @property
    def pipelineTasks(self):
        """Dict of {sContainerId: asyncio.Task} for running pipelines."""
        return self._dictRaw["pipelineTasks"]

    @property
    def sSessionToken(self):
        """Session token for WebSocket origin validation."""
        return self._dictRaw.get("sSessionToken", "")

    @property
    def setAllowedContainers(self):
        """Set of container IDs authorized for this session."""
        return self._dictRaw.get("setAllowedContainers", set())

    def require(self):
        """Raise if Docker is not available."""
        return self._dictRaw["require"]()

    def save(self, sContainerId, dictWorkflow):
        """Persist workflow to container."""
        return self._dictRaw["save"](sContainerId, dictWorkflow)

    def variables(self, sContainerId):
        """Build variable substitution dict for a container."""
        return self._dictRaw["variables"](sContainerId)

    def workflowDir(self, sContainerId):
        """Return the workflow directory path for a container."""
        return self._dictRaw["workflowDir"](sContainerId)

    # --- dict-compatible access for backward compatibility ---

    def __getitem__(self, sKey):
        return self._dictRaw[sKey]

    def __setitem__(self, sKey, value):
        self._dictRaw[sKey] = value

    def __contains__(self, sKey):
        return sKey in self._dictRaw

    def __delitem__(self, sKey):
        del self._dictRaw[sKey]

    def get(self, sKey, default=None):
        """Dict-compatible get with default."""
        return self._dictRaw.get(sKey, default)

    def setdefault(self, sKey, default=None):
        """Dict-compatible setdefault."""
        return self._dictRaw.setdefault(sKey, default)

    def pop(self, sKey, *args):
        """Dict-compatible pop."""
        return self._dictRaw.pop(sKey, *args)
