# Lessons from past sessions

Specific mistakes made while building vaibify, each worth remembering
because the wrong answer was the natural one. This file is the long
form; `AGENTS.md` keeps only the handful that change how code is
written on an ordinary day.

The entries are append-only and roughly chronological. When you solve a
problem whose wrong answer was tempting, add a line here rather than to
`AGENTS.md` -- a rule that applies to one subsystem belongs in that
subsystem's skill, and a war story belongs here.

This section records specific mistakes made in past sessions that are
worth remembering. It is empty at initial commit. Add entries as they
come up — one line each, pointing at the offending pattern and the
correct approach.

- The pre-refactor claim route short-circuited to `bClaimed: True`
  whenever a container was already locked, silently admitting a second
  same-hub browser tab. A claim must ARBITRATE (unowned grant /
  same-lease idempotent / foreign 409), never unconditionally succeed;
  `testClaimRejectsForeignLease` guards this.
- A green test suite is not proof of a working guarantee: the
  one-session refactor's owner map was keyed by container *name* on the
  write path but looked up by container *id* at the WebSocket gate, so
  every real hub session would have failed closed — yet the suite stayed
  green because its fixtures used name == id and never drove the live
  WebSocket/viewer paths. When a behavior crosses the
  HTTP/WebSocket/container boundary, assert it with name != id and an
  actual connection, not a unit stub.
- Five parallel agents changed JavaScript in one session and none could
  load a browser; the merged branch was green and the frontend entirely
  unexecuted. A green Python suite says nothing about the frontend.
  Load the page — see "Required after JS changes".
- `VaibifyApp` and friends are declared with `const`, which creates a
  global *lexical* binding, not a `window` property. A probe using
  `window.VaibifyApp` reports `undefined` for a module that is working
  perfectly. Use the bare identifier.
- A startup sweep deleted every "stale" credential file in
  `~/.vaibify/tmp`, including one an existing container had
  bind-mounted for months, leaving it unstartable (Docker fails the
  mount and creates a directory stub where the file was). Age is not
  evidence that a host file is garbage — reachability is. Before
  deleting anything under `~/.vaibify`, ask the daemon what it still
  mounts.
- A guarantee stated only in prose is not enforced, and mutation
  testing cannot find it: there is no mutant for a guard that was
  never written. `bAgentSafe` was metadata, the force-push hook missed
  every ordinary invocation order, and the science-name scan could not
  match the identifiers it existed to catch — all three passed CI for
  months. When adding a rule here, name the test that fails when it is
  broken.
- An import is not a smoke test. The release workflow validated every
  distribution with `import vaibify; print(__version__)`, which passes
  for a wheel containing no templates and no Docker build context —
  exactly what every wheel contained. The failure was not subtle
  (`vaibify init` printed "No templates found") but it was invisible
  to CI, and `init` exited 0, so even a human running it saw success.
  When a check exercises a component, ask what a *user* does with it.
- A template nobody executes is not a template. The shipped `workflow`
  template put `GenerateSamples` in a directory named `Sampler` —
  which vaibify's own slug contract forbids — and invoked two scripts
  that existed nowhere in the repository. Every new project from it
  opened red and could not run. Three template tests existed; all
  three checked token syntax, none loaded or ran the thing.
- Fixing the instance is not fixing the class. The packaging fix
  repointed the templates and the build context, added a locator, and
  shipped a distribution check — and missed the shell completions,
  which had the identical bug two directories away
  (`dirname(dirname(__file__)) + "completions"`, resolving to a path
  present in no install *and no checkout*). Completion had never
  worked for anyone, and first-run setup wrote a permanent marker
  saying it had. After fixing a resolution bug, grep for every other
  way the codebase reaches outside the package, not just the spelling
  that bit you.
- A marker that records "setup done" must not be written after a step
  that silently did nothing. It is checked forever, so one bad write
  makes the defect permanent for that machine.
- A CI step that reports success for having run nothing is worse than
  no step. The live-Docker job was guarded by `docker info || exit 0`,
  so an unreachable daemon turned it green; `pytest -m docker` was
  selected by no job at all. Both looked like coverage on the workflow
  list. When a check can be skipped, ask what the skip reports.
