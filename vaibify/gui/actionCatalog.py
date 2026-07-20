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
    # Remote-data gate (all run actions): a run covering a step whose
    # listRemoteData files already exist on disk is answered with
    # runRefused sReason=remoteDataOverwrite. Relay the question to
    # the researcher; only after their yes re-issue with
    # --confirm-remote-overwrite (bConfirmRemoteOverwrite=true).
    {"sName": "run-all", "sCategory": "execution",
     "sMethod": "WS", "sPath": "runAll",
     "bAgentSafe": True,
     "sDescription": "Run every step in the active workflow in order. "
                     "Refused with sReason=remoteDataOverwrite when a "
                     "covered step would re-pull remote data over the "
                     "canonical copy — ask the researcher, then retry "
                     "with --confirm-remote-overwrite."},
    {"sName": "force-run-all", "sCategory": "execution",
     "sMethod": "WS", "sPath": "forceRunAll",
     "bAgentSafe": True,
     "sDescription": "Run every step unconditionally, ignoring cache. "
                     "Subject to the same remote-data overwrite gate "
                     "as run-all."},
    {"sName": "run-from-step", "sCategory": "execution",
     "sMethod": "WS", "sPath": "runFrom",
     "bAgentSafe": True,
     "sDescription": "Run from the given step to the end. "
                     "Args: {iStartStep: int} or {sStartStepLabel: 'A09'}. "
                     "CLI accepts labels directly: run-from-step A09. "
                     "Subject to the remote-data overwrite gate."},
    {"sName": "run-selected-steps", "sCategory": "execution",
     "sMethod": "WS", "sPath": "runSelected",
     "bAgentSafe": True,
     "sDescription": "Run the listed steps in order. "
                     "Args: {listStepIndices: [int, ...]} or "
                     "{listStepLabels: ['A09', ...]}; both may be combined. "
                     "CLI accepts labels as positionals: "
                     "run-selected-steps A09 A10 A11. "
                     "Subject to the remote-data overwrite gate."},
    {"sName": "run-step", "sCategory": "execution",
     "sMethod": "WS", "sPath": "runSelected",
     "bAgentSafe": True,
     "sDescription": "Run a single step by label (A09 / I01) or 0-based "
                     "index. Alias for run-selected-steps with one entry. "
                     "Subject to the remote-data overwrite gate."},
    {"sName": "run-data-only", "sCategory": "execution",
     "sMethod": "WS", "sPath": "runSelected",
     "bAgentSafe": True,
     "sDescription": "Run only the data commands for one step (skip "
                     "tests and plots). Args: step label or 0-based "
                     "index. Payload: sRunMode=dataOnly."},
    {"sName": "run-plots-only", "sCategory": "execution",
     "sMethod": "WS", "sPath": "runSelected",
     "bAgentSafe": True,
     "sDescription": "Run only the plot commands for one step (skip "
                     "data and tests). Args: step label or 0-based "
                     "index. Payload: sRunMode=plotsOnly."},
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
    {"sName": "create-project", "sCategory": "workflow",
     "sMethod": "POST",
     "sPath": "/api/workflows/{sContainerId}/request-creation",
     "bAgentSafe": True,
     "sDescription": "Ask the researcher to create a new Project. "
                     "Creating a project.json is a researcher-only "
                     "structural decision, so this never creates one "
                     "directly: it opens the New Project dialog in "
                     "the researcher's browser (within ~5 s), "
                     "prefilled from the optional args "
                     "{sWorkflowName, sRepoDirectory}. The "
                     "researcher reviews and confirms there; once "
                     "the project exists, populate it with "
                     "create-step."},
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
    {"sName": "align-step-directories", "sCategory": "workflow",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/align-directories",
     "bAgentSafe": True,
     "sDescription": "Migrate every step whose directory does not "
                     "match slug(name) to the name<->directory "
                     "contract, running the full rename cascade "
                     "(git mv, marker, manifest, path rewrites) per "
                     "step with names unchanged. Steps whose names "
                     "contain forbidden characters are reported "
                     "skipped — rename those first. Refused 409 "
                     "while a pipeline action is running."},
    {"sName": "rename-step", "sCategory": "workflow",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}/rename",
     "bAgentSafe": True,
     "sDescription": "Rename a step and cascade the rename through "
                     "its directory (git mv), verification marker, "
                     "manifest paths, and every declared path. Args: "
                     "{sNewName, bDryRun}. Call with bDryRun=true "
                     "first — it returns the change-set and script "
                     "warnings without touching anything; apply with "
                     "bDryRun=false. Refused 409 while a pipeline "
                     "action is running."},
    {"sName": "update-step", "sCategory": "workflow",
     "sMethod": "PUT",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}",
     "bAgentSafe": True,
     "sDescription": "Edit properties of an existing step. "
                     "Args: a partial step object. Edits that would "
                     "empty saTestCommands, saOutputDataFiles, or "
                     "saInputDataFiles on a step that currently has "
                     "them require an explicit bConfirmDestructive="
                     "true flag in the body. Removing one input file "
                     "goes through this action (send the remaining "
                     "saInputDataFiles list)."},
    {"sName": "add-input-data-file", "sCategory": "workflow",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}/input-data",
     "bAgentSafe": True,
     "sDescription": "Declare one raw input data file on a step "
                     "(shown in its Input Data block and watched for "
                     "modification). Args: {sPath} repo-relative; "
                     "step products are rejected — those stay "
                     "{StepNN.*} tokens in commands."},
    {"sName": "declare-no-input-data", "sCategory": "workflow",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/declare-no-input-data",
     "bAgentSafe": True,
     "sDescription": "Set bNoInputData=true on every step that has "
                     "no input files listed and no declaration yet. "
                     "A step reaches Level 1 only when it lists "
                     "inputs or carries this declaration."},
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
    {"sName": "untrack-ai-declaration", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/git/{sContainerId}/untrack-ai-declaration",
     "bAgentSafe": False,
     "sDescription": "Remove the AI declaration file from git "
                     "tracking (the file stays on disk) and commit "
                     "the removal. Args: {sPath} required — must be "
                     "a step's declared declaration file. User-only: "
                     "withdrawing the declaration from the published "
                     "record is the researcher's call."},
    {"sName": "init-project-repo", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/repos/{sContainerId}/init",
     "bAgentSafe": False,
     "sDescription": "Initialize a /workspace/<sDirectory> as a git "
                     "repository so it can host vaibify workflows. "
                     "Args: {sDirectory: str, bCreateIfMissing: bool}. "
                     "Creates an empty initial commit so downstream "
                     "diff/marker logic has a parent. 409 if the "
                     "target is already a git repo. Demoted to "
                     "bAgentSafe=False: creating a project repo is a "
                     "structural decision the researcher should make "
                     "explicitly, not one the agent should improvise "
                     "in response to ambiguous instructions."},
    {"sName": "fetch-project-repo", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/git/{sContainerId}/fetch-project-repo",
     "bAgentSafe": True,
     "sDescription": "Fetch origin in the project repo and report "
                     "iBehind/iAhead so the dashboard can show drift "
                     "against origin/<branch>. Cached for 30s; pass "
                     "{bForce: true} to bypass. Does not modify the "
                     "working tree."},
    {"sName": "refresh-remotes", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/git/{sContainerId}/refresh-remotes",
     "bAgentSafe": True,
     "sDescription": "Fetch origin and return HEAD and upstream shas, "
                     "committer dates, and ahead/behind counts so the "
                     "dashboard reconciles with GitHub in one round "
                     "trip. Args: {bForce: bool, default true} to "
                     "bypass the 30s fetch cache. Agent-safe: only "
                     "remote-tracking refs change; the working tree "
                     "is untouched."},
    {"sName": "pull-project-repo", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/git/{sContainerId}/pull-project-repo",
     "bAgentSafe": True,
     "sDescription": "Fast-forward the project repo to origin. "
                     "Refuses on a dirty working tree, returning a "
                     "structured sRefusal so the dashboard can guide "
                     "the user. No args."},
    {"sName": "push-to-github", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/github/{sContainerId}/push",
     "bAgentSafe": True,
     "sDescription": "Stage, commit, and push specific output files to "
                     "the GitHub mirror (the Level-2 publication flow). "
                     "REQUIRES a listFilePaths body field — pass it as a "
                     "JSON list, e.g. "
                     "listFilePaths='[\"Step/out.csv\",\"Plot/fig.pdf\"]'; "
                     "optional sCommitMessage, sTargetDirectory. Verifies "
                     "the token owner matches the remote before pushing. "
                     "This is NOT a general 'git push' of existing "
                     "commits — to push code/commits, push the branch "
                     "directly."},
    {"sName": "add-file-to-github", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/github/{sContainerId}/add-file",
     "bAgentSafe": True,
     "sDescription": "Commit a single data file to GitHub and push."},
    {"sName": "set-git-identity", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/github/{sContainerId}/identity",
     "bAgentSafe": True,
     "sDescription": "Set git user.name and user.email inside the "
                     "project repo so commits can attribute. Args: "
                     "{sName, sEmail}. Local to the project repo "
                     "only; does not touch global git config."},
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
    {"sName": "pull-manuscript", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/overleaf/{sContainerId}/pull-manuscript",
     "bAgentSafe": True,
     "sDescription": "Pull the Overleaf manuscript sources (.tex/"
                     ".bib/.bbl) into <repo>/.vaibify/manuscript/ "
                     "for in-container reading (the read-manuscript "
                     "skill). Read-side: never dirties the repo."},
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
                     "Args: {sRecordId, sFileName, sDestination}. "
                     "sDestination must be repo-relative; "
                     "absolute or ..-escaping values are rejected."},
    {"sName": "verify-remote", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/sync/{sContainerId}/{sService}/verify",
     "bAgentSafe": True,
     "sDescription": "Verify the workflow manifest against one remote "
                     "(github, overleaf, zenodo, or arxiv). Returns "
                     "iMatching, iTotalFiles, listDiverged. Path: "
                     "sService is the remote name."},
    {"sName": "configure-arxiv", "sCategory": "sync",
     "sMethod": "POST",
     "sPath": "/api/sync/{sContainerId}/arxiv/configure",
     "bAgentSafe": True,
     "sDescription": "Set or clear the arXiv ID used to verify figures "
                     "against the published e-print tarball. Args: "
                     "{sArxivId: '2401.12345'} to set, "
                     "{bRemove: true} to stop tracking. Optional: "
                     "{dictPathMap: {sLocalRelPath: sTarballPath}} for "
                     "renames. Auto-runs verify on success."},
    {"sName": "verify-manifest", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/manifest/verify",
     "bAgentSafe": True,
     "sDescription": "Recompute SHA-256 hashes for every file in "
                     "MANIFEST.sha256 and report mismatches. Returns "
                     "iTotal, iMatching, listMismatches."},
    {"sName": "resolve-commands", "sCategory": "workflow",
     "sMethod": "GET",
     "sPath": "/api/steps/{sContainerId}/resolve-commands",
     "bAgentSafe": True,
     "sDescription": "Dry-run the workflow (the graph's `make -n`): "
                     "substitute every step command against the live "
                     "workflow WITHOUT running anything. Returns per "
                     "command the original text, the fully resolved "
                     "text, and any residual cross-step tokens that "
                     "failed to resolve, plus listWarnings from "
                     "reference validation (including the deprecation "
                     "nudge for the positional {StepNN.stem} form). Use "
                     "this to verify a rewire before running a step."},
    # ---- AICS ladder readiness ----
    {"sName": "check-l2-readiness", "sCategory": "verification",
     "sMethod": "GET",
     "sPath": "/api/workflow/{sContainerId}/level2/readiness",
     "bAgentSafe": True,
     "sDescription": "Return per-criterion pass/fail for the L2 "
                     "Publication gate (GitHub fully synced, Zenodo "
                     "fully synced, AI Declaration step present) so "
                     "the dashboard can render the readiness card."},
    {"sName": "report-l2-gaps", "sCategory": "verification",
     "sMethod": "GET",
     "sPath": "/api/workflow/{sContainerId}/level2/readiness",
     "bAgentSafe": True,
     "sDescription": "Prose-formatted alias of check-l2-readiness for "
                     "agent consumption: same endpoint, the CLI "
                     "translates the booleans into human-readable "
                     "remediation hints."},
    {"sName": "generate-ai-declaration-template",
     "sCategory": "workflow",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}"
              "/ai-declaration/generate-template",
     "bAgentSafe": True,
     "sDescription": "Write a starter AI_USAGE.md template to the "
                     "project repo root for the AI Declaration step "
                     "to point at. Refuses to overwrite an existing "
                     "file. Args: {sRelativePath?: str} (default "
                     "AI_USAGE.md)."},
    {"sName": "add-ai-declaration-step", "sCategory": "workflow",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}"
              "/ai-declaration/add-step",
     "bAgentSafe": True,
     "sDescription": "Append an AI Declaration step to the end of "
                     "the active workflow. 409 when a declaration "
                     "step already exists. Args: {sName?, "
                     "sDirectory?, sDeclarationFile?}; sDirectory "
                     "must be repo-relative and unique among step "
                     "directories (default aiDeclaration). The step "
                     "is interactive: only the researcher's sUser "
                     "badge can pass it."},
    {"sName": "check-l3-readiness", "sCategory": "verification",
     "sMethod": "GET",
     "sPath": "/api/workflow/{sContainerId}/level3/readiness",
     "bAgentSafe": True,
     "sDescription": "Return per-criterion pass/fail for the L3 "
                     "Reproducibility readiness check (manifest, "
                     "lockfile, environment digest, Dockerfile pin, "
                     "reproduce.sh, determinism declaration) so the "
                     "dashboard can render the L3 readiness card."},
    {"sName": "audit-determinism", "sCategory": "verification",
     "sMethod": "GET",
     "sPath": "/api/workflow/{sContainerId}/level3/readiness",
     "bAgentSafe": True,
     "sDescription": "Read-only diagnostic view of the determinism row "
                     "of the L3 readiness card (RNG seeds, BLAS "
                     "pinning, non-deterministic kernels). Returns the "
                     "same JSON as check-l3-readiness; the CLI extracts "
                     "the determinism row and renders it as prose. "
                     "Does not modify the workflow or the project repo."},
    {"sName": "generate-l3-envelope", "sCategory": "verification",
     "sMethod": "GET",
     "sPath": "/api/workflow/{sContainerId}/level3/readiness",
     "bAgentSafe": True,
     "sDescription": "Read-only diagnostic view that surfaces which "
                     "envelope artifacts (MANIFEST.sha256, "
                     "requirements.lock, .vaibify/environment.json) are "
                     "missing or stale. Returns the same JSON as "
                     "check-l3-readiness. To actually rewrite the "
                     "artifacts, use regenerate-envelope; the envelope "
                     "also regenerates automatically when a "
                     "verification transition leaves the workflow at "
                     "Level 1 or higher."},
    {"sName": "regenerate-envelope", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/level3/envelope",
     "bAgentSafe": True,
     "sDescription": "Rewrite the reproducibility envelope now: "
                     "MANIFEST.sha256, requirements.lock, and "
                     ".vaibify/environment.json. Tier failures are "
                     "isolated and logged; the response carries the "
                     "fresh L3 readiness gaps so the caller sees what "
                     "the regeneration achieved."},
    {"sName": "verify-dependency-lock", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/dependencies/verify",
     "bAgentSafe": True,
     "sDescription": "Structural check of requirements.lock: every "
                     "dependency pinned by exact version with SHA-256 "
                     "hashes. Returns listProblems (empty = clean); "
                     "format-only — actual install verification is "
                     "pip install --require-hashes."},
    {"sName": "delete-determinism", "sCategory": "verification",
     "sMethod": "DELETE",
     "sPath": "/api/workflow/{sContainerId}/determinism",
     "bAgentSafe": False,
     "sDescription": "Clear the workflow's declared determinism rules "
                     "(stored in workflow.json). The declare endpoint "
                     "only merges keys, so this is the one way to "
                     "retract a mistaken declaration; the researcher "
                     "then re-declares what still applies."},
    {"sName": "generate-reproduce-script", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/level3/reproduce-script",
     "bAgentSafe": True,
     "sDescription": "Render reproduce.sh from the active workflow + "
                     "environment.json and write it to the project repo "
                     "root. Idempotent; safe to invoke whenever the "
                     "readiness card flags the script as absent or "
                     "out of date."},
    {"sName": "view-l3-attestation", "sCategory": "verification",
     "sMethod": "GET",
     "sPath": "/api/workflow/{sContainerId}/level3/attestation",
     "bAgentSafe": True,
     "sDescription": "Return the current L3 attestation file plus "
                     "the full history of attempts so the agent can "
                     "report pass/fail status and timing without "
                     "triggering a new rebuild."},
    {"sName": "pin-base-image-digest", "sCategory": "workflow",
     "sMethod": "GET",
     "sPath": "/api/workflow/{sContainerId}/level3/readiness",
     "bAgentSafe": False,
     "sDescription": "Read-only diagnostic view that exposes the "
                     "Dockerfile pinning gap (which FROM line lacks an "
                     "@sha256: digest). User-only because the actual "
                     "Dockerfile rewrite is a researcher decision and "
                     "must not be done autonomously. The agent reports "
                     "the gap; the human edits the file."},
    {"sName": "verify-l3-reproducibility", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/level3/verify",
     "bAgentSafe": False,
     "sDescription": "Kick off the expensive rebuild + hash compare "
                     "and write .vaibify/l3_attestation.json. "
                     "User-only because the rebuild can take hours "
                     "and is the only L3 promotion path."},
    {"sName": "declare-standalone-binaries", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/binaries/declare",
     "bAgentSafe": False,
     "sDescription": "Set the workflow's binary-declaration state: "
                     "either {bNoStandaloneBinaries: true, "
                     "listDeclaredBinaries: []} (waiver) or "
                     "{bNoStandaloneBinaries: false, "
                     "listDeclaredBinaries: [{sBinaryPath, sPurpose, "
                     "sExpectedVersion}, ...]} (declaration). "
                     "User-only because misdeclaration produces a "
                     "falsely-passing L3 attestation."},
    {"sName": "declare-determinism", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/determinism/declare",
     "bAgentSafe": False,
     "sDescription": "Write the workflow's dictDeterminism block "
                     "read by the L3 determinism gate. Args: at "
                     "least one of {bAcceptBlasVariance: bool, "
                     "dOmpNumThreads: number, sMklCbwr: str}; "
                     "scalar JSON values only. User-only because "
                     "the bAcceptBlasVariance waiver passes the L3 "
                     "determinism gate and must remain a researcher "
                     "decision."},
    {"sName": "declare-ai-model", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/ai-models/declare",
     "bAgentSafe": True,
     "sDescription": "Declare (or update) one AI model used on this "
                     "project for the Replay-axis provenance record. "
                     "Args: {sVendor, sModelId, sUseStartDate, "
                     "sUseEndDate (YYYY-MM-DD)}; open-weights models "
                     "additionally {bOpenWeights: true, "
                     "sWeightsSource, sWeightsRevisionHash}. Declare "
                     "only facts the researcher confirmed — never "
                     "invent date ranges or weights hashes."},
    {"sName": "remove-ai-model", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/ai-models/remove",
     "bAgentSafe": False,
     "sDescription": "Remove one declared AI model by {sVendor, "
                     "sModelId}. User-only because deleting a "
                     "declaration erases provenance and can drop the "
                     "project below Level 2."},
    {"sName": "declare-personal-layer", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/personal-layer/declare",
     "bAgentSafe": False,
     "sDescription": "Record the researcher's answer about their "
                     "personal instruction layer (private host-side "
                     "agent configuration). Args: {sStatus: 'none' | "
                     "'declared-private' | 'included'}; "
                     "'declared-private' may add "
                     "{dictHashCommitment}; 'included' may add "
                     "{listIncludedPaths} (repo-relative). User-only "
                     "because only the researcher can truthfully "
                     "answer for their private host configuration — "
                     "an L2 consent moment like the other "
                     "declarations."},
    {"sName": "read-project-context", "sCategory": "workflow",
     "sMethod": "GET",
     "sPath": "/api/workflow/{sContainerId}/project-context",
     "bAgentSafe": True,
     "sDescription": "Read the project context file "
                     "(.vaibify/AGENTS.md) — the researcher's "
                     "standing instructions to the agent. Returns "
                     "{bExists, sContent}."},
    {"sName": "update-project-context", "sCategory": "workflow",
     "sMethod": "PUT",
     "sPath": "/api/workflow/{sContainerId}/project-context",
     "bAgentSafe": True,
     "sDescription": "Write the project context file "
                     "(.vaibify/AGENTS.md). Args: {sContent}. Use "
                     "when the researcher asks you to draft or "
                     "update the project's standing instructions; "
                     "the file is versioned with the repository, so "
                     "commit it through the normal canonical flow."},
    {"sName": "generate-project-context-template",
     "sCategory": "workflow", "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/project-context/template",
     "bAgentSafe": True,
     "sDescription": "Write the starter project-context template to "
                     ".vaibify/AGENTS.md. Refuses (409) when a "
                     "context file already exists."},
    {"sName": "configure-prompt-record", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/prompt-record/configure",
     "bAgentSafe": False,
     "sDescription": "Enable or disable the opt-in Prompt Record "
                     "(sanitized agent transcripts captured into the "
                     "repository). Args: {bEnabled: bool}. User-only: "
                     "whether the development dialogue is recorded is "
                     "the researcher's decision."},
    {"sName": "capture-prompt-record", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/prompt-record/capture",
     "bAgentSafe": True,
     "sDescription": "Run one Prompt Record capture pass: new or "
                     "grown agent transcripts are sanitized "
                     "(explicit [REDACTED: …] markers) and landed at "
                     ".vaibify/promptRecord/. Append-only and "
                     "sanitized, so agent-safe; refuses (409) when "
                     "the record is not enabled."},
    {"sName": "view-prompt-record-status", "sCategory": "verification",
     "sMethod": "GET",
     "sPath": "/api/workflow/{sContainerId}/prompt-record/status",
     "bAgentSafe": True,
     "sDescription": "Read the Prompt Record state: capture records, "
                     "coverage intervals (gaps are unmonitored time), "
                     "hash-chain integrity, and any tampered session "
                     "files. Read-only."},
    {"sName": "run-falsification", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}/run-falsification",
     "bAgentSafe": True,
     "sDescription": "Mutation-test a deterministic Python step's "
                     "code against its quantitative tests "
                     "(cosmic-ray) and record the kill-rate as a "
                     "non-gating falsification attestation. "
                     "Expensive: cost is mutants times step runtime, "
                     "bounded by a 300s per-mutant timeout. Measures "
                     "the tests' fault-detection sensitivity, never "
                     "the result's accuracy. 409 when the step is "
                     "not applicable (non-Python or non-deterministic)."},
    {"sName": "view-falsification-attestation",
     "sCategory": "verification",
     "sMethod": "GET",
     "sPath": "/api/steps/{sContainerId}/{iStepIndex}/falsification",
     "bAgentSafe": True,
     "sDescription": "Return the step's falsification attestation: "
                     "live applicability, the persisted kill-rate "
                     "record, digest-keyed staleness, and any "
                     "in-flight run status. Read-only; never runs "
                     "mutation testing."},
    {"sName": "capture-binary-environment", "sCategory": "verification",
     "sMethod": "POST",
     "sPath": "/api/workflow/{sContainerId}/binaries/capture",
     "bAgentSafe": True,
     "sDescription": "Hash a declared binary and capture its --version "
                     "output, appending the result to "
                     ".vaibify/environment.json so the L3 envelope "
                     "records the binary's SHA + version."},
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
    {"sName": "write-draft", "sCategory": "files",
     "sMethod": "PUT",
     "sPath": "/api/draft/{sContainerId}/{sFilePath:path}",
     "bAgentSafe": False,
     "sDescription": "Persist an in-progress textarea draft so unsaved "
                     "edits survive browser crashes. User-only because "
                     "drafts represent the researcher's in-flight work, "
                     "not authored content."},
    {"sName": "delete-draft", "sCategory": "files",
     "sMethod": "DELETE",
     "sPath": "/api/draft/{sContainerId}/{sFilePath:path}",
     "bAgentSafe": False,
     "sDescription": "Discard an editor draft. User-only because "
                     "drafts encode the researcher's unsaved edits."},
    {"sName": "check-files-exist", "sCategory": "files",
     "sMethod": "POST",
     "sPath": "/api/files/{sContainerId}/exist",
     "bAgentSafe": True,
     "sDescription": "Batched existence check: takes "
                     "{saRelativePaths: [str, ...]} (max "
                     "1000 entries, repo-relative or absolute paths) "
                     "and returns {dictExists: {sPath: bool}}. "
                     "Read-only; collapses the per-file HEAD storm "
                     "the file-status pollers used to issue."},
    {"sName": "clean-outputs", "sCategory": "files",
     "sMethod": "POST",
     "sPath": "/api/pipeline/{sContainerId}/clean",
     "bAgentSafe": False,
     "sDescription": "Delete all step outputs. User-only: destructive."},
    # ---- Diagnostics (read-only; safe for in-container agents) ----
    {"sName": "get-host-log-tail", "sCategory": "diagnostics",
     "sMethod": "GET",
     "sPath": "/api/pipeline/{sContainerId}/host-log-tail",
     "bAgentSafe": True,
     "sDescription": "Return the last N lines of ~/.vaibify/vaibify.log "
                     "filtered to this container. Args: {iLines: int, "
                     "default 200, cap 1000}. Read-only; lets an "
                     "in-container agent self-diagnose a run that died "
                     "with exit-code -9999 by reading the actual host "
                     "trigger instead of the symptom in pipeline_state.json."},
    {"sName": "get-pipeline-state", "sCategory": "diagnostics",
     "sMethod": "GET",
     "sPath": "/api/pipeline/{sContainerId}/state",
     "bAgentSafe": True,
     "sDescription": "Return the reconciled pipeline_state.json for this "
                     "container, identical to the dashboard's /state poll. "
                     "Read-only; lets the in-container agent see the same "
                     "post-reconciliation view the dashboard sees (with "
                     "stale-heartbeat fields stamped, including "
                     "sFailureCauseHost and iActiveStepAtDeath)."},
]


