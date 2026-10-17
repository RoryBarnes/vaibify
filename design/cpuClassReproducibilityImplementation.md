# Building the assessed Level 3 rerun: implementation guide

**Audience: an agent picking this up fresh.** This is the sequenced
work that builds [cpuClassReproducibility.md](cpuClassReproducibility.md)
("the design").

**Before you start.** Read this whole file first. Then read these
sections of the design:
- "The claim"
- "Rulings and adopted framework"
- "Facts already verified" (facts 22–24 matter most for this guide)
- "Decisions taken"
- "The frozen execution profile and comparison policy"
- "The outcome model and precedence"

Do not re-derive anything they settle. Reopening a decision costs a
round trip to the researcher.

**Line numbers are at commit `ffdbebb6` and will drift.** Trust function
names. When you need the current layout, run
`python tools/listModules.py <subtree>`; never guess.

**Authority.**
- The researcher approved building the design and will test the result
  against its Stage 8 checklist before anything reaches `main`. The
  approval covers this guide, not departures from it.
- **Ask the researcher before** changing a precedence rule, a decision
  (D1–D8) or an implementation decision (I1–I11 below); touching Docker
  security, user namespaces or network isolation; changing a CI
  workflow; or anything else on `CLAUDE.md`'s "Ask first" list.
- Editing `.claude/skills/*/SKILL.md` (Phase 8) triggers the
  sensitive-edit hook. That is expected; the researcher confirms it.

**History.**
- 2026-10-17: written from four read-only surveys of the code and two
  measurements (design facts 12 and 21).
- 2026-10-17: revised after a review of this guide:
  - `reproduce.sh` clears what it regenerates;
  - every lane follows the runner's order and failure handling;
  - structural checks no longer stand in for agreement;
  - evidence is bound to its step and command;
  - the recorded comparison policy is selected from vaibify's own
    frozen code;
  - provenance is recorded per section;
  - the image check has a supported-encoding contract;
  - processor capabilities are read before the library probe;
  - the work ships as one pull request.

  The code was re-read and three measurements made for this revision
  (design facts 22–24).

---

## Where the work happens: one branch, one pull request

- **One branch, `feat/assessed-level3`, cut from `main`, and one pull
  request into `main`.** The phases below are ordered groups of
  commits on that branch, not separate pull requests.
- **Open the pull request as a draft once Phase 1 is committed.** CI
  runs only for pull requests (`pull_request` triggers), so a pushed
  branch with no PR runs nothing; the draft is what gives every later
  push the browser lane, the exclusive `docker_live` lane and the full
  suite.
- **Push at the end of every phase, but do not wait for CI before
  starting the next one**, unless that phase's "done when" needs a lane
  only CI can run:
  - the browser lane, if you cannot load a browser locally;
  - the exclusive `docker_live` lane, for a falsification entry that
    reads NOT EVALUATED locally;
  - the CI runners' CPUs (Phase 5).

  When CI fails, fix it before building further on the failing area.
- **Keep the PR description current**: one section per phase, saying
  what landed, what was verified locally and what only CI verified.
  Name every surface you could not verify (`CLAUDE.md`).
- **The draft stays a draft until the researcher's checklist passes**
  (Phase 9). Phases 2–4 temporarily break parity: the dashboard grades
  with the new model while `reproduce.sh` still ends in `sha256sum -c`.
  Because nothing merges before the end, `main` never carries that
  state.
- **Optionally, work in a worktree**:
  `git worktree add ../vaibify-assessed-level3 -b feat/assessed-level3 main`.
- **Shepherd the PR with the `monitor-pr` skill** at the end, and
  whenever CI fails mid-way. Never force-push; add commits instead.
  `blockDestructiveGit.py` denies `--force`.

### Skills to load, by phase

| Phase | Load |
|---|---|
| all | `reproducibility-lane`, `dashboard-state-honesty` |
| 2, 4, 5, 7 (new execs or container writes) | `container-mutations` |
| 2, 4, 5, 7 (paths in containers and projects) | `host-mode-and-paths` |
| 4 (new pinned envelope file) | `container-image-and-packaging` only if anything under `vaibify/containerImage/` changes; nothing in this plan should |

### Before every push

Condensed from the repo's own checklist:

1. **For a live leg:**
   `VAIBIFY_REQUIRE_DOCKER_DAEMON=1 python -m pytest <file> -m docker_live -v`.
2. **New and changed tests:** `python -m pytest tests/<file>.py -v`.
3. **If you added or renamed an exec, a container write, a subprocess,
   or a function holding one**, regenerate all three ledgers and dispose
   any new row:
   - `python tools/generateMutationInventory.py --write && python tools/generateMutationInventory.py --check`
   - `python tools/generateHostCapabilityInventory.py --write && python tools/generateHostCapabilityInventory.py --check`
   - `python tools/generateStyleInventory.py --write && python tools/generateStyleInventory.py --check`
4. **Structural tests:**
   - `python -m pytest tests/testArchitecturalInvariants.py -v`
   - `python -m pytest tests/testStyleInvariants.py tests/testMutationInventory.py tests/testHostCapabilityInventory.py tests/testLedgerFormatIsCanonical.py tests/testBrowserLaneContract.py -v`
5. **Falsification tests:** `python -m pytest -m falsification -v`.
6. **Commit, then reconfirm each new registry entry:**
   `python tools/reconfirmFalsification.py --only <nodeid-substring>`.
   Every entry must report KILLED. A `docker_live` entry reads NOT
   EVALUATED without a daemon and is judged on CI's exclusive lane.
7. **Full suite:**
   `python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py`.
8. **JavaScript changed:**
   `pip install -e '.[browser]' && python -m playwright install chromium && python -m pytest tests/browser -m browser`.
9. **Docs or skills changed:**
   - `python tools/checkAmericanSpelling.py`
   - for a skill edit, also `python -m pytest tests/testSkillIntegrity.py -v` and the harnesses in `docs/skillTesting.md`.
10. **Carrier audit:** `python tools/carrierIntentAudit.py`.

**Two facts about the ratchets** (verified):
- `testModuleSizeIsBounded` and `testLeafModuleHasNoIntraPackageImports`
  scan only `vaibify/gui/`. The new leaf modules under
  `vaibify/reproducibility/` need their own test (Phase 1).
- The naming contract (`tests/testStyleInvariants.py`) applies to every
  new name outright, and `--write` cannot add debt. Names follow the
  `CLAUDE.md` style guide: Hungarian prefixes, `f<type>` function
  prefixes with an action verb, and camel-case file names.

---

## Implementation decisions this guide adds (provisional, like D1–D8)

These were made while mapping the code. Each is reflected in the design
where it changes the design.

- **I1. JUnit is the only source of test-node evidence, with one report
  per test command.**
  - Every test command the rerun runs gets
    `PYTEST_ADDOPTS="--junitxml=<evidence directory>/<invocation ID>.xml"`.
    The name is deterministic, never `mktemp`, so a report is tied to
    the command that wrote it (design fact 22).
  - Data and plot commands get no such export.
  - Test markers are never read for the assessment.
- **I2. Three modules, one rendered file, the same bytes in both
  reproduction lanes.**
  - **`vaibify/reproducibility/rerunProcedure.py`**, a leaf module that
    is current and may change. It holds:
    - integrity hashing against `MANIFEST.sha256`;
    - reference capture;
    - the clearing program, moved from `rerunPreparation`;
    - the capability reading and the library probe (I10);
    - invocation IDs;
    - the sub-command dispatcher.
  - **`vaibify/reproducibility/comparisonPolicyV1.py`**, a leaf module
    that is frozen once released (I9). It holds:
    - ledger and JUnit reading;
    - binding checks to evidence (I8);
    - the image check;
    - precedence and the roll-up;
    - verdict wording and exit codes;
    - the report.
  - **`vaibify/reproducibility/rerunAssessment.py`**, an ordinary module.
    It holds:
    - the policy registry;
    - the assessment-plan builder;
    - the renderer;
    - reading the recorded policy;
    - host-side re-derivation.
  - **Both leaf modules import only the standard library.** Pillow is
    imported inside the image check, and NumPy inside the probe. Neither
    module has a `from __future__` import or an `if __name__` block, and
    their top-level names are disjoint, because the renderer
    concatenates their sources verbatim.
  - **The rendered file is the project file `.vaibify/rerunAssessment.py`.**
    `reproduce.sh` runs the pinned copy. The dashboard lanes write
    vaibify's own render, for the recorded policy, into the shadow's
    scratch directory and run that, never the repository's copy.
  - **The host re-derives every derived field** with the imported
    policy module, and cross-checks output hashes and command exit
    statuses (Phase 2). Any disagreement is "Could not run:
    inconsistent assessment".
- **I3. The rendered file does integrity, reference capture and
  clearing in the container**, before the steps, and integrity again
  after them.
  - `reproduce.sh`'s final `sha256sum -c` is replaced by the assessor's
    exit code.
  - The script's host-side `exit 2`s become `exit 4`.
  - **Every exit path of the script ends in one of D6's codes** (0, 1,
    3, 4, 5).