- A source-mutating tool must not share a working tree. Running
  `reconfirmFalsification.py` beside any other pytest run made that
  run read half-mutated source, producing failures in tests nobody had
  touched — twice diagnosed as flakiness or test-order leakage, which
  is the natural wrong answer. It now mutates only inside a disposable
  git worktree, and refuses a dirty checkout unless
  `--include-local-diff` is passed. Note the diagnostic: with no
  randomization plugin installed, pytest's collection order is fixed,
  so two identical-order runs failing in *different* tests cannot be
  an ordering problem — that pattern means something outside pytest is
  editing the sources.
- **A count of code facts written into this file is wrong within
  weeks.** The carrier migration's route totals were re-typed and wrong
  four times in a single session (14/116, 31/99, 53/77, 60/70), each
  corrected only because somebody happened to notice; auditing for that
  found two more already stale — a "27 test files" that was 28, and a
  "20 unattributable rows" that migrations had reduced to 10. None was
  wrong in substance and all were wrong in fact, which is worse, because
  a reader who checks one and finds it false stops trusting the ones
  they cannot check. **State the mechanism, not the tally**, and where a
  number is genuinely wanted give the command that computes it —
  `PYTHONPATH=. python tools/carrierIntentAudit.py` for carrier
  coverage, `python tools/listModules.py` for structure. This is the
  deterministic-versus-stochastic split from
  [docs/vibeCoding.md](docs/vibeCoding.md) applied to this file: a fact
  that changes when the code changes does not belong in prose that
  does not.
- The carrier migration's only proof was unobservable in the tests that
  would have to observe it. "Forget a carrier and the primitive raises
  loudly" is true of the real `DockerConnection` and false of nearly
  every route test: **the route-test doubles answer a write by storing
  bytes and never consult the admission gate at all.** (Confirm with
  `grep -l 'def fnWriteFile' tests/test*.py | xargs grep -L
  mutationAdmission` — at the time of writing, all but one.) A migrated
  route with its carrier call deleted outright still passed its whole
  route-test file. The fix
  is `tests/testCarrierMigratedRoutes.py` — a double calling the same
  gates, under the same primitive names, at the same points the real
  connection calls them, recording the live admission MODE at each. Assert
  the mode, never merely that nothing raised: "no exception" is equally
  true of a route riding the ambient mint. Every future migration group
  needs an entry there; one verified against the ordinary route tests is
  not verified.
- An external review is evidence, not a verdict. A 2026-07-26 review
  correctly identified the browser/container execution hole and the
  doc drift, and was wrong about the falsification suite being
  order-unstable. A parallel audit over-reported the fixture-collapse
  sweep: of three flagged key pairs only one was real, because
  `sName`/`sDirectory` are *required* to agree by the slug contract
  and no code ever compares `sVersion` to `sExpectedVersion`. Check
  each claim against the source before acting on it; manufacturing a
  change to match a confident report is worse than ignoring it.
- **A falsification mutation detected through a SIDE EFFECT goes inert
  the day that mechanism is deleted, and nothing fails.** Three
  ownership-transfer guards are checked twice on purpose — once before
  anything is minted, once at the commit point — so disabling one copy
  changes nothing a caller can observe. They were killable anyway via
  the DRAINING phase: a doomed transfer must not drain the sitting
  owner's terminals. Deleting that phase (wave 2.4) silently made the
  discriminator vacuous, and for a week the harness reported three
  SURVIVED entries that read as three undefended ownership guards.
  Registry entries now carry `iExpectedOccurrences` so a guard with
  several copies has every one mutated. Two practices follow: when
  deleting a mechanism, grep the falsification docstrings that lean on
  it; and when an entry survives, first ask whether the mutation is
  *observable*, because "the guard is undefended" and "the mutation
  changes nothing" look identical in the report.
- A test that asserts an outcome plus a shared word does not identify a
  cause. The poison-transfer test asserted `S_TRANSFER_REFUSED` and
  `"reconcile" in sMessage` — both equally true of the live-terminal
  guard sitting below it, so the test was satisfied by a container
  carrying no poison at all. Where two guards can produce the same
  refusal, assert the text that names *this* cause.
