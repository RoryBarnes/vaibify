"""Agent-action catalog for in-container agents.

Every researcher-initiated UI action that mutates state has exactly
one agent-facing name here. The in-container ``vaibify-do`` CLI reads
this catalog (serialized to JSON, shipped into the container at
connect time) and uses it to translate natural-language intent into
the HTTP or WebSocket call the UI itself would make.

Design notes:

- The authoritative list is :data:`LIST_AGENT_ACTIONS` below. Every
  state-mutating HTTP route should also be decorated with
  :func:`fnAgentAction` so the invariant test can pair them up and
  catch drift.
- WebSocket actions are dispatched via a switch in
  :func:`pipelineServer.fnDispatchAction`, so there is no per-handler
  function to decorate; they live in the static list only.
- The shared constants :data:`S_SESSION_ENV_PATH`,
  :data:`S_CATALOG_JSON_PATH`, and :data:`S_SESSION_HEADER_NAME` are
  consumed by both the backend (when writing into a container) and
  the ``vaibify-do`` CLI (when reading). Changing any of them is a
  breaking change that requires coordinated updates.
"""

__all__ = [
    "LIST_AGENT_ACTIONS",
    "SET_INTENTIONALLY_EXCLUDED_PATHS",
    "S_CATALOG_JSON_PATH",
    "S_CATALOG_SCHEMA_VERSION",
    "S_SESSION_ENV_PATH",
    "S_SESSION_HEADER_NAME",
    "fdictBuildCatalogJson",
    "fdictLookupAction",
    "fnAgentAction",
]


S_SESSION_ENV_PATH = "/tmp/vaibify-session.env"
S_CATALOG_JSON_PATH = "/tmp/vaibify-action-catalog.json"
S_SESSION_HEADER_NAME = "X-Vaibify-Session"
S_CATALOG_SCHEMA_VERSION = "1.0"


def fnAgentAction(sName):
    """Attach the agent-action name to an HTTP route handler.

    Usage::

        @fnAgentAction("run-step")
        @app.post("/api/steps/{sContainerId}/{iStepIndex}/run-tests")
        async def fnHandler(...): ...

    The decorator is metadata only — it does not alter the handler's
    behavior. The invariant test
    ``tests/testArchitecturalInvariants.py::testAgentActionRegistered``
    walks the FastAPI route registry and verifies that every
    state-mutating route either has this marker or is explicitly
    excluded via :data:`SET_INTENTIONALLY_EXCLUDED_PATHS`.
    """
    def _fnDecorator(fn):
        fn._sAgentActionName = sName
        return fn
    return _fnDecorator