- **I4. D6 is refined.**
  - Insufficient evidence is exit 5, and 2 is reserved (Click's usage
    errors, and the old `reproduce.sh`).
  - A failed data or plot command is exit 1.
  - The design is updated.
- **I5. `reproduce.sh` runs the declared tests, and mirrors the runner
  command for command** (design fact 24):
  - the same order, the same section and group failure handling, and
    later steps still run after a failed one;
  - every command's exit status goes to the invocation ledger (I8);
  - this supersedes the 2026-09-01 decision recorded at
    `pipelineUtils.T_EXECUTED_COMMAND_KEYS`
    (`vaibify/gui/pipelineUtils.py:432-448`), as design F5 requires;
  - a generated-category test command that writes no report leaves its
    checks unexecuted (exit 5). The reason names "no JUnit report (is
    pytest installed in the image?)".
- **I6. The answer is stored as a versioned profile ID.**
  - It lives in `dictDeterminism`, for example
    `sExecutionProfile: "across-v1"`, written when the researcher
    answers.
  - The profile table is append-only.
  - The envelope records the concrete profile: `sProfileId`,
    `dictRequested`, `sRequiredLevel` and `listFloorKeys`.
    `reproduce.sh` replays it through `docker run -e`, with keys
    allowlisted and values validated.
- **I7. Provenance comes from a per-run context and is recorded per
  section.**
  - A step's data outputs are recorded only when its data section ran
    and every command in it exited 0; its plot outputs, likewise for
    the plot section. Nothing else relabels a file.
  - Records live in a tracked, unpinned file,
    `.vaibify/outputProvenance.json`.
  - Writing the manifest copies a digest-bound subset into
    `.vaibify/environment.json`.
  - No CPU model string and no hostname is ever recorded: vendor,
    `x86-64` level and library kernel names are enough.
- **I8. Evidence accounting.** These rules are part of
  `comparison-v1`.
  - **An invocation is one command the rerun runs.** Its ID is
    `<step label>.<section>.<1-based position>`, where the section is
    one of `data`, `plot`, `integrity`, `qualitative`, `quantitative`
    or `legacy`. An example is `A03.quantitative.1`. It is built only by
    `rerunProcedure.fsBuildInvocationId`.
  - **The ledger** is `<evidence directory>/invocations.tsv`, one line
    per command that ran: `<invocation ID>\t<exit status>`. A planned
    invocation with no line never ran.
  - **Binding.** An expected check is
    `(step, category, test file repository path, testcase name)`.
    - It is looked for only in the reports of that step's invocations
      of that category's group.
    - A testcase matches when its `name` equals the testcase name, and
      its `classname` equals the test file's repository path with `/`
      replaced by `.` and `.py` removed, or a suffix of it that starts
      after a `.`. The classname depends on the project's pytest
      configuration (fact 22).
    - A testcase that matches two of the step's expected test files is
      ambiguous: insufficient evidence, naming both.
  - **Classification of a testcase:**
    - `<failure>` is failed;
    - `<error>` and `<skipped>` did not assert;
    - absent from every report it could be in is not executed.
  - **Repeated occurrences combine worst first:** failed, then did not
    assert, then passed. A later pass never erases an earlier failure.
  - **Invocations without a usable report.** "Expected checks" means a
    structured group's command; a `legacy` command expects none.
    - A command with expected checks that wrote no report, or an
      unparsable one, leaves its checks "did not assert", with the
      reason named.
    - A `legacy` command with no report is a step-level required check
      judged by its exit status alone.
    - A non-zero exit with no `<failure>` in a parsable report is a
      step-level check that did not assert. Pytest exits 2–5 for an
      interruption, an internal error, a usage error, or no tests
      collected.
  - **Unexpected testcases** in any report are step-level required
    checks, classified the same way.
  - **Data and plot invocations** decide the first row of the design's
    per-output table: an output whose section did not finish with every
    command exiting 0 is Failed, naming the command.
- **I9. The comparison policy is selected, never inherited.**
  - **The recorded policy** is line 2 of the rendered file:
    `# vaibify-comparison-policy: <policy ID>`. Nothing else records it.
  - **`rerunAssessment.DICT_COMPARISON_POLICIES`** maps each ID to its
    frozen module. `S_CURRENT_COMPARISON_POLICY` is `"comparison-v1"`.
  - **The dashboard lanes read line 2** from the shadow's pinned copy,
    with a strict regex `^# vaibify-comparison-policy: ([a-z0-9-]{1,32})$`.
    They then run vaibify's own module for that ID:
    - an ID not in the registry is exit 4, with the design's "upgrade
      vaibify" wording;
    - an absent file means the current policy, reported as "none
      recorded".
  - **Freezing.** `comparisonPolicyV1.py`'s SHA-256 is pinned by a test.
    - Until the first release that ships it, edit freely, and update
      the pinned hash in the same commit.
    - After that release, a change is a new module and a new ID.
  - **Reproduction and eligibility are separate questions.**
    - A reproduction grades under the recorded policy.
    - `fbL3AttestationCurrent` and the reproduce-script currency check
      compare against the current policy. Moving a project to a new
      policy is the author regenerating `reproduce.sh`.
- **I10. Capabilities first, then the library probe.**
  - **Stage A, capabilities.**
    - It runs `rerunProcedure`'s capability reader in a process that
      imports nothing numerical, with every allowlisted key unset.
    - It reads the `x86-64` level, vendor and visible cores from
      `/proc/cpuinfo` and `os.sched_getaffinity`.
    - A pure function then decides which requested settings apply:
      below `sRequiredLevel`, the `listFloorKeys` are dropped and the
      thread pins are kept.
  - **Stage B, the probe.**
    - It loads NumPy and OpenBLAS under exactly the applied settings.
    - It reports what it finds: "NumPy not installed", or "OpenBLAS
      not detected", with NumPy's reported BLAS name when it has one.
    - It never changes the applied settings.
  - **Order.** Every lane runs A, then B, then the steps: the author's
    runs, the dashboard's "Run tests", the shadow and `reproduce.sh`.
- **I11. `reproduce.sh` clears the reader's own directory, as the
  shadow clears its copy.**
  - **Today:** the script mounts `$PWD` at `/work` and regenerates
    outputs in place (`reproduceScriptGenerator.py:373-378`).
  - **The change:**
    - it now deletes the plan's produced paths first, with the
      shadow's classification and refusals;
    - before deleting, it prints what it is about to delete and how to
      restore it (`git checkout -- <paths>`, or the deposit);
    - references and evidence stay in a container-only `mktemp -d`,
      never in `/work`.
  - **Would reverse:** the researcher preferring that the script run in
    a container-internal copy and leave the reader's directory
    untouched.

---

## Attack surface

`CLAUDE.md` requires this to be called out before implementing.

1. **A new pinned file executes inside the container** (Phase 4).
   - `.vaibify/rerunAssessment.py` runs only in the container, never on
     the host.
   - Its bytes are integrity-checked through the manifest, and Level 3
     requires them to equal vaibify's own render for the current policy.
   - The dashboard lanes run vaibify's render, never the repository's
     copy (I2).
2. **`reproduce.sh` reads execution settings from a cloned file.** The
   host half reads `.dictExecutionProfile` from `.vaibify/environment.json`
   with `jq`, and a stranger's clone supplies it.
   - Only keys on a fixed allowlist pass: `OPENBLAS_CORETYPE`,
     `NPY_DISABLE_CPU_FEATURES`, `OPENBLAS_NUM_THREADS`,
     `OMP_NUM_THREADS` and `GLIBC_TUNABLES`.
   - Values must match `^[A-Za-z0-9_.,=-]{0,64}$`.
   - `sRequiredLevel` must be empty or one of `x86-64-v2`, `x86-64-v3`
     and `x86-64-v4`. `listFloorKeys` must be a subset of the allowlist.
   - Anything else exits 4, naming the key. A value is never passed
     through `eval` or an unquoted expansion.
   - The same allowlist, rendered from one Python tuple, is enforced
     host-side when the dashboard replays a profile. In the container,
     the keys Stage A drops are unset only after matching that list in
     a literal `case` statement.
3. **The dashboard reads a policy ID from a cloned file** (I9). Only the
   strict regex and the registry's own keys are accepted. The ID
   selects vaibify's code; it never names a path or a module to import.