- A check that can be skipped must say what the skip reported, and a
  check that is *timing out* is saying nothing at all. The macOS
  falsification legs had exceeded their 25-minute ceiling for weeks;
  raising it produced the first one that ever finished and immediately
  exposed seven entries that no daemon-less host can evaluate. A red
  lane nobody can read is indistinguishable from a lane that never ran.
- **A pull request whose base is not `main` runs no CI at all.** Every
  workflow is `on: pull_request: branches: [main]`, so a PR stacked on
  another open branch shows a clean, empty check list — the same shape
  as the older "a pushed branch with no PR runs no browser lane" trap,
  and just as easy to read as a pass. Retargeting the base does not
  trigger the workflows either; `gh pr close` then `gh pr reopen`
  does.
- **A git fixture inherits the machine's `init.defaultBranch`.** A
  host-mode test that pushed `HEAD:refs/heads/main` with `-u` passed
  on a laptop defaulting to `main` and failed all four falsification
  legs on runners defaulting to `master`, because the production
  `git push` was then handed a local branch and an upstream with
  different names. Pin it with `git symbolic-ref HEAD
  refs/heads/<name>` (works on every git version, unlike `init -b`),
  and reproduce a divergence like this locally with
  `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=init.defaultBranch
  GIT_CONFIG_VALUE_0=master` rather than pushing a guess at CI.
- **A browser-lane test can drive an event shape no browser sends.** A
  Shift+scroll handler was added so a researcher could reach the
  terminal scrollback while an agent held the mouse, and its
  browser-lane test passed in Chromium and Firefox — while the feature
  scrolled nothing at all for every real user. Browsers remap a shifted
  wheel onto the HORIZONTAL axis (`deltaY` 0, magnitude on `deltaX`);
  Playwright's `mouse.wheel` writes `deltaY` whatever the modifier
  state, because it injects at the DOM layer and the remap happens
  below it. The handler read `deltaY`, measured zero lines, and then
  still claimed the event — so the "fix" SUPPRESSED the scroll the user
  would otherwise have had. Driving a real gesture through a real
  browser is not the same as driving the event that gesture produces:
  where an input-layer transform sits between the two, construct and
  dispatch the event yourself. The same caution applies to touch, IME
  composition, and any modifier that changes an axis.
- **Agent-facing docs that specify procedure without semantics produce
  confident invention, not questions.** Four wrong answers reached a
  researcher in one session — qualitative tests are "user-only", an
  unattested AI Declaration blocks L1, making figures standards enables
  qualitative tests, and `iProofLevel` recited at a scientist — and
  every one came from guidance that said how to DO things and never
  what they ARE. The container docs named the three test tiers as a
  list of three words and never defined them; the L1 rule was stated
  without its carve-out; the blocker list existed only on the
  dashboard's poll path, so an agent asked "why am I not at Level 1"
  had raw JSON and nothing else. An agent will not stop at a gap, it
  will fill one. When documenting an action for an agent, state what
  the thing IS and what it does NOT establish, and give it a call that
  returns a verdict rather than fields to infer one from.
- **A local rename re-fingerprints ledger rows, in more than one
  ledger.** Renaming `sFullPath` to `sAbsolutePath` inside one function
  invalidated its row in `mutationInventory.json` (keyed on the
  enclosing scope) AND in `hostCapabilityInventory.json` (keyed on the
  command expression itself). Regenerating the first left the second
  drifted, and `--check` on one ledger reads exactly as clean as
  `--check` on all three. Worse, the first regeneration silently
  dropped that row's recorded security review — it came back
  `UNCLASSIFIED`, drift printed `{}`, and only
  `testTheUnclassifiedBudgetOnlyEverShrinks` noticed. After touching
  any function that composes container commands, regenerate and
  drift-check EVERY generated ledger, and diff the disposition count
  before and after.
- **A guard can be credited with a property the surrounding code
  already supplies, and then it is not a guard.** `_fsLevelCellState`
  checked `bUnknown` ahead of the counts, documented and tested as
  what keeps a stale verify cache out of `attained`. It never did:
  `iSatisfied` counts only `bMet is True`, so an unknown requirement
  already forces `iSatisfied < iTotal` and attainment is impossible by
  the arithmetic alone. All the short-circuit did was erase a
  researcher's positively-verified GitHub mirror behind a "?" because
  Zenodo had never been checked. Three tests defended it and none
  would have failed if the property were gone, because they asserted
  the MARK ("unknown") rather than the property (never `attained`).
  Before preserving a guard, find the line that actually supplies the
  thing it is credited with; and assert properties, not the symptom
  you expect a property to produce.