SET_INTENTIONALLY_EXCLUDED_PATHS = frozenset({
    # Project-context import reads the HOST filesystem; an
    # agent-invokable host read would let a compromised in-container
    # agent exfiltrate home-directory files into a public repository.
    # Researcher-only, via the dashboard's import picker.
    ("POST", "/api/workflow/{sContainerId}/project-context/import"),
    # The personal-layer hash endpoint reads an arbitrary HOST file
    # and returns its SHA-256 + byte count. Agent-invokable, that is
    # a hash oracle over host files (confirm guesses about
    # credentials, dotfiles, private notes byte-for-byte).
    # Researcher-only: excluded here AND the route itself rejects the
    # agent token lane with 403.
    ("POST", "/api/workflow/{sContainerId}/personal-layer/hash"),
    # The Prompt Record review gate exists so a human confirms what
    # the sanitizer produced before it is treated as publishable; the
    # agent must never approve publication of its own transcript.
    ("POST",
     "/api/workflow/{sContainerId}/prompt-record/"
     "approve-first-capture"),
    # The supervised party must never switch its own supervision on
    # or off; Supervised mode is toggled by the researcher only.
    ("POST", "/api/workflow/{sContainerId}/supervision/configure"),
    # Control-plane endpoints used by the UI to bootstrap a session;
    # agents cannot usefully invoke them.
    ("POST", "/api/connect/{sContainerId}"),
    ("POST", "/api/session/spawn"),
    ("POST", "/api/workflows/{sContainerId}/create"),
    # Docker-runtime retry — agents run inside the container that
    # needs Docker, so the UI is the only sensible caller.
    ("POST", "/api/system/docker-status/retry"),
    # Configuration surface owned by the researcher.
    ("PUT", "/api/settings/{sContainerId}"),
    # Repos-panel operations — user-only repo management.
    ("POST", "/api/repos/{sContainerId}/{sRepoName}/track"),
    ("POST", "/api/repos/{sContainerId}/{sRepoName}/ignore"),
    ("POST", "/api/repos/{sContainerId}/{sRepoName}/untrack"),
    ("POST", "/api/repos/{sContainerId}/{sRepoName}/push-staged"),
    ("POST", "/api/repos/{sContainerId}/{sRepoName}/push-files"),
    # Dependency / script scans — triggered by the UI's poll loop,
    # not by a researcher clicking a button. These handlers are
    # read-only: they walk the step's saScripts and saDataCommands to
    # report what the source code touches, never mutate workflow.json,
    # never write to the container filesystem, never reach the network.
    # Invariant: a scan must produce the same dictWorkflow on disk
    # before and after the call. Exclusion is safe so long as that
    # invariant holds.
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