LIST_AGENT_ACTIONS = [
    # ---- Execution (WebSocket except kill + acknowledge) ----
    {"sName": "run-all", "sCategory": "execution",
     "sMethod": "WS", "sPath": "runAll",
     "bAgentSafe": True,
     "sDescription": "Run every step in the active workflow in order."},
    {"sName": "force-run-all", "sCategory": "execution",
     "sMethod": "WS", "sPath": "forceRunAll",
     "bAgentSafe": True,
     "sDescription": "Run every step unconditionally, ignoring cache."},
    {"sName": "run-from-step", "sCategory": "execution",
     "sMethod": "WS", "sPath": "runFrom",
     "bAgentSafe": True,
     "sDescription": "Run from the given step index to the end. "
                     "Args: {iStartStep: int}."},
    {"sName": "run-selected-steps", "sCategory": "execution",
     "sMethod": "WS", "sPath": "runSelected",
     "bAgentSafe": True,
     "sDescription": "Run the listed step indices in order. "
                     "Args: {listStepIndices: [int, ...]}."},
    {"sName": "run-step", "sCategory": "execution",
     "sMethod": "WS", "sPath": "runSelected",
     "bAgentSafe": True,
     "sDescription": "Run a single step by name or 1-based index. "
                     "Alias for run-selected-steps with one entry."},
    {"sName": "verify-only", "sCategory": "execution",
     "sMethod": "WS", "sPath": "verify",
     "bAgentSafe": True,
     "sDescription": "Check outputs without executing step commands."},
    {"sName": "run-all-tests", "sCategory": "execution",
     "sMethod": "WS", "sPath": "runAllTests",
     "bAgentSafe": True,
     "sDescription": "Execute integrity, qualitative, and "
                     "quantitative tests for every step."},
    {"sName": "kill-pipeline", "sCategory": "execution",
     "sMethod": "POST", "sPath": "/api/pipeline/{sContainerId}/kill",
     "bAgentSafe": False,
     "sDescription": "Stop a running pipeline. User-only because "
                     "aborting work is a researcher decision."},
    {"sName": "acknowledge-step", "sCategory": "execution",
     "sMethod": "POST",
     "sPath": "/api/pipeline/{sContainerId}/acknowledge-step/{iStepIndex}",
     "bAgentSafe": True,
     "sDescription": "Update mtime baseline after confirming a step's "
                     "outputs look right."},
    # ---- Verification / judgment ----
    {"sName": "run-unit-tests", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}/run-tests",
     "bAgentSafe": True,
     "sDescription": "Run all test categories for one step."},
    {"sName": "run-test-category", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}/run-test-category",
     "bAgentSafe": True,
     "sDescription": "Run a single test suite. "
                     "Args: {sCategory: 'integrity'|'qualitative'|'quantitative'}."},
    {"sName": "save-and-run-test", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}/save-and-run-test",
     "bAgentSafe": True,
     "sDescription": "Write a test file and execute it. "
                     "Args: {sRelativePath, sContent}."},
    {"sName": "generate-tests", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}/generate-test",
     "bAgentSafe": False,
     "sDescription": "Have vaibify generate test boilerplate for the "
                     "step. User-only because the researcher should "
                     "review AI-generated test content."},
    {"sName": "delete-generated-tests", "sCategory": "verification",
     "sMethod": "DELETE",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}/generated-test",
     "bAgentSafe": False,
     "sDescription": "Remove an auto-generated test suite. "
                     "User-only: destructive."},
    {"sName": "accept-plots-as-standard", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}/standardize-plots",
     "bAgentSafe": False,
     "sDescription": "Promote the step's current plots to the "
                     "reference standards. User-only: requires "
                     "researcher judgment about scientific correctness."},
    {"sName": "compare-plot", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}/compare-plot",
     "bAgentSafe": True,
     "sDescription": "Prepare a plot/standard diff payload for one "
                     "figure. Read-only; returns data."},
    # ---- Workflow editing ----
    {"sName": "create-step", "sCategory": "workflow",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/create",
     "bAgentSafe": True,
     "sDescription": "Append a new step to the active workflow."},
    {"sName": "insert-step", "sCategory": "workflow",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/insert/{iPosition}",
     "bAgentSafe": True,
     "sDescription": "Insert a new step at the given 0-based position."},
    {"sName": "update-step", "sCategory": "workflow",
     "sMethod": "PUT",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}",
     "bAgentSafe": True,
     "sDescription": "Edit properties of an existing step. "
                     "Args: a partial step object."},
    {"sName": "delete-step", "sCategory": "workflow",
     "sMethod": "DELETE",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}",
     "bAgentSafe": False,
     "sDescription": "Remove a step from the workflow. User-only: "
                     "destructive to intent and invalidates downstream."},
    {"sName": "reorder-steps", "sCategory": "workflow",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/reorder",
     "bAgentSafe": False,
     "sDescription": "Move a step to a new position. User-only: "
                     "changes pipeline semantics."},
    # ---- Sync / git / external ----
    {"sName": "commit-canonical", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/git/{sContainerId}/commit-canonical",
     "bAgentSafe": True,
     "sDescription": "Stage and commit the vaibify canonical "
                     "state (workflow.json, markers). "
                     "Args: {sCommitMessage} optional."},
    {"sName": "push-to-github", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/github/{sContainerId}/push",
     "bAgentSafe": False,
     "sDescription": "Push staged changes to GitHub. User-only: "
                     "publishing to a remote is the researcher's call."},
    {"sName": "add-file-to-github", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/github/{sContainerId}/add-file",
     "bAgentSafe": False,
     "sDescription": "Commit a single data file to GitHub. "
                     "User-only: same reasoning as push-to-github."},
    {"sName": "push-to-overleaf", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/overleaf/{sContainerId}/push",
     "bAgentSafe": False,
     "sDescription": "Push plots or tables to the Overleaf project. "
                     "User-only: externally visible."},
    {"sName": "refresh-overleaf-mirror", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/overleaf/{sContainerId}/mirror/refresh",
     "bAgentSafe": True,
     "sDescription": "Fetch the current Overleaf project state into "
                     "the local mirror. Read-side."},
    {"sName": "delete-overleaf-mirror", "sCategory": "sync",
     "sMethod": "DELETE",
     "sPath": "/api/overleaf/{sContainerId}/mirror",
     "bAgentSafe": False,
     "sDescription": "Remove the cached Overleaf mirror. User-only: "
                     "destructive."},
    {"sName": "publish-to-zenodo", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/zenodo/{sContainerId}/archive",
     "bAgentSafe": False,
     "sDescription": "Archive outputs to Zenodo. User-only: a Zenodo "
                     "DOI is a public research artifact."},
    {"sName": "set-zenodo-metadata", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/zenodo/{sContainerId}/metadata",
     "bAgentSafe": False,
     "sDescription": "Set Zenodo title, creators, license, keywords. "
                     "User-only: researcher authorship / licensing."},
    {"sName": "download-zenodo-dataset", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/zenodo/{sContainerId}/download",
     "bAgentSafe": True,
     "sDescription": "Pull a dataset from a Zenodo record. "
                     "Args: {sRecordId, sFileName, sDestination}."},
    # ---- Files ----
    {"sName": "pull-file", "sCategory": "files",
     "sMethod": "POST",
     "sPath": "/api/files/{sContainerId}/pull",
     "bAgentSafe": True,
     "sDescription": "Copy a file from the container to the host."},
    {"sName": "upload-file", "sCategory": "files",
     "sMethod": "POST",
     "sPath": "/api/files/{sContainerId}/upload",
     "bAgentSafe": True,
     "sDescription": "Upload a file into the container. "
                     "Args: {sFileName, sBase64Content, sDestination}."},
    {"sName": "write-file", "sCategory": "files",
     "sMethod": "PUT",
     "sPath": "/api/file/{sContainerId}/{sFilePath:path}",
     "bAgentSafe": True,
     "sDescription": "Write text content to a file inside the container."},
    {"sName": "clean-outputs", "sCategory": "files",
     "sMethod": "POST",
     "sPath": "/api/pipeline/{sContainerId}/clean",
     "bAgentSafe": False,
     "sDescription": "Delete all step outputs. User-only: destructive."},
]