- **A constant that must equal a derivation has to be pinned to it,
  not re-typed.** `fdictBuildAiDeclarationStep` defaulted `sName` to
  "AI Declaration" and `sDirectory` to the independently-typed
  `"aiDeclaration"`, while the slug contract derives `"AIDeclaration"`
  from that name. Every declaration step vaibify built was therefore
  born violating vaibify's own contract, and the dashboard painted it
  a red error telling the researcher to rename a step the product had
  just created for them. Nothing caught it because creation validated
  the name and the directory independently — the guard existed only on
  the rename path. Two rules: pin the relationship
  (`assert CONSTANT == fsDerive(OTHER)`) rather than the spelling, and
  when a contract is enforced on edit, check whether creation enforces
  it too.
- **A projection that narrows its input set turns a proved failure
  into a green check.** The L2 reverify compared every canonical path
  against GitHub, recorded the AI declaration file as diverged in the
  project's cached sync status — and the per-step projection then intersected
  that divergence list with `_flistStepOutputFiles` alone, so no
  blocker was emitted and the row rendered "Outputs match the GitHub
  mirror". The gate whose purpose is not to overstate publication was
  asserting a match about a file it had just proved did not match.
  Two things to carry forward. When a comparison and its display are
  separated by a projection, the projection's set must be a SUPERSET
  of the comparison's — check that relationship explicitly, because
  both stages look correct in isolation. And a *narrow but accurate*
  label ("Outputs") reads as a broad claim once it sits in a
  requirement row, so it hides the gap rather than disclosing it.
- **A threaded parameter can be accepted and dropped, and every call
  site still reads correctly.** Wiring the GitHub verify cache into
  the badges meant adding one argument to four functions in a chain.
  The third link took it into its signature with a default and then
  called the fourth without it — so every badge silently read
  `unknown`, the honest-but-wrong answer, while a reader checking any
  individual function found nothing amiss. Signatures agreed; the wire
  was cut. Nothing about a green import or a green existing suite could
  see it, because the parameter's absence is indistinguishable from its
  default. Two habits: when threading a value through more than two
  hops, assert it arrives with a value the DEFAULT cannot produce; and
  distrust a change whose new tests all fail the same way, because one
  broken link and one wrong branch look identical from the assertion.
- **`git checkout <file>` to undo a kill-confirm mutation discards the
  fix with it.** Restoring from HEAD reverts every edit in that file,
  not the mutation — and the suite goes green afterwards, because the
  pre-fix code is what the pre-fix tests were written against. Copy
  the file aside and copy it back, and re-grep for a marker string
  from the change before believing the restore.
- **A fake that answers every input cannot exercise an adapter that
  refuses most of them.** `SnapshotRepoFiles` — the adapter the
  file-status poll passes — answers `fbIsFile` only for the paths one
  container exec sampled and raises `KeyError` for the rest, by
  design, because guessing would make a gate silently wrong. Every
  test of the new Level 3 envelope gate drove a hand-written fake
  whose `fbIsFile` answers anything, so all of them passed while the
  shipped gate raised on a dependency-declaration path, 500'd the poll, and
  blanked every badge and level cell on the researcher's dashboard.
  The tests and the code were both self-consistent and neither was
  the product. When a module has a permissive test double and a
  fail-closed production adapter, at least one test must drive the
  real one — and where a set in module A must be a subset of a set in
  module B, pin the relationship, because each edit looks complete on
  its own.