4. **Clearing deletes files** (Phase 2 in the shadow, Phase 4 in the
   reader's directory, I11).
   - The classification runs host-side with the existing helpers. The
     in-container program validates every target before deleting any,
     resolves symlinks against the container's filesystem, refuses
     anything outside the repository or overlapping a protected path,
     and never follows a symlink out of a directory it clears.
   - Keep every existing refusal and its tests (`rerunPreparation`).
   - An ambiguous or unsafe classification also refuses to render
     `reproduce.sh`, so a reader's script never carries a job the
     shadow would refuse.
5. **Scratch and evidence directories** are created by vaibify, fresh
   and exclusively; an existing one refuses the run. Their paths never
   come from workflow text, and they are never inside the repository.
   - The shadow resolves them beside the shadow repository, under
     `shadowRerun.S_SHADOW_WORKSPACE_ROOT`
     (`ftResolveShadowPaths`, `shadowRerun.py:161`).
   - The script uses `mktemp -d` inside the container.
   - Steps write the JUnit files, so the evidence directory must be
     writable by the user the steps run as.
6. **The assessor runs in the same container as the published code.**
   - Its verdict is in-container evidence, exactly as test outcomes are
     today; observed hashes are already computed in the container by a
     typed read (`dockerConnection.fdictHashContainerRepoPaths`).
   - Mitigations: `python3 -I`, which ignores user site-packages and
     `PYTHON*` variables; host-side re-derivation; and the host-side
     hash and exit-status cross-checks.
   - Only the Stage B probe runs without `-I`, because it must see
     what the steps see. It yields observations, never a verdict.
   - Do not claim robustness against deliberately adversarial published
     code. The lane never had it.
7. **Provenance publishes hardware facts**: vendor, `x86-64` level,
   kernel names and core count, to GitHub and Zenodo through the
   envelope. Record nothing more identifying (I7).

---

## Formats (Phase 1 defines them; every later phase reads them)

### The rendered file

`rerunAssessment.fsRenderRerunAssessmentScript(dictWorkflow, sComparisonPolicy)`
returns, deterministically:
1. `# Generated by vaibify; do not edit. Regenerate reproduce.sh instead.`
2. `# vaibify-comparison-policy: <policy ID>`
3. the source of `rerunProcedure.py`, verbatim;
4. the source of the selected policy module, verbatim;
5. `DICT_ASSESSMENT_PLAN = json.loads(<repr of the plan's JSON text>)`,
   with the JSON from `json.dumps(..., sort_keys=True, indent=1)`. The
   `repr` keeps every workflow-controlled string a literal;
6. `if __name__ == "__main__": sys.exit(fiRunMain(sys.argv[1:], DICT_ASSESSMENT_PLAN))`.

**Sub-commands.** Every one takes `--repo <dir>` explicitly: the shadow
runs the file from its scratch directory, and the script from `/work`.

| Sub-command | Does | Interpreter |
|---|---|---|
| `prepare --scratch` | verify pinned inputs (exit 3 on a change); copy each published raster figure whose manifest hash matches into the scratch directory | `python3 -I` |
| `clear` | delete `dictClearingJob`'s produced paths and clear its scratch paths, all validated first; a refusal is exit 4 | `python3 -I` |
| `capabilities --scratch` | Stage A (I10): write the decision to the scratch directory, and print one key to unset per line | `python3 -I`, allowlisted keys unset |
| `probe --scratch` | Stage B: write the observations to the scratch directory | the image's `python3`, without `-I` |
| `assess --scratch --evidence [--machine]` | integrity again, then grade; print the verdict and tally, and with `--machine` a final `VAIBIFY_RERUN_ASSESSMENT <json>` line; exit with the code | `python3 -I` |

The `--machine` line follows the `rerunPreparation._S_RESULT_PREFIX`
precedent.

### The assessment plan (schema 1)

- `iPlanSchemaVersion`, `sComparisonPolicy`.
- `dictClearingJob`: `listProduced`, `listProtected` and `listScratch`,
  all repository-relative.
  - Built from `rerunPreparation.fdictClassifyRerunPaths` and
    `flistResolveScratchRepoPaths`.
  - Scratch paths are resolved with the same variables that
    `_fdictBuildReproductionVariables` gives the script's commands.
- `listCarriedPaths` (`rerunVerification.flistCarriedOutputRepoPaths`).
- `listSteps`, in execution order (`flistSelectStepsTheRerunExecutes`),
  each with:
  - `sStepLabel`, `sStepDirectory`;
  - `listDataInvocations` and `listPlotInvocations`: invocation IDs, one
    per non-empty command, in order;
  - `dictTestGroups`: section name to a list of invocation IDs, in the
    order of `workflowManager.fdictResolveTestCommandGroups`;
  - `listOutputs`, each with:
    - `sPath`;
    - `sSection` (`data` for `saOutputDataFiles`, `plot` for
      `saPlotFiles`);
    - `sKind` (`data`, `raster` or `vector`, by extension per
      `comparison-v1`);
  - `listStandardSets`: `sCategory`, `sStandardsPath` and `sTestPath`,
    one pair per generated category the step has.

**Resolving outputs per section.** Use `manifestPaths.flistStepOutputRepoPaths`
(`manifestPaths.py:198-240`). Give it an optional keys parameter,
defaulting to `TUPLE_OUTPUT_KEYS` (50), so each section resolves through
the same code. Never re-derive the resolution.

**Pairing standards with test files.** Use the `testGenerator` path
helpers (`fsQuantitativeTestPath` and its siblings, `testGenerator.py:205-235`)
and their standards-path counterparts. The templates pair them by
suffix, through `__file__`.

**Standards are not copied.** The assessor reads the pinned standards
files at run time, so it grades the bytes the manifest pins.

**How a standard names its output.** Verified in the templates:
- quantitative `sDataFile` resolves as `pathlib.Path(<step directory>) / sDataFile`
  (`dataLoaders._ffLoadValue`, `dataLoaders.py:1180`; the step
  directory is the test file's grandparent);
- integrity and qualitative checks join `sFileName` the same way.

A standard that resolves to no planned output is a step-level check.

**Expected testcase names** (fact 13; verified at
`templateManager.py:162`, `421`, `517`):
- `test_quantitative_benchmark[<sName>]`;
- `test_integrity_check[<sFileName>]`;
- `test_qualitative_check[<sFileName>]`.

The quantitative ones are agreement comparisons; the other two are
required checks (design, "An output's expected checks are of two
kinds"). A duplicate `sName` in one file is ambiguous for every check
under it.

### The assessment report (schema 1)

- `iSchemaVersion`, `sComparisonPolicy`, `sComparisonPolicySource`
  (`recorded`, or `current; none recorded`).
- `dictIntegrity`: `bInputsUnchanged`, `listChangedInputs`,
  `listMissingInputs`, `bManifestMoved`, `bChangedDuringRun`.
- `listInvocations`, each with:
  - `sInvocationId`, `sSection`;
  - `iExitStatus` (`null` if it never ran);
  - `sReportState`: `read`, `absent`, `unparsable` or `none expected`.
- `listOutputs`, each with:
  - `sPath`, `sRole` (`output` or `carried`), `sSection`, `sKind`;
  - `sExpectedSha256`, `sObservedSha256` (`null` if missing);
  - `sSectionState`: `completed`, `failed: <invocation ID> exited <n>`,
    or `not run: <reason>`;
  - `listAgreementChecks` and `listRequiredChecks`. Each entry holds its
    binding key and its outcome: `passed`, `failed`, `did not assert`,
    `not executed` or `ambiguous`, plus its reason;
  - `dictImageCheck` or `null`. It holds `bRan`, `sEncoding`,
    `iLargestLevelChange`, `iPixelsChanged`, `iPixelsOverThreshold` and
    `sReason`;
  - `sOutcome`.
- `listStepLevelChecks`.
- `dictContexts`: the Stage A decision and the Stage B observations,
  copied verbatim. They explain; they are never graded.
- `dictTally`, `sVerdict`, `sVerdictRendered`, `iExitCode`.

Every derived field (`sSectionState`, each check's outcome, `sOutcome`,
`dictTally`, `sVerdict`, `sVerdictRendered`, `iExitCode`) is a pure
function of the observation fields and the plan. The host recomputes
them in Phase 2.

---

## Phase 0: setup and spikes (no product code)

1. **Cut `feat/assessed-level3` from `main`.**
2. **Map the code.** Run `python tools/listModules.py vaibify/reproducibility`
   and `python tools/listModules.py vaibify/gui`.
3. **Spike A: a JUnit report through the runner's real exec path.**
   - In a throwaway container from a vaibify image, run a generated
     test command exactly as `pipelineRunner._ftRunSingleCommand`
     builds it (`pipelineRunner.py:342-413`): the run marker prefix,
     then the environment prefix, then the `/usr/bin/time` wrapper.
   - Append
     `export PYTEST_ADDOPTS="--junitxml=<dir>/A01.quantitative.1.xml" && `
     to the environment prefix.
   - Confirm three things with the image's pytest:
     - the report lands, and its testcases carry the expected names;
     - the classname changes with a root `pyproject.toml`;
     - a command's own `--junitxml` suppresses the export (fact 22).
4. **Spike B: the image check with the image's Pillow.**
   - Confirm that converting to RGBA and taking per-band
     `ImageChops.difference(...).getextrema()` reproduces fact 20's
     "largest pixel change" column, on the same synthetic figures.
   - Count changed pixels from the histogram of the per-pixel maximum
     over bands (`ImageChops.lighter` across the split bands).
   - Re-run fact 23's cases (palette, palette transparency, 16-bit RGB
     PNG, 16-bit grayscale, multi-frame TIFF and float TIFF) to confirm
     the image's Pillow behaves as the host's did.
5. **Hand off spike C to the researcher** (needs their Mac): design
   Stage 5 measurement 0a. It does not block building; it can only move
   `across-v1` to `across-v2` (D5).

**Done when** both spikes' results are written into the design's facts
list, and any surprise has gone back to the researcher.

---

## Phase 1: the assessment core

**Goal.** Build the procedure module, the frozen policy module, the
registry and the renderer. Nothing is wired into a lane yet. Open the
draft PR once this phase is committed.

**Build:**
1. **`vaibify/reproducibility/rerunProcedure.py`** (leaf):
   - `fsBuildInvocationId(sStepLabel, sSection, iPosition)`. It
     validates the label (`^[A-Z][0-9]{2,}$`) and the section against
     the six names;
   - integrity: hash the manifest's pinned inputs and report changed,
     missing and moved;
   - reference capture for `prepare`. It copies bytes only and decodes
     nothing;
   - **clearing:** move the logic of `rerunPreparation._S_PREPARATION_PROGRAM`
     here as functions, keeping every check: validate all targets
     first, resolve symlinks in the container, refuse outside-repo and
     protected overlaps, and never follow a symlink out;
   - Stage A and Stage B (I10). The probe finds OpenBLAS by globbing
     `numpy.libs/*openblas*` beside the NumPy package. It reads the
     kernel through `ctypes` with the first symbol present among
     `scipy_openblas_get_corename64_`, `openblas_get_corename64_` and
     `openblas_get_corename`, and the thread count with the matching
     `..._get_num_threads...` symbol. It reads NumPy's CPU features
     programmatically (on NumPy 2,
     `numpy._core._multiarray_umath.__cpu_features__`), recording "not
     readable" when absent;
   - `fiRunMain(listArguments, dictPlan)`, the sub-command dispatcher.
2. **`vaibify/reproducibility/comparisonPolicyV1.py`** (leaf, frozen
   after release):
   - reading the ledger and the JUnit reports, with I8's binding,
     classification and worst-first combination;
   - **the image check** (design D2):
     - decide the encoding from the file's own bytes: the PNG header's
       bit-depth byte, the TIFF `BitsPerSample` and `SampleFormat` tags,
       the JPEG decoded mode, and `n_frames`;
     - an unsupported encoding returns "cannot assess: <reason>",
       never a comparison;
     - supported pairs must match in format, dimensions and decoded
       mode. Both are converted to RGBA and compared per channel, with
       a threshold of 2 levels;
     - Pillow missing, `DecompressionBombError` and any decoding error
       mean "cannot assess";
   - the per-output table and the overall roll-up, exactly as in the
     design, with agreement comparisons and required checks kept
     apart;
   - verdict wording and exit codes (D6 as refined by I4);
   - the report builder;
   - constants: `S_COMPARISON_POLICY = "comparison-v1"`, the raster and
     vector extension tuples, and the 2-level threshold.
3. **`vaibify/reproducibility/rerunAssessment.py`**:
   - `DICT_COMPARISON_POLICIES`, `S_CURRENT_COMPARISON_POLICY`, and
     `S_COMPARISON_POLICY_V1_SHA256`;
   - `fdictBuildAssessmentPlan(dictWorkflow)`. It raises the
     `rerunPreparation` refusals for an ambiguous or unsafe
     classification;
   - `fsRenderRerunAssessmentScript(dictWorkflow, sComparisonPolicy)`,
     with an unknown ID raising;
   - `fsReadRecordedComparisonPolicy(sFileText)`, which returns the ID,
     `None` for no header, or raises on a malformed header;
   - `fdictRederiveAssessment(dictReport, dictPlan)`, which dispatches
     on the report's policy.

**Tests.** Use generic fixtures only: `testNoScienceSpecificIdentifiersInSource`
scans `vaibify/`, and the spirit of `CLAUDE.md` covers tests. Each test
is a falsification entry with a `Kills:` line, and its oracle comes
from somewhere other than the code under test.
- **Structure:**
  - both leaf modules import nothing from `vaibify` (a new test,
    because the gui-only leaf test does not reach them);
  - their top-level names are disjoint, and neither has a
    `from __future__` import or an `if __name__` block;
  - `comparisonPolicyV1.py`'s bytes hash to
    `S_COMPARISON_POLICY_V1_SHA256`.
- **The transcription runs standalone.** Render the file, run each
  sub-command with `python3 -I` in a temporary repository, and get the
  same report as the imported functions on the same inputs. Rendering
  twice gives identical bytes.
- **Every row of the per-output table and the roll-up**, including:
  - bytes identical with a failing node gives Failed;
  - bytes identical with a skipped node gives Insufficient;
  - **a data file whose bytes differ, with only passing integrity and
    qualitative checks, gives Insufficient** (the review's
    counterexample);
  - a data command that exited 1 gives Failed for that step's data and
    plot outputs, naming the invocation;
  - a plot command that exited 1 gives Failed for the plot outputs,
    while the data outputs are graded normally;
  - a mix of failures and missing evidence gives Failed, with both in
    the tally;
  - a deleted pinned test gives Published inputs changed;
  - zero comparisons with bytes differing gives Insufficient;
  - a standard whose node is absent while the file's other nodes passed
    gives Insufficient;
  - a duplicate `sName` gives Insufficient.
- **Evidence accounting (I8):**
  - two steps whose test files share a name, with classnames as a
    project without root configuration produces them (fact 22): a
    passing report in one step never satisfies the other step's absent
    check;
  - the same check failing in one report and passing in a later one
    gives Failed;
  - a structured group's command that ran with no report gives "did
    not assert" for its checks;
  - a `legacy` command with no report and exit 1 gives a failed
    step-level check, and with exit 0 a passed one;
  - a non-zero exit with no `<failure>` gives a step-level check that
    did not assert;
  - a planned invocation with no ledger line gives "not executed";
  - an ambiguous classname match gives "ambiguous", naming both files.
- **The image check**, on images generated in the test with Pillow:
  - 1024×1024 with 16 pixels inverted fails (the review's
    counterexample);
  - a ±1-level change passes;
  - **palette images with identical indices and different palettes
    fail** (fact 23);
  - palette transparency is resolved;
  - **a 16-bit RGB PNG differing only in its low byte, a 16-bit
    grayscale PNG, a float TIFF and a two-frame TIFF each give
    Insufficient**, with the reason named. Build the 16-bit RGB PNG by
    hand with `zlib` and `struct`, because Pillow cannot write one;
  - different dimensions give Insufficient;
  - a vector file that differs gives Insufficient;
  - a plot-only step gets no exemption.
- **Policy registry:**
  - an unknown ID raises in the renderer;
  - a malformed header raises in the reader;
  - a missing header reads as `None`.

**Done when** every row and rule above is pinned by a KILLED
falsification entry, the transcription round trip passes, and the
draft PR is open.

---

## Phase 2: the shadow lane grades with the assessor (backend)

**Goal.** Route these through the assessor, and persist the new outcome
model:
- the dashboard's Verify;
- `vaibify reproduce --rerun` and `--from`;
- the dashboard's "Reproduce a published project".

**Read first:**
- `rerunVerification.fdictRerunAndVerifyWorkflow` (`rerunVerification.py:155-251`);
- `fbRunWorkflowInContainer` (386-414);
- `fdictVerifyRerunOutputs` (465-579);
- `rerunPreparation.fdictClearShadowBeforeRerun` (263-293);
- `shadowRerun._fdictCompareUnderTheShadowsOwnAdmission` (`shadowRerun.py:739-786`).

Every exec below must happen inside the shadow's own admission: after
it is opened at 768, and before it is closed at 786. After 786,
container execs are unsafe.

**Build:**
1. **Select the policy, write the assessor, and run `prepare`.** In
   `fdictRerunAndVerifyWorkflow`, after `fdictClassifyRerunPaths`
   (about line 224) and **before** the clearing (226):
   - read the shadow's pinned `.vaibify/rerunAssessment.py` by typed
     read, and select the policy with `fsReadRecordedComparisonPolicy`
     (I9). An unknown ID gives the unrun outcome (`fdictUnrunOutcome`)
     with the "upgrade vaibify" wording;
   - create the scratch and evidence directories (attack surface item
     5);
   - write vaibify's render for that policy into the scratch directory,
     then run `prepare`.
2. **Clear with the shared code.** `fdictClearShadowBeforeRerun` keeps
   its signature, its classification, its two host-side refusals and
   `RerunPreparationRefusedError`. Only its in-container program
   changes:
   - replace `_S_PREPARATION_PROGRAM` by running the rendered file's
     `clear`, with the same job;
   - the existing `rerunPreparation` tests must stay green unchanged;
     any that pinned the old program text are re-pointed, never
     deleted.
3. **Thread evidence and invocation identity through the runner.** This
   is active only when the rerun lane asks for it; ordinary runs build
   byte-identical command strings, and existing tests pin that.
   - `fiRunAllSteps` (`pipelineRunner.py:1801`) takes
     `dictRerunEvidence=None`, holding `sEvidenceDirectory` and an empty
     `listLedger`. `fbRunWorkflowInContainer` passes it.
   - `_ftPrepareLogAndVariables` (1719) puts it into `dictVariables`
     under a new key.
   - `_ftRunCommandList` (307-340) takes `sInvocationScope=""`, for
     example `"A03.data"`. When the evidence key is present it:
     - builds each command's ID with `fsBuildInvocationId`;
     - for test sections only, adds the `PYTEST_ADDOPTS` export after
       `sEnvPrefix`;
     - appends `(ID, exit status)` to `listLedger` once the command
       returns.
   - The step label reaches `ftRunStepCommands` (732) from
     `_fiRunStepList`'s `sStepLabel` (1507-1550). The three sections
     pass their scopes:
     - data: `_ftRunSetupIfNeeded`, 823;
     - plot: the call at 777;
     - each test group: `pipelineTestRunner._fdictRunOneCategoryCommands`
       (70), mapping `dictIntegrity` to `integrity`, `dictQualitative`
       to `qualitative`, `dictQuantitative` to `quantitative`, and
       `legacy` to `legacy`.
4. **Run `assess`** after the steps and before the comparison (about
   lines 239–247):
   - write `listLedger` as `invocations.tsv` into the evidence
     directory;
   - from Phase 5 on, write the Stage A and B records into the scratch
     directory;
   - run `assess --machine`, and parse the final prefixed line.
   - Thread the parsed report into `fdictVerifyRerunOutputs` as a new
     keyword argument.
5. **`fdictVerifyRerunOutputs`:**
   - keep its typed-read hashing (`_fdictHashEntriesOrNone`) as the
     host's hash cross-check;
   - also cross-check every `listInvocations` exit status against the
     host's own `listLedger`;
   - re-derive every derived field with `fdictRederiveAssessment`;
   - on any disagreement, return "Could not run: inconsistent
     assessment";
   - on success, return:
     - `bPassed = (iExitCode == 0)`, kept for existing readers;
     - the report;
     - `listFileOutcomes`, extended with the per-output outcome, so
       `manifestWriter.fsRenderReproducedManifest` still renders
       `REPRODUCED.sha256`;
     - the tally.
   - **A run that ran and failed is still a graded result.** Today a
     non-zero rerun sets `bRerunAttempted` true and `bPassed` false;
     under the new model it is Failed (exit 1), with
     `dictRerunFailure` still recorded. Only a rerun that could not
     start or could not be graded is unrun.
6. **Verdict constants.** In `reproductionReport.py`:
   - add the verdict constants for byte-identical, comparisons passed,
     failed, insufficient evidence and inputs changed beside
     `S_VERDICT_*` (69-71);
   - derive the verdict from the assessment in
     `fdictBuildReproductionReport` (106-112);
   - render `sVerdictRendered` in `fsRenderVerdict` (217);
   - bump `I_REPORT_SCHEMA_VERSION` (66).

   **Remove the duplicated verdict strings in `reproductionLabel.py:73-74`
   and `159-166`** by importing the constants. That duplication is a
   drift bug waiting to happen.
7. **The attestation.** In `l3Attestation.py`:
   - set `I_SCHEMA_VERSION = 7` (80);
   - add `_fdictMigrateAttestationV6ToV7`, which writes `None` for the
     new fields and sets `bHistorical: True`;
   - store the report, the tally, `sComparisonPolicy` and the profile
     fields. The profile fields are `None` until Phase 5; reserve them
     now.
   - `fbL3AttestationCurrent` (291-307) additionally requires all of
     (design F11, D7, I9):
     - schema 7 or later;
     - not historical;
     - `sComparisonPolicy == S_CURRENT_COMPARISON_POLICY`.

   Update both builders: `reproducibilityRoutes._fbPersistAttestation`
   (1470-1532) and `commandReproduce._fdictBuildRerunAttestation`
   (808-872).
8. **The lanes:**
   - **dashboard:** `_fsPhaseForOutcome` (`reproducibilityRoutes.py:1240-1249`)
     and `_fbRecordOutcome` (1347-1385), keeping the rule that a
     no-verdict outcome is never written as an attestation;
   - **CLI `--rerun`:** `_fnReportHashCompare` and
     `_fbWriteAttestationFromRun` (`commandReproduce.py:892-1002`);
   - **CLI `--from`:** exit with the assessor's D6 code instead of the
     non-REPRODUCED exit 1 (1208);
   - **dashboard published reproduction:**
     `reproductionRoutes._fdictRerunAndReport` (314-365);
   - **`reproductionRecord.flistWriteVerificationOutcome`:** keep the
     write order (reproduced manifest, then the record) and update the
     status mapping (163).
9. **`levelGates`:**
   - the "Rebuild attestation" requirement reads the new currency rule;
   - a historical attestation, or one made under an older policy,
     reads as such, never as current (`fdictL3ReadinessGaps` 1673-1675,
     `_fdictL3WorkflowChecks` 3208-3231);
   - update `pipelineRoutes._fdictSummarizeAttestation` (2783-2810) so
     the row carries the verdict, its tally, its policy and the
     historical flag.

**Tests.** Falsification entries unless noted.
- **The precedence end to end through the shadow lane**, on real
  directories made distinct (`tests/testRerunVerifiesWhatItRan.py` is
  the model). Cover:
  - a failing test with byte-identical outputs;
  - a skipped test;
  - a deleted test file;
  - a tolerance edited mid-run;
  - a data command that exits 0 and writes nothing, which must give
    Failed (missing);
  - a data command that exits 1 in an early step, whose later steps
    still run and are graded.
- **Stale evidence cannot pass.** A shadow seeded with the author's
  passing markers whose tests do not run must give insufficient
  evidence.
- **Host re-derivation catches a forged verdict.** Mutate the
  assessor's `sVerdict` while leaving its observations alone; the lane
  must answer "Could not run: inconsistent assessment". Do the same
  for one `iExitStatus` that disagrees with the host ledger.
- **The recorded policy is replayed (I9).**
  - With a fixture, register a test-only `comparison-vtest` whose
    threshold is 0, and make it current.
  - A project whose pinned file records `comparison-v1`, rerun with a
    1-level figure difference, still gives "comparisons passed". Its
    attestation is not current.
  - A fresh render under `comparison-vtest` gives insufficient
    evidence for the same figure.
  - A recorded `comparison-v9` is unrun, with the upgrade wording.
- **A historical attestation never satisfies the new requirement**,
  even when the manifest digest still matches.
- **Exit codes** for `--rerun` and `--from`. Update
  `tests/testReproduceCommand.py:150-311` and `477-546` where the codes
  are pinned, and re-point their falsification entries.
- **Ordinary runs are unchanged:** without `dictRerunEvidence`, every
  command string equals today's.
- **A `docker_live` leg:**
  - the shadow really runs `prepare`, `clear` and `assess` under its
    own admission;
  - the figure is stashed before the clearing;
  - the evidence directory is writable by the steps' user.
- **Ledgers:** regenerate them, and dispose the new exec and write
  rows.

**Done when** all four lanes report the new verdicts and the old
attestation reads historical. The UI may still show raw verdict
strings until Phase 3.

---

## Phase 3: verdicts and attestations on screen (frontend)

**Goal.** Every surface renders the design's wording, the tally, the
policy and the historical state. None of them re-derives a verdict
(`dashboard-state-honesty`).

**Build** (functions from the survey; check the current names):
- **`scriptFileOutcomes.js`:** `_DICT_STATUS_WORDS`, `_DICT_STATUS_RANK`
  and `_fsOutcomeWord`, for the per-output outcomes, the image-check
  label "within vaibify's image-difference threshold (policy v1)", and
  the "cannot assess" reasons.
- **`scriptProofTab.js`:**
  - `_fsRenderL3AttestationCard`, `_fsRenderAttestationDetails` and
    `_fsRenderLatestReproductionCard`: render `sVerdictRendered`,
    never the raw constant;
  - `_fsRenderL3HistoryTable` and `_fsBuildHistoryStatusIconHtml`:
    historical rows are labeled "previous check";
  - `listReproductionHistory` is rendered nowhere today. Render it, or
    record in the PR description why not.
- **`scriptWorkflowRequirements.js`:** `_fsDescribeAttestation`,
  `_fsRenderAttestationFailure` and `_flistAttestationRows`.
- **`scriptReproducePublished.js`:** `_fnRenderResult` (the class switch
  at 495-496) and `_fsRenderVerdict`.
- **`scriptApplication.js`:** `_fsRenderAttestationDocument` and
  `_fnRenderToolbarReproductionBadge`.
- **The IIFE rule in `vaibify/gui/static/AGENTS.md`:** mutate shared
  collections in place, never reassign them.

**Tests:** browser tests on a seeded workflow, modeled on
`tests/browser/testAReadersVerificationLeavesTheAttestationRowStill.py`.
- the tally is always beside a comparison verdict;
- a comparison verdict never reads "every file byte-identical";
- the image-check label is present;
- a failed command is named;
- a historical attestation, and one made under an older policy, show
  as such;
- `pageDashboard.listPageErrors == []` throughout.

Run the browser lane locally. If you cannot load a browser, push and
let the draft PR's browser lane run it, and say so in the PR
description (`CLAUDE.md`).

**Done when** the browser lane is green and every new assertion has a
KILLED falsification entry.

---

## Phase 4: `reproduce.sh` parity

**Goal.** A reader's `reproduce.sh` runs the same procedure and gives the
same verdict and exit code as the dashboard on the same tree.

**Build:**
1. **Envelope membership for `.vaibify/rerunAssessment.py`.** All four
   changes land together:
   - add it to `publicationScope.TUPLE_LEVEL3_ENVELOPE_PATHS`
     (`publicationScope.py:171-180`) and bump
     `I_PUBLICATION_SCOPE_VERSION` (165). The comment at 194-195 calls
     this a ladder change; the researcher's approval of the design
     covers it;
   - add it to `repoFiles.TUPLE_SNAPSHOT_CONTENT_PATHS`
     (`repoFiles.py:815-834`), or the poll raises a `KeyError` and
     returns 500. `tests/testPublicationScopeSeparatesTheLevels.py`
     pins this;
   - add it to `stateContract.TUPLE_ROOT_CONFIG_FILES`
     (`gui/stateContract.py:73-103`) so it can be committed. That tuple
     also feeds `gitRoutes.TUPLE_CURATED_COMMIT_KINDS`.

   Because it joins the envelope, `rerunPreparation.fdictClassifyRerunPaths`
   protects it automatically, so clearing can never delete it.
2. **Writing it:**
   - `reproduceScriptGenerator.fsGenerateReproduceScript` (431-468)
     writes the rendered file, under `S_CURRENT_COMPARISON_POLICY`,
     beside `reproduce.sh` through `connectionDocker.fnWriteFile`;
   - the route `_fdictWriteScriptThenRepinManifest`
     (`reproducibilityRoutes.py:1759-1784`) writes both before
     `_fbRepinManifestOrWarn`, so the manifest pins both;
   - `fsRenderReproduceScript` (479-496) calls
     `fdictBuildAssessmentPlan` first, so an ambiguous or unsafe
     classification refuses to render, as unresolved tokens already do
     (`_fnRejectUnresolvedTokens`).
3. **The container half** (`_fsBuildContainerPreamble`, 385-422, and the
   body), in this order:
   1. `set -euo pipefail`, plus an `ERR` trap that prints "Could not
      run: reproduce.sh failed at line $LINENO" and exits 4. Commands
      in an `if` condition do not trigger it, which is how step
      commands are wrapped below.
   2. Create the scratch and evidence directories with `mktemp -d`
      outside `/work`, removed by the existing `trap ... EXIT` (keep the
      matplotlib salt directory's literal naming).
   3. Install from the lock. The existing `pip install` line keeps its
      flags and now exits 4 with a "Could not run" line on failure.
   4. `prepare`. If it exits non-zero, exit with its code.
   5. Print what will be deleted and how to restore it (I11). Then run
      `clear`; if it exits non-zero, exit with its code.
   6. From Phase 5 on, `capabilities`, the validated `unset`s, and
      `probe`.
   7. **The steps**, mirroring `ftRunStepCommands` and `_fiRunStepList`
      exactly (fact 24), for every step in
      `flistSelectStepsTheRerunExecutes` order:
      - create the plot directory, as the runner does (765);
      - run the data commands in order (the rerun ignores `bPlotOnly`),
        and stop the section at the first non-zero exit;
      - only if every data command succeeded, run the plot commands in
        the same way;
      - only if the plot section succeeded, run the test groups in
        `fdictResolveTestCommandGroups` order. Stop each group at its
        first failure, but run every group;
      - **continue with the next step regardless.**

      Each command runs as
      `if ( cd <quoted step directory> && <command> ); then s=0; else s=$?; fi`.
      For test sections, the `PYTEST_ADDOPTS` export comes inside the
      subshell before the command. Then
      `printf '%s\t%s\n' <quoted ID> "$s" >> "$sEvidence/invocations.tsv"`.
      Each command gets its own subshell, because the runner gives each
      command its own exec and shares no shell state between commands.

      **Match the runner's shell options too.** The runner runs each
      command as `/bin/bash -c <command>` with bash's defaults
      (`dockerConnection._fdictBuildExecCreateKwargs`): no `-e`, no
      `-u`, no `pipefail`. So the subshell runs `set +u +o pipefail`
      before the command; `-e` is already off inside an `if`
      condition. Today's script runs step commands under
      `set -euo pipefail`, so a command such as `a | tee log` can exit
      differently in the two lanes. This fixes that existing
      divergence; pin it with a container-half test.
   8. `assess`, which prints the verdict and tally. `exit` with its
      code.
   9. Remove the unconditional `sha256sum -c` (`_S_SCRIPT_EPILOGUE`,
      425-428).
4. **The host half** (286-384):
   - change the `exit 2`s (295 and 361) to `exit 4`;
   - capture the final `docker run`'s status without letting `set -e`
     exit on it. Pass 0, 1, 3, 4 and 5 through; map anything else
     (125, 126, 127, or a signal) to a "Could not run: docker exited
     <n>" line and exit 4.
5. **Command rendering:**
   - gather each step's commands per section, substituted exactly as
     today (`_flistGatherStepCommands`, 630-649). Gather the test groups
     with `workflowManager.fdictResolveTestCommandGroups`
     (`workflowManager.py:2899`);
   - render IDs with `fsBuildInvocationId`, so the script and the plan
     can never name a command differently;
   - update `pipelineUtils.T_EXECUTED_COMMAND_KEYS` and its comment
     (I5);
   - the step text stays inside the quoted heredoc, and
     `_fnRejectDelimiterForgery` still runs over the whole script.
6. **Currency:**
   - `levelGates.fbVerifyReproduceScriptCurrent` (1568-1609) compares
     the rendered file as well as `reproduce.sh`, by exact text against
     the render under the current policy (I9);
   - `fbVerifyReproduceScript` (1535-1565) also requires the rendered
     file to be present and pinned;
   - expect every existing project's `reproduce.sh` to read stale after
     this phase. That is correct, and it is regenerated in Phase 9;
   - wire the new failure into the same rows that render
     `reproduce-script-stale` (`pipelineRoutes.py:3490-3495`,
     `levelOrdering.py:240-245`).
7. **The preserved contracts** (`reproducibility-lane` skill,
   "reproduce.sh carries the SAME determinism guarantees"). Do not
   loosen any of them:
   - the epoch from the envelope, passed by `-e`;
   - the shared salt builder;
   - `--entrypoint bash`;
   - the quoted heredoc and its single-delimiter guard;
   - `--platform` from the envelope.

**Tests:**
- **Host-half observation:** extend `tests/testReproduceScriptDeterminism.py`
  (a fake `docker` on PATH, argv recorded) to cover `exit 4` on an
  unobtainable image, and on a fake `docker run` exiting 125.
- **The container half under real `bash`, without Docker.** Extract the
  heredoc body and run it with `bash` in a temporary repository on the
  host, with a fake `pip` on PATH and `/work` substituted. This makes
  the following cheap, and each is a falsification entry:
  - **a data command `true` that writes nothing gives exit 1, naming
    the output as missing** (the review's case);
  - a failing data command in step 1 still runs step 2, and the ledger
    shows step 1's plot and tests absent;
  - a failing test command still produces exit 1 with a printed tally,
    not a bare shell abort;
  - a failing `pip`, or a `false` injected into the preamble, gives
    exit 4;
  - the ledger lines equal the IDs the plan lists, in order;
  - a carried output and the pinned files survive `clear`;
  - a data command `false | true` records exit 0, as the runner's
    `bash -c` does, and one that reads an unset variable is not
    aborted by `set -u`.
- **Parity at the source:** the shadow lane and the generated project
  file carry byte-identical text for the same policy and workflow.
- **A `docker_live` parity leg:**
  - generate a project whose image has `python3`, `pytest` and Pillow;
  - run the real `reproduce.sh` and the shadow lane on the same tree;
  - cover byte-identical, comparisons passed with byte differences,
    failed, insufficient evidence, and a command that writes nothing.
    Each must produce the same verdict and exit code, and the same
    invocation statuses;
  - call `fnRequireDaemonReachable()` first, and add the
    `_fbBindMountReachesTheDaemon` skip the existing live leg uses.
    Never skip green.
- **Currency:** the criterion fails on a hand-edited rendered file
  (falsification).

**Done when** dashboard and script parity holds in the live leg, the
container-half tests pass, and every new entry is KILLED.

---

## Phase 5: execution profiles

**Goal.** Requested settings are applied in every lane after a
capability check, observed by a probe, recorded in the envelope, and
replayed. The profile comes from `dictDeterminism["sExecutionProfile"]`;
when it is absent, `speed-v1` is today's behavior, with no settings.

**Build:**
1. **`vaibify/reproducibility/executionProfiles.py`**:
   - an append-only table. Each profile has `dictRequested`,
     `sRequiredLevel` and `listFloorKeys`:
     - `speed-v1`: nothing requested, no required level, no floor keys;
     - `across-v1`:
       - requested: `OPENBLAS_CORETYPE: "Haswell"`,
         `NPY_DISABLE_CPU_FEATURES: "AVX512_ICL,X86_V4"`,
         `OPENBLAS_NUM_THREADS: "1"`, `OMP_NUM_THREADS: "1"`;
       - required level: `x86-64-v3`;
       - floor keys: `OPENBLAS_CORETYPE` and `NPY_DISABLE_CPU_FEATURES`;
       - no glibc variable (design fact 21);
   - the key allowlist and value pattern (attack surface item 2);
   - builders for the container shell exports and the host overlay;
   - the pure decision function used by Stage A.
2. **Stage A, then the prefix, then Stage B, in the runner** (I10):
   - in `_ftPrepareLogAndVariables` (1719), before
     `_fnInjectDeterminismEnvPrefix` (called at 1735-1738), run Stage A
     in the container. Use `python3 -I -c` with a program rendered from
     `rerunProcedure`'s capability functions, so the runner and the
     script run one text. Model it on how `introspectionScript` ships a
     program, but keep it a plain string, not an f-string;
   - `_fnInjectDeterminismEnvPrefix` (`determinismEnvironment.py:336`)
     then appends only the applied settings to the container prefix
     and the host overlay;
   - then run Stage B under that exact prefix, and record its
     observations;
   - **host mode:**
     - Stage A reads the host's `/proc/cpuinfo` where one exists;
       elsewhere it reports "capabilities unreadable on this host", and
       the floor keys are not applied;
     - the thread pins always apply;
     - Stage B is not run, because the helper interpreter is vaibify's
       own `python3` and would describe the wrong NumPy (the
       `helperPrograms.py` docstring). It records "probe not run in host
       mode".
3. **`routes/testRoutes.py` builds its own prefix** (227-265, 848-858).
   Run the same Stage A decision there. Otherwise "Run tests" from the
   dashboard runs under different settings from a pipeline run.
4. **The not-applied path:**
   - record "floor not applied: this processor lacks x86-64-v3";
   - for the author, surface it before running, as a run-start warning
     like `_flistCollectPreflightWarnings`.
5. **Recording:**
   - each run keeps requested, applied and probe-observed settings
     apart (F7);
   - the envelope gets `dictExecutionProfile`, holding `sProfileId`,
     `dictRequested`, `sRequiredLevel` and `listFloorKeys`, from
     `dataArchiver._fdictBuildEnvironmentPayload` (217-258). The policy
     is recorded in the rendered file, not here (I9);
   - only non-volatile values go there.
     `environmentSnapshot.fbEnvironmentPayloadMatches` (902-917) skips
     writes when the payload is unchanged, and a run-specific value
     would rewrite a pinned file on every regeneration.
6. **Replay:**
   - **the shadow lane** applies the envelope's `dictExecutionProfile`,
     never the current table's lookup of the answer (F9). It goes
     through the runner's Stage A, so a reproducer's processor that
     lacks the level drops the floor keys;
   - **`reproduce.sh`:**
     - the host half reads the profile with `jq`, validates it against
       the allowlist (rendered from the same tuple), and passes it as
       `-e KEY=VALUE` in a bash array. Use the guarded expansion idiom,
       because an empty array under `set -u` fails on bash 3, as the
       platform array already handles;
     - it passes `sRequiredLevel` and `listFloorKeys` the same way, as
       `VAIBIFY_REQUIRED_LEVEL` and `VAIBIFY_FLOOR_KEYS`;
     - in the container, `capabilities` runs with every allowlisted key
       unset through `env -u`. Each key it prints is checked against a
       literal `case` of the allowlist, then `unset`;
     - `probe` runs after that.
7. **Retirement (F8).** The attestation stores `sProfileId` and
   `dictRequested`. `fbL3AttestationCurrent` also requires them to equal
   the workflow's current profile.

**Tests:**
- **The settings are really requested.** This is a `docker_live` test.
  - Under `across-v1`, a generic linear-algebra fixture in a container
    restricted to two cores with `--cpuset-cpus` gives the same hash as
    with all cores.
  - Under `speed-v1` the hash differs when the core counts differ
    (design fact 18).
- **Order (I10), a falsification entry.** Record the environment each
  stage sees. Stage A's process has none of the allowlisted keys;
  Stage B's and the steps' have exactly the applied ones.
- **The not-applied path:**
  - a simulated `v2` capability result drops the floor keys, keeps the
    thread pins, and records why;
  - neither the probe nor any step ever receives a floor key.
- **Absent libraries:**
  - an image without NumPy gives "NumPy not installed";
  - NumPy whose BLAS is not a bundled OpenBLAS gives "OpenBLAS not
    detected";
  - neither changes the applied settings.
- **Replay is frozen.** Add a test-only `across-v2` to the table. An
  envelope recorded under `across-v1` still replays `across-v1`'s
  values.
- **The allowlist holds:**
  - an envelope with `LD_PRELOAD` gives exit 4, naming the key;
  - a value with `$(` gives exit 4;
  - a floor key outside the allowlist gives exit 4;
  - all three are enforced by the script and by the host.
- **Retirement:** changing the profile retires the attestation, and the
  history keeps it.
- **The cross-machine fixture hash is committed.** CI runners must
  reproduce it under `across-v1` and log their CPU (design 0e). If no
  runner has AVX-512, record that half as a falsification entry and say
  so in the PR description.

**Done when** every lane applies, records and replays `across-v1` after
its capability check, and every test above is KILLED.

---

## Phase 6: one question replaces three

**Goal.** The researcher answers one question. The answer stores a
profile ID, and every surface shows it.

**Build:**
1. **`determinismGate.py`:**
   - `LIST_DETERMINISM_QUESTIONS` becomes one entry:
     - `sKey: "repeatability"`;
     - `sAnswerKey: "sRepeatabilityAnswer"`;
     - `tAnswers: ("speed", "across")`;
     - `sValueKey: "sExecutionProfile"`;
     - `tAnswersNeedingValue: ("speed", "across")`.
   - The value is written by vaibify when the researcher answers, never
     typed. Use the design's wording, with no schema keys.
   - **Keep the legacy constants** under a "legacy, read only by
     migrations" comment.
     `testTheMigrationSpellsTheSameKeysTheGateReads`
     (`tests/testDeterminismRowMatchesItsGate.py:326-362`) still
     inspects the v12→v13 migrator against them.
   - Keep `fdictDetectMathsLibrary`, but repurpose it to warn that MKL is
     present and not covered by any profile (design "Known limits").
2. **The routes** (`reproducibilityRoutes.py`):
   - the type table `_DICT_DETERMINISM_KEY_TYPES` (1978-1988);
   - `_fdictValidateDeterminismBody` (2032-2064), whose docstring is
     already stale;
   - the declare route (2067-2096) sets `sExecutionProfile` to the newest
     table version for the chosen answer.
3. **The wire** (`pipelineRoutes.py`): `_flistEnvelopeDeterminismQuestions`
   (3137-3166) works generically; drop or repurpose `dictMathsLibrary`
   (2969-2972).
4. **The agent catalog** (`actionCatalog.py`): update
   `declare-determinism` (779-796) and the already-stale
   `audit-determinism` (627-640). Keep each action's `bAgentSafe` as it
   is; `testAgentActionRegistered` and
   `tests/testAgentLaneEnforcement.py` must stay green.
5. **The JavaScript:**
   - `scriptWorkflowRequirements.js`: `_DICT_DETERMINISM_CONTROLS`,
     `_DICT_DETERMINISM_VALUE_FIELDS`, the form renderers and
     `_DICT_DETERMINISM_ANSWER_LABELS`;
   - `scriptApplication.js`: `_fdictBuildDeterminismBody`, which
     hard-codes `"dOmpNumThreads"` at 4693, and
     `_fdictDescribeDeterminismScan`;
   - `scriptProofTab.js:289-297`.

   The form has:
   - two radio options, with nothing pre-selected;
   - the "Recommended for most projects" label and the stated cost;
   - a collapsed "Why does this matter?".

   When an answer would retire an attestation, the form says so before
   saving.
6. **The CLI:** `commandReproduce._fdictAggregateAllWorkflows`
   (243-273) and the tier-4 check (520-521).
7. **Migration v13 → v14** (`gui/workflowMigrations.py`):
   - add `_fnMigrateV13ToV14`, append `(13, _fnMigrateV13ToV14)` to
     `T_MIGRATORS` (783-797), and set `I_CURRENT_WORKFLOW_VERSION = 14`
     (50);
   - it leaves the question unanswered and keeps the legacy keys
     (design D7);
   - this module may import only leaf modules, so spell keys as
     literals, and add a sibling of
     `testTheMigrationSpellsTheSameKeysTheGateReads` for the new
     migrator.

**Tests:**
- Rewrite the three determinism test files for one question:
  - `tests/testDeterminismGate.py`;
  - `tests/testDeterminismRowMatchesItsGate.py`;
  - `tests/browser/testDeterminismRowFollowsTheGate.py`, whose
    row-count falsification (`.slice(0,1)`) is meaningless with one row.
    Replace it with a falsification that the row renders the answer's
    label and the profile ID.

  Re-point their registry entries.
- Update the fixtures that hard-code the three-answer block:
  - `testLevelGateL3EndToEnd.py:219`
  - `testReproduceCommandAttestation.py:96`, `:517`
  - `testReproduceRerunHashCompare.py:93`
  - `testLevelGateL3.py:94`
  - `testBinaryDeclaration.py:41`
  - `testAiDeclarationAddStepAndDeterminismRoutes.py:264`
  - `testProofLadderUncoveredLines.py:213`
  - `testReproduceCommand.py:114`
  - `testRerunVerifiesWhatItRan.py:330`, `:496`
  - `testLevelGateL3Blockers.py:109`, `:423`, `:440`
  - `testReproducibilityRoutes.py:60`
- **Never delete a test to make it pass** (`CLAUDE.md`). A test that
  asserted the three-question shape is rewritten to assert the new
  shape.
- **Migration:** a v13 workflow with all three legacy answers comes out
  unanswered, with the legacy keys intact; a v14 workflow is unchanged.

**Done when** the browser lane is green, the agent-lane tests are green,
and the PROOF tab shows one determinism row.

---

## Phase 7: production-time provenance and two-computer messaging

**Goal.** Every output records how it was produced, only when it was
actually produced. A byte difference is explained, never predicted.

**Build:**
1. **Two hook points, one per section**, in
   `pipelineRunner.ftRunStepCommands` (`pipelineRunner.py:732-797`):
   - **data**, right after the data section succeeds (after the
     failure return at 774-775). Record the step's
     `saOutputDataFiles`, but only when the data section really ran:
     - not `plotsOnly`;
     - not a `bPlotOnly` step outside the rerun;
     - at least one non-empty data command.

     `_ftRunSetupIfNeeded` (823) returns `(0, 0.0)` both for success
     and for a `bPlotOnly` skip, so decide from those conditions, never
     from the exit code alone;
   - **plot**, right after the plot section (after 782, before the
     `plotsOnly` return at 783). Record the step's `saPlotFiles`, only
     when the section ran (not `dataOnly`), `iExitCode == 0`, and at
     least one plot command is non-empty;
   - **nothing else records.** A tests-only run, "Run tests" from the
     dashboard, a failed section and an interactive step all leave
     every record as it was.
   - **Resolve the paths with `manifestPaths.flistStepOutputRepoPaths`,
     restricted to the section's key** (the keys parameter from Phase
     1), so the records use the manifest's own path keys. Do not use
     `_flistBuildStepOutputAbsPaths` (987-1001), which returns both
     sections' files.
   - Hash with the typed read. A new call site is a ledger row.
2. **The record file:** `vaibify/gui/outputProvenance.py` owns
   `.vaibify/outputProvenance.json`, schema 1:
   - `dictRunContexts`, keyed by context ID, holds the Stage A decision
     and Stage B observations, the profile ID, the requested and
     applied settings, and a UTC time;
   - `dictOutputs`, keyed by repository path, holds `sSha256`,
     `sSection` and `sRunContextId`;
   - write it with a temporary file and a rename.
   - **It is tracked and never pinned:**
     - add it to the tracked files in `stateContract.py` and the curated
       commit kinds;
     - keep it out of `manifestWriter.flistManifestPathsToPin` and out
       of the envelope tuple, because the shadow's runner writes it too.
3. **The envelope copy** (`dataArchiver._fdictBuildEnvironmentPayload`):
   - for each pinned output whose provenance digest equals its manifest
     digest, copy its context into `dictOutputProvenance`;
   - otherwise write "production context unknown";
   - this changes only when outputs change, so
     `fbEnvironmentPayloadMatches` stays meaningful.
4. **Reports.** The assessment report and the attestation carry the
   rerun's context beside each byte-differing output's production
   context.
5. **Messaging** (JavaScript plus the poll payload):
   - **a step whose outputs were produced under a context that differs
     from this computer's current one** says "Produced under different
     execution settings or hardware", neutrally, and names the
     differing fields. It never says "a different computer";
   - **when "Manifest matches the files" fails only on such outputs,**
     the remedy offers "rerun every step here and regenerate the
     manifest" as an option, never as a refusal (F5);
   - follow `dashboard-state-honesty`: render the recorded facts; derive
     nothing.

**Tests:** falsification entries unless noted. Use real directories,
with the contexts differing in the field that matters (core count or
`x86-64` level).
- **No relabeling, by run kind:**
  - produce everything under context A, then under context B:
    - run tests only, and every record still reads A;
    - run plots only, and the plots read B while the data reads A;
    - run data only, and the data reads B while the plots read A;
  - a full run of a `bPlotOnly` step under B relabels only the plots;
  - a full run whose plot command fails under B relabels only the data.
- **"Production context unknown"** appears when the digests disagree.
- **No identifying fields are recorded:** assert the absence of
  `model name` and hostname in both files.
- **A browser test** for the neutral wording, asserting the exact
  phrase and that the row is not styled as a failure (not a
  falsification entry).

**Done when** a two-computer round trip, simulated with two contexts,
shows both contexts in the report and nothing styled as a failure.

---

## Phase 8: documentation and skills

1. **Docs:**
   - `docs/proofLadder.md`: the Level 3 table row, paragraph and
     requirement rows, per design D1;
   - `docs/quickStart.md` sections 6–7:
     - "Every file now matches" becomes the verdict a reader actually
       gets, with its tally;
     - the determinism question appears where the reader meets it;
     - `reproduce.sh` deletes and regenerates the declared outputs
       (I11);
   - `docs/reproducibility.md`:
     - integrity versus agreement, and agreement comparisons versus
       required checks;
     - precedence, and the evidence contract with its invocation
       accounting;
     - the image check, its supported encodings and its disclosed
       limit;
     - profiles, the capability check and their measured cost;
     - the frozen comparison policy, provenance and exit codes;
   - `docs/environmentsAndProjects.md` and `docs/architecture.md`: the
     one question;
   - `docs/lessons.md`: the war story (design Stage 6).
2. **`.claude/skills/reproducibility-lane/SKILL.md`**:
   - rewrite "Determinism is THREE questions, and answering is the
     criterion." (line 16) for one question and versioned profiles;
   - extend "`reproduce.sh` carries the SAME determinism guarantees the
     runner does…" (line 36) with profile replay, the capability-first
     order, clearing, test execution and the assessor;
   - update "The rerun grades only bytes it regenerated" (around 482) to
     the assessment model;
   - add the new do-not-undo contracts:
     - the procedure and frozen policy modules, and the same rendered
       bytes in both reproduction lanes;
     - JUnit-only evidence, bound to its step and command, with every
       command accounted for;
     - structural checks never establish agreement;
     - the precedence table;
     - the recorded policy, selected from vaibify's own code;
     - the frozen profile;
     - historical attestations;
     - the allowlist;
     - provenance per section.
3. **`.claude/skills/dashboard-state-honesty/SKILL.md`**: update "A
   requirement row renders the gate's VERDICT, never re-derives it."
   (line 55) for one row and the verdict tally.
4. **Checks:**
   - `python tools/checkAmericanSpelling.py`;
   - `python -m pytest tests/testSkillIntegrity.py -v`;
   - the trigger and outcome harnesses in `docs/skillTesting.md`.

---

## Phase 9: hand-off

1. **Before handing off,** on the branch:
   - run the full suite, the browser lane and every live leg;
   - reconfirm every falsification entry this work added
     (`--only` over each new test file);
   - run `monitor-pr` until the draft PR is green;
   - bring the PR description up to date, including every surface you
     could not verify.
2. **Give the researcher the design's Stage 8 checklist.** It is the
   acceptance test for D1–D8 and I1–I11. Record their results in the
   design's facts list.
3. **Before the PR is marked ready,** pin `comparisonPolicyV1.py`'s
   final hash. From the release on, it is frozen (I9).
4. **After their sign-off,** mark the PR ready for review. It merges to
   `main` only then.
5. **Re-releasing the QuickStart example (design Stage 9) is the
   author's work,** not this branch's:
   - answer the question;
   - rerun;
   - regenerate `reproduce.sh`, the rendered file, the envelope and the
     manifest;
   - commit, push, and run a fresh assessment.

   The image needs no new upload.

---

## Contracts this work must not break

From the `reproducibility-lane` skill unless noted; each has a
kill-confirmed test.
- **The epoch** is the envelope's recorded one, never re-derived from
  HEAD, in both lanes.
- **The matplotlib salt** comes from `fsBuildMatplotlibSaltShell`, never
  a private copy.
- **The coherent export** keeps both halves of its check, and the
  `.git/` exemption stays exactly `startswith(".git/")`.
- **A rerun that reached no verdict** is never written as an
  attestation (`bRerunAttempted`), and the two lanes agree.
- **The first rerun failure** is kept with a bounded tail, because it is
  published.
- **`REPRODUCED.sha256`** keeps its write order, and the manifest never
  pins it.
- **Carried outputs** are excluded through the same path helpers the
  manifest writer uses.
- **Clearing** (`rerunPreparation`, now shared by both lanes) refuses an
  ambiguous or unsafe classification by name, validates every target
  before deleting any, and never follows a symlink out of a directory
  it clears.
- **The shadow's own admission** wraps every shadow exec, and nothing
  execs after it closes.
- **The image is obtained** by registry pull, then archive, then a local
  copy, never reordered.
- **The three platform facts** stay apart by name.
- **The envelope pins the agent-free environment**, never the agents.
- **Ordinary runs are unchanged** by the rerun's evidence threading:
  without `dictRerunEvidence`, every command string is today's (this
  guide).
