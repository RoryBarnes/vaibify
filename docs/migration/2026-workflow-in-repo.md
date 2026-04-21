# Migration — workflow.json must live inside a git repo

**Applies to**: any vaibify container created before the "Workflow = git
repo" architectural change. If your dashboard shows all badges as
`G— O— Z—` (grey) and the server-side detection is returning an empty
`sProjectRepoPath`, you need this migration.

**Why**: vaibify now auto-detects the project repo from the workflow's
parent directory. A `workflow.json` at `/workspace/workflow.json` has
no enclosing git repo, so detection fails and the dashboard reports
"Workflow is not in a git repository" — as it should. See
[docs/architecture.md](../architecture.md), "Workflow = git repo," for
the rationale.

**Scope**: one-time per container. The steps below move one legacy
workflow into a project-repo subdirectory. Repeat per workflow if
more than one exists at the workspace root.

## Steps

```bash
# 1. Enter the container.
docker exec -it <containerName> bash

# 2. Pick the project repo. Inside /workspace you should already have
# a cloned repo that is the natural home for this workflow (the paper
# you're writing, the analysis you're packaging). If there isn't one,
# create it with `git init` inside /workspace first; do not place the
# workflow at workspace root.

# 3. Create the .vaibify/workflows directory inside the project repo.
mkdir -p /workspace/<ProjectRepo>/.vaibify/workflows

# 4. Move the workflow.
mv /workspace/workflow.json \
   /workspace/<ProjectRepo>/.vaibify/workflows/<chosen-name>.json

# 5. Commit it so it is actually under version control.
cd /workspace/<ProjectRepo>
git add .vaibify/workflows/<chosen-name>.json
git commit -m "[vaibify] relocate workflow into project repo"
```

## Verify

```bash
# From inside the container:
git -C /workspace/<ProjectRepo> rev-parse --show-toplevel
# → /workspace/<ProjectRepo>
```

Then, in the host's GUI:

1. Reconnect to the container in the dashboard (or hard-reload).
2. Open the Step Viewer on any file row.
3. Expected: badges render their real states (not uniform grey). For a
   cleanly committed file never pushed to Overleaf or Zenodo, you
   should see `G✓ O— Z—`.

If badges are still grey, fetch the raw status payload to diagnose:

```bash
curl -s "http://localhost:<port>/api/git/<containerName>/status" | jq
```

A `bIsRepo: true` response with a non-empty `sBranch` confirms the
pivot is complete. A `bIsRepo: false` response with `sReason:
"Workflow is not in a git repository"` means the move was not applied
to the workflow actually loaded in the GUI — double-check that the
dashboard picked up the relocated file by refreshing the workflow
selector.

## Creating new workflows going forward

The "Create workflow" flow in the GUI now validates that
`sRepoDirectory` is a git repo. If you target a directory that is not
under version control, the request fails with HTTP 400 and a message
pointing you to `git init`. Create or select an existing git repo
before creating a workflow.

---

# Migration — step directories + markers (follow-on, 2026-04)

**Applies to**: any container migrated by the first section above,
where `workflow.json` step `sDirectory` values are still absolute
(e.g. `/workspace/<ProjectRepo>/KeplerFfdCorner`) and test markers
still live at `/workspace/.vaibify/test_markers/`.

**Why**: step directories and marker files have been moved onto the
same repo-relative footing as every other tracked artifact. Step
`sDirectory` must be relative to `workflow.json`'s directory (usually
a sibling name like `KeplerFfdCorner`), and markers live at
`<ProjectRepo>/.vaibify/test_markers/<slug>.json` where the slug is
the step directory with `/` → `_`. See
[docs/architecture.md](../architecture.md) § "Workflow = git repo"
for the full rationale. Both the load-time validator
(`flistValidateStepDirectories`) and a new architectural invariant
(`testNoWorkspaceRootedMarkerHardcodeInSource`) reject the old layout
going forward.

## Steps

Copy this Python script into the container and run it — it takes the
project-repo path as its first argument:

```bash
docker exec -it <containerName> bash
cat > /tmp/migrate-step-dirs.py <<'PYEOF'
"""Rewrite step sDirectory + relocate markers. Idempotent."""
import json
import shutil
import sys
from pathlib import Path

sRepo = Path(sys.argv[1])  # e.g. /workspace/GJ1132_XUV
sWorkflowJson = sRepo / ".vaibify/workflows" / sys.argv[2]  # filename
sOldMarkerDir = Path("/workspace/.vaibify/test_markers")
sNewMarkerDir = sRepo / ".vaibify/test_markers"
sPrefix = str(sRepo) + "/"
sMarkerPrefix = "workspace_" + sRepo.name + "_"

dictWorkflow = json.loads(sWorkflowJson.read_text())
iRewritten = 0
for dictStep in dictWorkflow.get("listSteps", []):
    sDir = dictStep.get("sDirectory", "")
    if sDir.startswith(sPrefix):
        dictStep["sDirectory"] = sDir[len(sPrefix):]
        iRewritten += 1
sWorkflowJson.write_text(json.dumps(dictWorkflow, indent=2) + "\n")
print(f"Workflow: rewrote {iRewritten} sDirectory values")

sNewMarkerDir.mkdir(parents=True, exist_ok=True)
iMoved = 0
if sOldMarkerDir.is_dir():
    for pOld in sorted(sOldMarkerDir.iterdir()):
        if not (pOld.name.startswith(sMarkerPrefix)
                and pOld.suffix == ".json"):
            continue
        sStepName = pOld.name[len(sMarkerPrefix):-len(".json")]
        pNew = sNewMarkerDir / (sStepName + ".json")
        dictMarker = json.loads(pOld.read_text())
        sInner = dictMarker.get("sDirectory", "")
        if sInner.startswith(sPrefix):
            dictMarker["sDirectory"] = sInner[len(sPrefix):]
        pNew.write_text(json.dumps(dictMarker, indent=2))
        pOld.unlink()
        iMoved += 1
print(f"Moved {iMoved} markers")
PYEOF

python3 /tmp/migrate-step-dirs.py /workspace/<ProjectRepo> <workflow-filename>.json
```

Then commit inside the project repo:

```bash
cd /workspace/<ProjectRepo>
git add .vaibify/workflows/<workflow-filename>.json .vaibify/test_markers/
git commit -m "[vaibify] repo-relative sDirectory + migrate markers"
```

Finally, clean up legacy workspace copies (only after the commit
lands):

```bash
# After confirming the GUI reconnects cleanly and badges hydrate:
rm -rf /workspace/.vaibify/test_markers
```

## Verify

```bash
# From inside the container:
python3 -c "
import json
with open('/workspace/<ProjectRepo>/.vaibify/workflows/<filename>.json') as f:
    d = json.load(f)
print([s['sDirectory'] for s in d['listSteps']])
"
# Expect repo-relative names — no leading '/workspace/'.

ls /workspace/<ProjectRepo>/.vaibify/test_markers/
# Expect files named <StepDir>.json (no 'workspace_' prefix).
```

In the GUI: open the Step Viewer; badge column and step-status
column should both reflect the committed markers. Re-running tests
for one step should write a new marker to the project repo's
`.vaibify/test_markers/` — not to `/workspace/.vaibify/...`.