- **Run the mutations locally before pushing; the tool exists.**
  `tools/reconfirmFalsification.py --only <substring>` applies each
  recorded mutation in a disposable git worktree, runs the named test,
  and reports KILLED or SURVIVED — about a minute for a handful of
  entries, against ~20 minutes for a suite and far longer for a CI
  round trip. `--include-local-diff` replays uncommitted work; it
  otherwise refuses a dirty tree, because checking out HEAD would
  report on code you do not have. Three defects reached CI in one
  session that a single narrowed run would have caught, and the agent
  responsible had asserted that no local runner existed — the tool's
  own `--help` said otherwise. `testFalsificationRegistryIsWellFormed`
  is NOT this check: it verifies the mutation text still appears in
  the source, never that a test would notice the mutation.
- **Batch the small fixes; every one costs a full verification cycle.**
  A suite run is ~20 minutes locally and a CI round trip is longer, so
  finishing four small items in four passes spends over an hour to
  learn what one pass would have said. It also hides interactions: the
  blocker fix in one item is what turned another item's "no
  materialized force yet" into three red tests, and a batched run
  surfaced that immediately instead of a cycle later. "Deliberately
  scoped out" is a decision worth stating, but re-check it whenever a
  neighboring change lands — the force that was absent is often
  created by the very next commit.
- **Retargeting a falsification entry is authoring a new mutation, not
  bookkeeping.** When a refactor moves the code an entry points at,
  the natural reflex is to repoint `old`/`new` and move on — it feels
  like updating a reference. It is not: the replacement text has to
  parse AND still kill. One retarget dropped a closing quote
  (`f"(git diff --cached ` for `f"(git diff --cached --quiet || "`),
  producing an unterminated f-string; the static registry check passed,
  because it only asks whether `old` appears in the source, and CI
  reported `ERROR: mutation does not compile` an hour later. Three
  entries were retargeted in that change, two were kill-confirmed, and
  the one treated as clerical was the broken one. Verify every
  retarget the same way as a new guard: apply it, `ast.parse` the
  result, run the named test, see it fail, restore.
- **A new early return converts every downstream guard's test into a
  tautology, and only the marked ones are noticed.** Adding the
  scope-version check to `_fbCachedSyncStatusFullMatch` made it refuse
  fixtures before the SHA, freshness and divergence guards below it —
  and those tests kept passing, because `False` is what they already
  asserted. CI's mutation run flagged the three that were
  `@pytest.mark.falsification`; a fourth was an ordinary unit test and
  was invisible to every lane (verified: deleting the divergence guard
  outright left it green). When adding an early return to a gate, list
  what sits below it and check that each one's test still fails when
  its guard is removed. Shared fixture builders are a control here, not
  a tidiness exercise: a hand-typed fixture gains a newly-required
  field in five files and silently not the other fourteen.
- **A kill-confirmation that edits by non-unique text mutates the
  wrong site and reports a false negative.** `syncDispatcher.py` has
  three byte-identical `(git diff --cached --quiet || git commit …)`
  guards in three functions, so a `str.replace(old, new, 1)` aimed at
  the third silently hit the first. The suite went green, which reads
  exactly like "this guard is not load-bearing" — the conclusion that
  gets a guard deleted. Before believing a mutation survived, `grep -c`
  the target text: more than one occurrence means the edit landed
  somewhere unknown. Mutate by line number, or by text that includes
  the enclosing function's unique context, and print the mutated region
  before running.
- **The obvious assertion often cannot tell two shell chains apart.**
  For `a && b || c && d`, the natural test — "a failed `add` returns
  non-zero" — passes against BOTH the grouped and ungrouped forms,
  because a failed add with a clean index stops either way. The forms
  diverge only when the index is DIRTY: ungrouped, the failure falls
  through to the commit, which succeeds on unrelated staged content and
  pushes it. A docstring claiming the simple case kills the mutant was
  written and was false. When pinning operator grouping, find the input
  where the two parses actually differ, and verify the kill rather than
  reasoning about it.
- **A researcher's project STATE leaks into comments more easily than
  their science does.** The "never hard-code science-specific
  examples" rule is easy to obey for dataset and target names and easy
  to break for everything else: a comment justifying a fix was written
  as `the row reads "19 of 19 files matching"` — a count copied
  straight off the researcher's dashboard while debugging with them.
  It names no science, so it reads as harmless, and
  `testNoScienceSpecificIdentifiersInSource` cannot catch it (the seed
  list matches identifiers, not arbitrary numbers). It is still their
  project pinned into vaibify's source, where every future agent will
  read it as if it were a fact about vaibify. Write the general shape
  — "all files matching" — never the observed instance. The tell is a
  comment containing a number, path, or timestamp you learned from the
  session rather than from the code.