SET_INTENTIONALLY_EXCLUDED_PATHS = frozenset({
    # Control-plane endpoints used by the UI to bootstrap a session;
    # agents cannot usefully invoke them.
    ("POST", "/api/connect/{sContainerId}"),
    ("POST", "/api/session/spawn"),
    ("POST", "/api/workflows/{sContainerId}/create"),
    # Configuration surface owned by the researcher.
    ("PUT", "/api/settings/{sContainerId}"),
    # Repos-panel operations — user-only repo management.
    ("POST", "/api/repos/{sContainerId}/{sRepoName}/track"),
    ("POST", "/api/repos/{sContainerId}/{sRepoName}/ignore"),
    ("POST", "/api/repos/{sContainerId}/{sRepoName}/untrack"),
    ("POST", "/api/repos/{sContainerId}/{sRepoName}/push-staged"),
    ("POST", "/api/repos/{sContainerId}/{sRepoName}/push-files"),
    # Dependency / script scans — triggered by the UI's poll loop,
    # not by a researcher clicking a button.
    ("POST",
     "/api/steps/{sContainerId}/{iStepIndex}/scan-scripts"),
    ("POST",
     "/api/steps/{sContainerId}/{iStepIndex}/scan-dependencies"),
    # Sync setup / tracking — credentials and service wiring; user-only.
    ("POST", "/api/sync/{sContainerId}/setup"),
    ("POST", "/api/sync/{sContainerId}/track"),
    # Read-side Overleaf diff preparation.
    ("POST", "/api/overleaf/{sContainerId}/diff"),
})


def fdictLookupAction(sName):
    """Return the catalog entry for sName, or None."""
    for dictEntry in LIST_AGENT_ACTIONS:
        if dictEntry["sName"] == sName:
            return dictEntry
    return None


def fdictBuildCatalogJson():
    """Return a deep-copied catalog in the shape written into the container."""
    return {
        "sSchemaVersion": S_CATALOG_SCHEMA_VERSION,
        "listActions": [dict(dictEntry) for dictEntry in LIST_AGENT_ACTIONS],
    }