- **Making a gate scope-aware does not make the row beside it
  scope-aware.** The L2/L3 split taught the gates that a drifted
  `reproduce.sh` says nothing about whether the researcher's DATA is
  published — and the Published-copies row kept reporting the verify's
  aggregate counts and listing `reproduce.sh` among the files, so the
  screen went on making exactly the statement the gate had just been
  corrected out of. The researcher saw it immediately; the suite could
  not, because every test asserted the gate. After changing what a
  computation MEANS, grep for the other consumers of the same cached
  record — a summary, a count, a file list — and check each one
  against the new meaning.
- **A cached comparison is a claim about verify-TIME bytes, and read
  time is a different moment.** The envelope gate quoted a Zenodo
  verify's divergence list after the local `environment.json` had been
  regenerated — so the per-file badge (live hashes) showed red while
  the Level 3 cell (this cache) stayed green on the same screen
  (researcher-reported, 2026-09-01). A cached verdict about a file is
  only usable while the file still IS the bytes that were graded:
  record the local hash each path was compared AS
  (`dictComparedHashes` in `syncStatus.json`) and re-check it at read
  time, treating a mismatch or a pre-field cache as UNPROVEN.
  `test_an_envelope_regenerated_after_the_verify_no_longer_passes` is
  the kill-confirmed guard; `_fsEnvelopeStateFingerprint` keeps the
  blocker cache from masking the transition. The same session added
  the twin alert for the other direction of staleness — the envelope
  pinning an image the container no longer runs
  (`fdictAssessEnvelopeImageCurrency`, captured once at connect) —
  because both were found the same way: a rebuild landed, nothing on
  the screen moved.

- **Generated bookkeeping must never be able to overturn a
  scientific verdict.** The conftest vaibify generates writes its test
  marker in `pytest_sessionfinish`, unguarded, so a marker directory
  it could not create ended the session non-zero — over a run that had
  just printed `1 passed`. Every test tier of every step reported
  `exit 1` and the researcher read it as their science failing
  (2026-09-04). Two separate causes had to line up and each is its own
  lesson. The generated file had a container path written into it as a
  literal, which is the `/workspace`-as-constant trap arriving in
  GENERATED output where no source scan looks for it — a generated
  artifact needs the same review as source, and it must locate its
  root rather than be stamped with one. And the refresh that would
  have replaced it is memoized per hub PROCESS and runs only at
  connect, so a `git pull` reinstated the stale copy and reopening the
  project re-probed nothing: **a cache keyed on identity, over a file
  something outside the process can rewrite, is a correctness bug
  wearing a performance optimization's clothes.** Re-probe at the
  moment the content matters. Guarded by
  `tests/testConftestRefreshBeforeRun.py`.
- **A delegated agent that dies mid-edit leaves a half-applied diff
  that `git add -A` sweeps up as if it were finished.** One was cut
  off by an API limit after editing the wizard's page catalog: it
  had deleted a page-list constant the sandbox branch still referenced
  and added nothing that used the new pages, and the commit that
  followed carried that broken half. Before committing a tree another
  agent touched, diff its surface and confirm every symbol it removed
  has no remaining reference (`node --check` catches syntax, not a
  dangling name).
- **A `monkeypatch` guarantee stops at the process boundary, and the
  docstring will not say so.** `tests/conftest.py` promised that "no
  test can read, overwrite, or delete the researcher's real stored
  credentials" while patching only this process; over a hundred test
  files spawn subprocesses, each of which imported the real `keyring`
  and reached the real keychain. It surfaced as four macOS approval
  dialogs during one suite run (researcher-reported, 2026-09-04) —
  the reads being the visible half, while a child reaching
  `_fnDeleteKeyringEntry` would have destroyed a working credential
  silently. When an isolation fixture protects host state, ask what a
  CHILD process sees, and enforce it through the environment children
  inherit. Guarded by
  `tests/testKeychainIsolationCrossesProcesses.py`, which asserts the
  backend the child itself reports rather than the variable the
  parent exported.
