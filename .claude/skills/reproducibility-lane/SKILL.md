---
name: reproducibility-lane
description: Contracts for vaibify's reproducibility machinery: PROOF levels and their gates, the L3 shadow rerun, attestations, determinism declarations, reproduce.sh, the environment archive, Zenodo deposits, and reproducing a published project. Use when touching anything under vaibify/reproducibility/, the level gates, the PROOF tab, or an attestation lane.
---

# Working on the reproducibility lanes

These are the contracts the attestation machinery rests on. Every one
of them was established by a defect that reached a researcher, and
most name the test that guards them. A test is kill-confirmed only
when `tests/falsificationRegistry.py` carries its node id (grep it
there before calling it so), and several named here have no entry:
the live legs `tests/testRerunVerifiesWhatItRan.py`,
`tests/testDisposableContainerLive.py` and
`tests/testReproductionLive.py`; `testBothLanesGradeOneTreeAlike`,
`testReproductionSourceNamesNoForgeHostname`,
`test_no_reader_of_the_record_fetches_a_url_on_trust` and
`testFnWriteFileDefaultsToContainerUserOwnership`; and the files
`tests/testRegistryIsNotAnL3Requirement.py`,
`tests/testDaemonCapacity.py` and
`tests/browser/testVerifyWarnsBeforeCopyingTheProject.py`. Read the
sections that cover the lane you are touching before you edit it.

The repository-wide rules in `AGENTS.md` still apply; this file is
the detail for this subsystem.

**Determinism is ONE question, and answering is the criterion
(2026-10-17, superseding the three-question ruling of 2026-08-30).**
The block asks how much speed vaibify should trade for repeatable
files. `sRepeatabilityAnswer` is `speed` or `across`; either passes and
only silence fails, exactly as for Personal AI Configuration. The
answer changes how steps run, never what Level 3 requires. Four things
not to undo:

- **The researcher never names a profile.** The SERVER writes
  `sExecutionProfile` as the newest table version for the answer
  (`executionProfiles.fsNewestProfileForAnswer`), and a request naming a
  profile or a legacy answer is refused
  (`testACallerCannotNameAProfileOrALegacyAnswer`). An answer without
  its profile is half an answer.
- **The answer lives in its own key**, because a VALUE cannot express
  consideration — the lesson of the old `bAcceptBlasVariance: false`,
  which meant "unanswered" and "declined" at once. The three legacy
  answers are kept on disk and answer nothing
  (`testTheLegacyAnswersDoNotAnswerTheQuestion`).
- **The schema-v14 migration is a no-op**: three old answers imply
  neither new one, so the question opens again. It spells no key the
  gate reads (`testTheV14MigratorSpellsNoKeyTheGateReads`); the v13
  migration and its literal-key pin stand as they were.
- **Researcher-facing wording** (`listChoices`, `sWhy`, `sConsequence`)
  lives in `LIST_DETERMINISM_QUESTIONS` beside the gate, travels on the
  poll, and never carries a schema key. Changing the answer retires
  the current attestation, which stays on file as history
  (`testChangingTheAnswerRetiresTheAttestationAndKeepsIt`).

**The answer is ordered (2026-10-27).** `levelOrdering` has edges from
the Project block's `determinism-repeatability` row to the manifest,
the rebuild attestation and both published envelopes, judged by the
gate's own unanswered list, as the poll's row is. Each reason is
CONDITIONAL, because an unanswered project already runs, records and
verifies under the speed settings: answering "Prioritize speed"
afterwards undoes nothing, so no reason may claim an undo for it
(`test_an_unanswered_determinism_question_is_named_before_the_envelope`).
The arrow is an endgame device, so the costliest order of all --
answering before the run that makes the outputs -- is said by the
in-container proof-ladder skill, not by the arrow.

**Only the GitHub sync push records where a push landed (2026-10-27).**
The GitHub verify is bound to that host-side record, and the sync push
writes it only after checking that the researcher's token owns the
remote. The Repos panel runs no such check, and a push's real
destination can differ from the `origin` url the container wrote, so
it deliberately records nothing: a binding recorded there would let a
container agent steer the agent-safe verify, and the researcher's
token, at a repository of its choosing
(`testARepoPanelPushOfTheProjectRepoRecordsNoGithubBinding`). The
verify's binding check is asserted at the shared call site,
`fdictVerifyRemoteService`, with the fetch rigged to fail if reached
(`testAnUnboundRemoteIsRefusedBeforeAnyFetchAndTheRefusalIsRelayed`).

**Two level readings that must not be widened back (2026-10-27).**
The step save and the test-execution routes detect a crossing of
Level 1, so they read `fiComputeProofLevelUpToOne` (the marker rule
plus the Level 1 gate), never `fiProofLevel`: the full ladder hashed
every published file through the container on each click of a
researcher's badge (`testASignOffNeverEvaluatesTheLevelTwoOrThreeGate`,
and `testTheLevelOneReadingEqualsTheLadderCappedAtOne` pins the two
equal). And `fdictLevel2Gaps["bAtLeastLevel2"]` is the gate's own
verdict, with keys for the basename-collision and Overleaf conjuncts,
so every false verdict has a false key beside it
(`testABasenameCollisionReadsBelowLevelTwoInTheGapsDict`).

**`reproduce.sh` carries the SAME determinism guarantees the runner
does, and it reads the epoch from the ENVELOPE.** The runner prefixes
every step with `SOURCE_DATE_EPOCH` and a matplotlib `svg.hashsalt`
derived from it; the generated script exported neither until
2026-09-05, so the artifact vaibify hands the world rendered every PDF,
EPS, PS and SVG with the reproducer's wall clock and a fresh per-process
salt, and its own closing `sha256sum -c MANIFEST.sha256` could not pass
for any workflow with a vector figure. The shadow rerun was
determinism-correct throughout, which is why nothing internal noticed:
the lane vaibify runs for itself passed and the one a stranger runs
could not. Four things not to undo:

- **The epoch is the RECORDED one** — the envelope's
  `iSourceDateEpoch` — never re-derived from HEAD. The commit that
  publishes the manifest moves HEAD, so a re-derivation is
  guaranteed to salt the figures differently from the pinned ones. It is
  read on the reproducing host and passed as a `docker run -e`, because
  a *quoted* heredoc — the thing that keeps workflow-controlled step
  text off the host shell — cannot carry a host value into its body.
- **The salt comes from the runner's own builder**
  (`determinismEnvironment.fsBuildMatplotlibSaltShell`), which takes two
  shell WORDS, the epoch and the directory, so both lanes can call it.
  A private copy in the generator would drift, and the two lanes
  disagreeing is the defect being fixed. The directory is private to a
  run (the runner mints one per run, `reproduce.sh` uses its own
  temporary one and removes it on exit, naming it literally in the trap
  because a step may export its own `MPLCONFIGDIR`): with one shared
  path, run A wrote salt 111, run B wrote 222 and A then read 222,
  which grades a reproducible SVG as not reproducible. Only the salt
  reaches the figure's bytes, never the path
  (`tests/testMatplotlibSaltIsolationLive.py` measures it).
- **`--entrypoint bash` is load-bearing.** A vaibify image declares
  `USER researcher`, and its entrypoint's first phase needs root:
  measured, `docker run <image> bash -s` exits 255 on
  `git config --system`. The Dockerfile says as much — invocations that
  must run the entrypoint pass `--user 0`. A reproduction wants none of
  that phase anyway; it clones repos and configures credentials.
- **An absent epoch is announced, never quietly degraded**, and `0` reads
  as unrecorded (agreeing with `fiCaptureSourceDateEpoch` and
  `fiRecordedSourceDateEpoch`, the other two readers of that field).

`tests/testReproduceScriptDeterminism.py` drives the host half through
real `bash` with the envelope epoch and the repo HEAD epoch made
DISTINCT, so a re-derivation cannot pass; its `docker_live` leg reads
the guarantees back out of a real container. A string assertion that
the preamble mentions `SOURCE_DATE_EPOCH` is necessary and nowhere near
sufficient — it passes against a script exporting the wrong value.

**Since 2026-10-17 `reproduce.sh` runs the same PROCEDURE the shadow
lane does, and ends in the same assessor.** It no longer closes with
`sha256sum -c`. Inside the container it runs, in this order:
`pip install --require-hashes -r requirements.lock` (a lock that does
not install is "Could not run", exit 4), the assessor's `prepare`
(checks every published input, sets the
published figures aside), `clear` (deletes the declared outputs through
`rerunPreparation`, after listing them and how to restore them, because
it regenerates in the reader's own directory), `capabilities`, then
the case-checked unsets of the floor keys Stage A dropped, `probe`, then
each step as the runner runs it — data, plot, then each test group,
every command under its invocation ID with its JUnit report, and the
next step regardless of a failure in this one — and finally
`assess`, whose exit code becomes the script's once the assessor has
written its report (no report is "Could not run"). The shadow
lane passes `--machine`, which adds the report as one machine-readable
line; the script does not. The lock install is why the shadow, which
stays HERMETIC and installs nothing (the 2026-09-01 ruling), refuses
before any step when the pinned image does not satisfy
`requirements.lock`
(`shadowRerun._fdictRefusalIfImageLacksLockedPackages`,
`S_DIVERGENCE_LOCK_UNSATISFIED`): the two procedures converge only
where that install is a no-op. The diff is
`lockSatisfaction.flistDescribeLockMismatch`, the one derivation the
row and the pre-flight also read. Five things not to undo:

- **The execution profile is REPLAYED from the envelope's
  `dictExecutionProfile`**, never looked up from the answer, and the
  host half validates every key against the allowlist and every value
  against its pattern in bash, BEFORE anything reaches `docker run -e`
  (`testTheScriptRefusesASettingOutsideTheAllowlist` and its two
  siblings; the Python half has its own). A later vaibify must not
  change what a published profile asked for.
- **Capabilities come FIRST, in a process that loads no math
  library** (`env -u <allowlist> python3 -I`), because OpenBLAS reads
  its settings once, at load; then the probe runs under the exact
  prefix the steps get. `testEachStageSeesWhatTheOrderPromises`. The
  probe's matrix product runs in a CHILD process
  (`fdictExerciseMatrixProduct`), because OpenBLAS can segfault under
  a forced kernel (design fact 28) and a crash inside the probe would
  leave no record; a crash or a timeout is announced before the steps,
  an exception only recorded.
  `testAMatrixProductThatCrashesIsRecordedAsACrash`.
- **An empty tests block renders nothing**, never `if … then fi`,
  which bash refuses — found by the live leg, not by a string check.
- **The image no lane can obtain, and a `docker run` that fails
  itself, are "Could not run" (exit 4)**, never a verdict about the
  project (`tests/testReproduceScriptDeterminism.py`).
- **Both lanes give one verdict on one tree.**
  `tests/testReproduceScriptParity.py::testBothLanesGradeOneTreeAlike`
  drives five outcomes through both; the `docker_live` copy runs a real
  shadow and a real `reproduce.sh` against one image.
  `testTheLaneAndTheScriptRunTheSameAssessor` kills a generator that
  writes the assessor in any form other than the shadow lane's own
  render of it.

**A container registry is a convenience, never a PROOF rung (ruled
2026-09-05, superseding that morning's ruling that publishing the image
was an L3 requirement).** Docker Hub and GHCR are commercial services
with no retention policy, no DOI and no succession plan; grant
reviewers have declined to accept even GitHub as a long-term
repository, and a rung resting on one would be a claim vaibify could
not defend. So they are treated like Overleaf and arXiv: vaibify
integrates with them because they are useful, and they gate nothing.
`image-not-published` is emitted by no gate, counted by no tuple and a
conjunct of nothing. The readiness payload still carries
`bImagePublished`, because the PROOF tab shows the registry copy as an
OPTIONAL row (`bOptional: true`, excluded from the met count like the
project context file), and `reproduce.sh` still tries the registry
first because it is the fast path — pull, then the archived copy, then
a copy already on this host, the last with a warning that only the
author can take that path. The image's only rung is the environment
archive (`image-not-archived`, below), because Zenodo is the one of
these with a preservation commitment. The shadow lane still translates
the SDK's bare `404 ... No such image` into a refusal naming which kind
of reference it is and what to do — recognized by the SDK's exception
CLASS, never by matching "404" in a message, because an unreachable
daemon carries 404s from other causes — and the remedy it names for a
vanished local-only image is rebuild-or-load-the-deposit, never
publish. `tests/testRegistryIsNotAnL3Requirement.py`.

**The workflow-scope Level 3 criteria are the keys of
`levelGates._fdictL3WorkflowChecks`; read them there, not from a list
here.** Several ask about PUBLISHED copies rather than local files: the
envelope must match the GitHub mirror (`envelope-not-in-github-mirror`)
AND the Zenodo archive (`envelope-not-in-zenodo-archive`, every
declared record consulted); the archive must carry a rebuild
attestation that truthfully describes the archived manifest
(`attestation-not-in-zenodo-archive`, a semantic check, never byte
equality with the local file); no archive may be KNOWN to be a Zenodo
sandbox deposit (`an-archive-is-a-sandbox-deposit`, where unknown
passes); and `reproduce.sh` with its assessor must be exactly what
vaibify would render now (`reproduce-script-stale`, apart from
`reproduce-script-missing`, which owns absence). Because a deposit or a
regeneration writes files these and the Level 2 criteria compare,
`tests/testAdvancingALevelNeverLowersOne.py` guards that an action
offered to reach one rung never moves the bytes another rung compares.

**"Reproduce a published project" is an input adapter to the shadow
lane, and its honest boundary is the source table.** `vaibify
reproduce --from <source>` stages a git URL or a clean local clone as
an exact snapshot of ONE commit (`reproductionSource`), validates it
STRICTLY as reproduction-ready — six rules, the first failure named
with its file, never advisory the way tiers 1-4 are — and exports it
in the tar shape `fbufferRepackArchiveStamped` accepts, so every later
step consumes the staged archive and never a re-clone. The verdict is
"reproduction-ready", NEVER "Level 3": the six rules are what a rerun
depends on (a loadable selected workflow, a content-pinned image WITH
a recorded architecture, a manifest that parses, matches and covers
the selected workflow), deliberately not `fbL3ReadinessOK` — an
attestation, a mirror or a lock file are the AUTHOR's claims, and the
reproduction is the check. A first review read "complete Level 3
project" in the CLI output, ran the fixture through the real gate, got
`False`, and was right. Six things not to undo. The accepted source shapes are matched by
SHAPE, never by forge hostname (`testReproductionSourceNamesNoForgeHostname`
is the tripwire); a dirty clone is refused, naming the paths, because
a working tree is not a published project. Every `git` it runs carries
BOTH `gitHardening` lists, `GIT_TERMINAL_PROMPT=0` AND ssh in batch
mode (`GIT_TERMINAL_PROMPT` silences git's own prompt only; ssh asks
for a passphrase or a host key through `/dev/tty` on its own); the one
exception is the clone of a LOCAL repository, which is itself the file
transport `protocol.file.allow=never` refuses (measured: a plain path,
a `file://` URL and a bundle are all refused), so that clone alone
appends `protocol.file.allow=always` AFTER the list, only after the
path was admitted under the researcher's home, and asks for no
submodule. `--from` never enters the tier sequence and never runs
tier 2's host `pip install`. A report reads source facts from
`fdictDescribeStagedSource` ONLY — kind, commit, remote with userinfo
stripped, workflow name, never a host path — because a reproduction
report is the reproducer's own artifact, never an attestation, never
written into a repository, and never read by `levelGates`. And the
platform is THREE facts kept apart by name (the envelope's required
platform, the obtained image's platform, the daemon's architecture);
staging records the first from the envelope and announces its absence
rather than defaulting it. `tests/testReproductionSource.py` drives
real `git` and a real loopback HTTP remote, and every rule has a
kill-confirmed entry. The staging sweep skips any directory whose live
lock is held, whatever its age — the `~/.vaibify/tmp` lesson.

**The redaction boundary is wider than the userinfo, and the staged
`.git` is inside it.** Three leaks, all found by review on 2026-09-07,
all in code that read as careful. A credential rides in a URL's QUERY
(`?access_token=`) as readily as in its userinfo, so the classifier
REFUSES those parameters — pinned to `credentialRedactor`'s own tuple,
never a second list — the recorded remote is scrubbed through
`fsRedactUrlCredentials` as a second line, and the refusal does not
echo the URL it is about. `git clone` records the source it was given
in TWO places, `.git/config` and the `clone: from …` reflog line, so
`_fnScrubStagedGitMetadata` rewrites the origin to the redacted remote
(or removes it) and deletes `.git/logs` before anything reads the
snapshot: the staged tree, `.git` included, is copied into a container
built from a stranger's image, and a local clone's source is an
absolute host path. The guard asserts over EVERY archive member, not
over `.git/config`, which is how the reflog half was found at all —
and the URL-clone test carries a USERNAME, because with a bare URL the
recorded remote and the cloned URL are the same string and the
assertion is vacuous (it was, and the mutation survived it). And the
staging size ceiling bounds the DISK: the export is separately bounded
by `daemonCapacity`'s `iArchiveTotalBytes` and spooled to a private
file, because what the hub may hold in memory is a different question
from what a clone may occupy.

**A staged job holds the lock that keeps the sweep off its clone, so
it needs an expiry of its own.** `reproductionProgress` carries TWO
retentions — a settled job holds a report id, a staged one holds a
whole repository — plus a concurrency cap, and dismissing the
confirmation card DELETES the snapshot (`fnDiscardJob`, the
`/discard` route) rather than merely releasing it. Before that, stage
and close left a repository per attempt until the hub restarted. A
RUNNING job is never discarded out from under its shadow. The
routes — stage, run, `/retry` and `/discard` — are catalog entries
(the `reproduction` category of `LIST_AGENT_ACTIONS`) with
`bAgentSafe: False` AND their own agent-lane rejection, because a
host-filesystem capability is not something the catalog can express.
The run is NOT in the durable-task
registry: that registry is keyed on a container with an owner lane
tuple, and a reproduction has neither — its shadow is created inside
the worker and destroyed at the end. What "durable" was wanted FOR is
delivered instead by `fbHubHoldsLiveReproduction`, the idle
watchdog's veto, on the Agent Council's precedent and for the same
reason: a reproduction holds no socket and no owned container, so
without it a closed tab lets the clock go stale and the hub SIGTERMs
itself mid-run. A poll that meets a 404 DISARMS and says the job is
gone; jobs live only as long as their hub.

**A second review of the same code found five more, and their shapes
are the lesson.** A refusal that redacts ONE of its branches redacts
nothing: the query branch was scrubbed and the userinfo branch went on
echoing the password it refused. A credential parameter name is
PERCENT-DECODED before it is compared, because a server decodes it —
`?access%5Ftoken=` and `?access_token=` are the same parameter — and
the shared redactor's URL regex matches ANY scheme, because
`ssh://user:key@host` is the same credential in the same place and the
http-only spelling left every ssh remote unredacted (it also captures
the scheme and writes it back, so redacting does not silently rename
the transport). A cap whose count and insert are two critical sections
is not a cap; twelve registrations released into the gap all pass, and
the guard for it is STRUCTURAL — a racy test serializes often enough to
pass against the bug. `fbClaimJobForRun` moves the job into a live
phase in the SAME acquisition that consumes it, because a job claimed
but still reading `staged` is one a Discard deletes the snapshot out
from under, and the card's own running flag is set only when the Run
request returns. And a status callback handed to a lane that runs in a
worker thread must be SYNCHRONOUS: an `async def` there returned a
coroutine nobody awaited, so every step label and both new phases went
to the floor behind a `RuntimeWarning` — which is also why `comparing`
and `tearing-down` are emitted from INSIDE the lane, at the moment the
thing they name begins, rather than set by the caller after the lane
has returned and the container is already destroyed.

**The image is obtained through the chain `reproduce.sh` runs, and
the two lanes are pinned to agree.** `imageAcquisition` walks registry
pull, then the archived deposit (hash from the ENVELOPE, checked
before any load), then a copy on
this daemon (author-only, and it says so) — the order is a ruling and
is never reordered or extended. The download RESUMES: a verified
tarball is removed once the daemon has loaded it, bytes a failure left
behind are KEPT when another attempt can continue from them (a spent
retry budget, a full disk, a daemon that would not load), and deleted
only when nothing could (the deposit changed, the researcher
canceled); every refusal's `dictFailure` says which. A verified load
is noted under `~/.vaibify/depositLoads`, so a daemon still holding
that image ID is served without a second download. `reproduce.sh`
keeps nothing: it removes its temporary tarball whatever the outcome.
The three links that touch the daemon
are SDK calls in `disposableContainer` (`fdictPullImage`,
`fsLoadImageFromStream`, `fdictInspectImage`,
`fsReadDaemonArchitecture`), the SDK authority for disposable work;
the chain module acquires no subprocess and no client, because a
`docker pull` or `docker load` outside a gateway module is a
mutation-capable row the ratchet refuses. The generated script now
passes `--platform` to `docker pull` AND `docker run`, from the
envelope's architecture normalized in shell, and announces an absent
architecture on stderr rather than defaulting it — the same treatment
as an absent epoch; the option rides in a bash array expanded with the
guarded idiom because an empty array under `set -u` is an error on
bash 3. `tests/testImageAcquisition.py` drives BOTH lanes against one
tarball and one envelope. The three platform facts stay apart:
obtained ≠ required always refuses, required ≠ daemon is emulation
(refused without `--allow-emulation`, recorded with it), and the
daemon's architecture is asked of the daemon — an image reports its
own on any host. `shadowRerun.fdictRerunAndVerifyFromSnapshot` is the
second seed: image and platform from the acquisition's answer (a
deposit-loaded image answers to its ID alone), platform requested on
the create, the loaded-from-archive marker written under the shadow's
own admission before any step. `reproductionReport` is the
reproducer's artifact under `~/.vaibify/reproductions/reports/`, apart
from staging, never read by `levelGates`; its verdicts are
"reproduced", "reproduced under emulation", "diverged", "no verdict",
never "attested". `tests/testReproductionLive.py` drives the whole
lane against a real daemon — registry fails by DNS, the deposit loads,
the shadow runs the loaded ID — and proves the platform reaches the
daemon by the daemon's own refusal of a wrong one. Two facts found
live: a stock base image already owns uid 1000, and the shadow's typed
reads need a `python3` in the image.

**The dashboard's reproduction is a one-shot JOB with no project
container, and its record is its own.** `POST /api/reproductions/stage`
and `POST /api/reproductions/{sJobId}/run` (`reproductionRoutes`),
like `/retry` and `/discard`, are browser-hub control-plane routes,
registered in the agent catalog with `bAgentSafe: False` (none is
excluded) AND rejecting the agent lane by name, declared
`separate-authority` because the only container they touch is the
shadow `shadowRerun`
creates, admits and destroys itself — `tests/testCarrierMigratedRoutes.py`
pins that they reach no project primitive. The job record
(`reproductionProgress`) is keyed by job, NOT by container: it holds
the staged snapshot's live lock for exactly the job's life (taken in
the request that staged it, released from the task that settles it),
is consumed ONCE (`fbClaimJobForRun` checks and marks under one lock;
a second Run is a 409 by name), and its client view carries no staging
token — the token names a directory on this host. Do not key it on
`archiveProgress`, which is the deposit registry keyed by a container
the job does not have; only the deposit row's VISUAL shape is borrowed.
Phases are written from events that happened — the chain's `pulling` /
`downloading` / `loading`, the pipeline's `stepStarted` — and the
comparison plus teardown, which the rerun seam performs with no event,
are reported together as `finishing`, never invented from a timer. The
card polls only while the hub says `bLive` and disarms on settle
(`tests/browser/testReproducePublishedCard.py` counts polls after
settle), and no run request leaves the page before the researcher has
seen the confirmation and clicked Run (the same file asserts the
ORDER, not the wording). Both lanes write the same report through the
same seams, including the shared archive re-check
`reproductionReport.fdictRecheckObtainedImage`, which moved out of the
CLI when the dashboard became its second caller.

**The environment archive is one question with two blocks, and the L3
half must never read the L2 answer.** Level 2 asks whether the
researcher ANSWERED — `archived`, `referenced` and `declined` all pass,
and only silence fails. Level 3 asks whether a matching archive
EXISTS. Keeping them apart is what makes declining a decision rather
than a lock: a decline is an absent archive, so changing the answer and
depositing opens L3 with nothing to undo. The one sound direction is
the other one — `fbImageArchiveQuestionSettled` reads the deposit
record, because having deposited is having decided, and the deposit
finishes in a durable task holding no commit lane to persist an answer
through. It reads the LINEAGE note too (`fdictArchiveLineageOf`,
2026-09-28): a changed image drops the deposit record but keeps a note
of the record it was archived under, and a rebuild does not unanswer a
researcher who deposited; whether the NEW image is archived stays
Level 3's question. Five things not to "simplify", each already pinned by a
kill-confirmed test in `tests/testEnvironmentArchive.py`:

- **The version DOI, never the concept DOI.** Zenodo's concept DOI
  always resolves to the NEWEST version, so recording it repoints every
  earlier paper at whatever image was deposited last — the link
  resolves, nothing errors, and the archived environment is simply not
  the one that produced those numbers.
- **Architecture is recorded, never inferred from the digest.** A
  manifest-list digest spans several platforms and pins none of them,
  so matching digests do not imply a matching build. This is also why
  the envelope's regeneration carries a deposit record forward only
  when BOTH the digest and the architecture still agree.
- **Unchecked is never red.** Red means diverged, a claim about the
  deposit; an envelope one side of which is missing was compared with
  nothing. `flistDescribeArchiveMismatch` RAISES `LookupError` for that
  case rather than returning a reason, so a caller cannot accidentally
  render it as a difference.
- **CLOSED needs positive evidence.** "Declined, and the image is gone,
  so L3 is unreachable for this result" is the strongest statement the
  row makes. The presence probe is three-state and captured at connect
  (the poll makes no daemon call); `None` means nobody looked, and the
  row falls back to the weaker reading.
- **The attestation re-check can be VACUOUS.** An image obtained by
  loading the deposit hashes to the deposit's own bytes every time.
  `reproduce.sh`'s fallback writes
  `.vaibify/image_loaded_from_archive` — a file beside the envelope,
  never a field inside it, because the envelope is pinned in
  `MANIFEST.sha256` and the script's assessor checks every pinned input
  unchanged (until 2026-10-17 the script ended in `sha256sum -c`) — and
  the check reports vacuous rather than passed.

Two hashes ride in the record and neither substitutes for the other:
`sTarballSha256` binds the bytes UPLOADED (what a downloader verifies)
and `sImageStreamSha256` binds `docker save`'s uncompressed output
(what the re-check compares, because zstd and gzip — and two builds of
one codec — give different bytes for one image). The deposit runs
host-side and reads the Zenodo token out of the CONTAINER keyring
through the typed-read seam, because `docker save` can only run on the
host and the token lives nowhere else; it is held in a local for one
upload and written to no file and no log.
`docs/architecture.md` — "The environment archive" — carries the model.

**A deposit is fetched from the Zenodo the record NAMES, and no
redirect is followed before its host is checked (2026-09-11).** The
record carries `sZenodoService`; readers map it through
`zenodoClient`'s table (`fsResolveServiceBaseUrl`) and refuse any other
value, and a record written before the field existed is classified by
DOI prefix (`fsServiceForDoi`: `10.5072/` is the sandbox — the word
"sandbox" is in no sandbox DOI, and matching it sent every sandbox
deposit to production, where the record 404s and the fallback composed
a download URL from a DataCite error page). Every GET the client makes
goes through `fresponseGetWithinAllowlist`, which follows redirects BY
HAND and checks each hop's origin before sending; the shell fallback in
`reproduce.sh` does the same with `curl` and `%{redirect_url}`, never
`-L`, and its host table is RENDERED from the client's. Nothing read
from the envelope (`environment.json`) is ever fetched as a URL: it is
a file in a cloned repository. `test_no_reader_of_the_record_fetches_a_url_on_trust`
pins the single transport call, and the decoy-server tests in
`tests/testImageAcquisition.py` and `tests/testReproduceScriptGenerator.py`
prove a refused host receives no request. The download is BOUNDED by
the envelope's recorded size in both lanes (2026-09-12): the script
passes it to `curl --max-filesize` and measures the bytes that landed
before hashing them, the client refuses to grow past it, and a record
with no size is refused rather than fetched without a ceiling.

**A project containerized from the author's PINNED image is admitted
by its origin record, and the baseline is proven against the image
(2026-09-11).** The Containerize wizard can OBTAIN the image a clone's
envelope pins instead of rebuilding it (`docker/pinnedImageAcquisition`,
the published chain). Six things not to undo: the conversion is a
WHITELIST of runtime fields onto the author's vaibify.yml
(`pinnedEnvironmentConversion`), never a merge, and the agent install
keys are written only after the overlays exist; which overlays the
obtained image holds is PROVEN by its `vaibify-overlays` label (which
must EQUAL the repository's candidate, additions or not) or by
recomputing the recipe fingerprint over the shipped texts — a header
line in a cloned repository is a claim, and unproven never fails open
(no additions: the base runs as-is; additions: refuse before tagging);
overlays are stacked on the obtained image ID with
`vaibify.pinnedBaseImageId`, and a DERIVED running image is reported
as derived, never as the pin; the origin record
(`config/imageOrigins`) is written LAST and the launch guard in
`containerManager.flistBuildRunArgs` refuses a start without a good
one — a STALE record (tag moved, or running image neither the base
nor labeled derived) reads as absent, so a `docker build` outside
vaibify cannot inherit the archive's provenance — and requests the
recorded platform, refusing a switched daemon's silent emulation; the
record ends only on switch-to-building (one locked mutation with the
registry's `dictImageSource`), un-registration or rename, never on
stop, and plain `/build` answers 409 naming the switch; and the shadow
runs the record's BASE id on the obtained platform with the
archive-loaded marker, while `fbEnvironmentWasObtained` is the one
predicate behind the regenerate refusal. Two new routes read the host
filesystem (`pinned-environment`, `acquire-image`) and are
catalog-excluded AND agent-lane rejected. `tests/testPinnedImageAcquisition.py`
is the kill-confirmed guard.

Four more, from a review of that lane (2026-09-12). The overlays
label is a SET rendered in canonical order (`flistCanonicalizeOverlaySet`),
never the build order: a differential stack installs `node` after the
author's `claude`, and a label written in that order is one the
label's own parser refuses, so the derived image could never be
acquired again. An unproven baseline with no additions answers `None`,
not `[]`: an empty PROVEN set would make the differential resolver
re-stack the author's own overlays onto the image that holds them.
The unproven-with-additions refusal NAMES its recovery
(`sAction: reobtain-without-additions`), and `acquire-image` takes
`bWithoutAdditions`, which drops the added agents from the registry
entry before obtaining -- a refusal whose remedy has no lane is a dead
end. And re-obtain and switch-to-building are refused by the server
while the project's container EXISTS, asked of the daemon
(`_fnRefuseWhileTheContainerExists`), because the page's stop can
fail and every transition after a stop reads its answer
(`fnStopContainer` returns it) -- a stop that failed must never let a
retag, a cleared origin record or a build through.

**The envelope pins the agent-free ENVIRONMENT, never the coding
agents (ruled 2026-09-28).** Agents write code and compute nothing, so
the pin -- and with it the shadow rerun, `reproduce.sh`, the deposit
and a reader's acquisition -- is the build stage below the first agent,
and a reader brings their own agents. Five things not to undo, each
kill-confirmed. Every stage's `vaibify-overlays` and recipe labels
describe THAT stage (`test_each_stage_is_labelled_with_what_it_holds`);
stamping the whole chain's list on every stage is how `:base` came to
claim agents it did not hold. Agent and prerequisite stages name the
agent-free stage in `vaibify-environment-image-id` and every other
stage stamps it EMPTY, so no stage inherits a value through `FROM`.
`fsResolveEnvironmentImageId` trusts the label only after the named
image's layers prove a prefix of the running image's, and an
environment it cannot vouch for pins NOTHING with `sUnpinnedReason` --
falling back to the running image would publish the agents while every
record said otherwise. `agentLayerSeparation` reads the agents' layers
out of `docker save`, naming blobs by UNCOMPRESSED sha256 (the
containerd store gzips them and writes the manifest last; tiny empty
layers are real layers -- a size cut-off reported real images as
unreadable) and refuses on five rules before a deposit saves a byte
and before a reader's stacked agents are tagged; an unrun check
refuses too. And the deposit refuses a pin whose own label names
agents. Currency reads a pin on the environment below the live image
as `sRelation: "derived"`, the note obtained images already use.
`tests/testAgentLayerSeparation.py`,
`tests/testEnvironmentSnapshot.py`, `tests/testDockerfileProvenance.py`.

The pin is agent-FREE, not agent-guidance-free, and that is a known,
DEFERRED design question with its own plan. The in-container agent
guidance — the skills, the staged docs, `entrypoint.sh` with the
instructions it generates, and `vaibify-do` — is `COPY`'d by the base
`vaibify/containerImage/Dockerfile`, which `imageBuilder.fnBuildImage`
builds before any overlay, so it lies inside the agent-free stage
`environmentSnapshot.fsResolveEnvironmentImageId` names. A guidance
edit therefore changes the environment image ID the envelope pins once
the image is rebuilt, and the recipe fingerprint, which hashes only the
Dockerfile texts, does not move with it. Do not answer it here by
moving the guidance above the pin: that changes every project's pin
once, which is the deferred plan's decision to make.


## A human step's outputs are GIVEN, not reproduced

**The rerun carries an interactive step's outputs instead of refusing
the workflow (2026-08-31 ruling).** Before this, any interactive step
refused the whole tier-5 rerun. That made Level 3 **unreachable for
every project vaibify builds**: vaibify creates an interactive AI
Declaration step, Level 2 blocks on `missing-ai-declaration-step`, so
reaching L2 the product's own way guaranteed L3 could never pass. The
researcher found it by clicking Verify; no test combined the two,
because the refusal tests used generic interactive steps.

The model is that a human step's outputs are **input** to the rerun,
like the repository's source files — data a person produced, which the
steps below consume. Nothing new carries them: the shadow's coherent
export already copies everything git can enumerate, so they arrive
verbatim and the executable steps run against the bytes the original
run used.

**The carve-out is exclusion, and both directions of it are load-bearing.**
`flistCarriedOutputRepoPaths` drops those paths from the comparison,
`fdictBuildAttestation` records them in `listCarriedPaths`, and both
lanes report them. Three ways to break it, each already pinned by a
kill-confirmed test:

- **An exclusion set that matches nothing.** It must resolve paths
  through the SAME `fdictWorkflowTemplateValues` +
  `flistStepOutputRepoPaths` helpers the manifest writer uses. Resolved
  any other way the paths simply fail to match, nothing is excluded,
  nothing says so, and every given file is graded as reproduced.
- **An exclusion that leaves nothing.** A workflow whose every pinned
  entry is human-made has nothing for a rerun to reproduce, so it fails
  closed on `S_DIVERGENCE_EVERY_ENTRY_GIVEN` rather than passing 0 of 0.
- **A silent count.** `iOutputHashesTotal` now covers only what
  executed. Displaying that ratio without `listCarriedPaths` beside it
  turns a narrow true statement into a broad false one — the same shape
  as the "Outputs match the GitHub mirror" projection bug.

**DISABLED steps still refuse, and the asymmetry is deliberate.** Being
interactive is a declared property of the workflow; being disabled is a
switch. Carrying a disabled step's outputs would let anyone silence a
step and still attest around it. The AI Declaration needs no case of
its own — it is an interactive step, and the general rule covers it.

**The rerun grades only outputs it regenerated (2026-10-01 ruling,
graded by assessment since 2026-10-17).** The shadow rerun runs every
executed step's data commands whatever `bPlotOnly` says, and deletes
every path an executed step produces before running anything, so a file
left over from the original run can never be graded as reproduced. An
output the rerun did not write is FAILED (missing), never byte-identical.
A path that is both carried (human-made) and produced by an executed
step is an ambiguous overlap, and the rerun is refused by name rather
than silently keeping the file. Pinned inputs (`reproduce.sh`,
`Dockerfile`, `requirements.lock`, `environment.json`, scripts, tests,
their standards, the rendered assessor) are INTEGRITY, checked before
and after the run, and never graded as outputs.

**Level 3 is an assessment, and its contracts are these (design D1–D8,
I1–I11).** Integrity is exact; agreement is graded per output as
failed, byte-identical, comparisons passed, or insufficient evidence;
byte identity is reported whenever it holds and is never the
criterion. Each of these was built against a falsification test, and
each is a way to turn a verdict back into a claim nobody earned:

- **Two leaf modules, one rendered file.** `rerunProcedure` and the
  frozen `comparisonPolicyV1` import only the standard library, parse
  as Python 3.8, and share no name, because they are concatenated into
  the project's rendered assessor (`S_RERUN_ASSESSMENT_REPO_PATH`)
  (`tests/testRerunAssessmentStructure.py`). Both reproduction lanes
  run the same bytes for the same policy and workflow.
- **The policy is frozen at its pinned hash.** A fix is a new policy
  version, never an edit
  (`testThePolicyModuleIsFrozenAtItsPinnedBytes`). A reproduction
  grades under the policy the project RECORDED (line 2 of the rendered
  file), using vaibify's own module for it, never the repository's
  copy (`testAProjectGradesUnderTheComparisonPolicyItRecorded`); an ID
  this vaibify lacks is "Could not run", naming the upgrade.
- **Evidence is JUnit only, bound to its step, its test file and its
  command identity**, and every planned command is accounted for: a
  command with no ledger line never ran, a report with no ledger line
  is never read, a skip and a missing report assert nothing, and one
  step's passing report never satisfies another's absent check
  (`tests/testRerunAssessmentEvidence.py`). The author's committed
  passing markers never stand in for tests that did not run.
- **A structural check never establishes agreement.** It can fail an
  output; changed bytes with only structural checks are insufficient
  evidence (`testChangedBytesWithOnlyStructuralChecksIsInsufficient`).
- **The precedence is a table in the policy, worst first**: could not
  run, published inputs changed, not reproduced, insufficient evidence,
  then comparisons passed or byte-identical. A later pass never erases
  an earlier failure (`tests/testRerunAssessmentPrecedence.py`). Every
  verdict ships with its tally, and the dashboard renders the server's
  sentence.
- **The image check is narrow and labeled as vaibify's**: a single
  frame, at most 8 bits per sample, the same format, size and mode,
  and no RGBA channel moving by more than 2 levels. A higher-precision
  image is never reduced to fit, and a vector figure is never assessed
  by it (`tests/testRerunAssessmentImageCheck.py`).
- **The host re-derives the verdict from the raw evidence** and never
  trusts the container's report; a self-consistent forgery of an exit
  status or a hash is caught (`tests/testAssessedRerunLane.py`).
- **A historical attestation stays true and stops counting.** An
  attestation made by the byte-only verifier, or under another policy or
  profile, reads as a previous check and never satisfies the rebuild
  requirement (`testAHistoricalAttestationNeverSatisfiesTheRebuildRequirement`).
- **The execution profile is frozen in the envelope**, its keys on a
  closed allowlist checked in both languages, and a profile this vaibify
  lacks stops the run before any step
  (`testAProfileThisVaibifyLacksStopsTheRunBeforeAnyStep`).
- **Provenance is recorded per SECTION, only after it completed.** A
  tests-only run, a failed section and a skipped one relabel nothing;
  an identical rerun leaves the record byte-identical; no processor
  model or computer name is ever recorded
  (`tests/testOutputProvenance.py`). A provenance difference is
  context, never an explanation of the difference; it is information,
  never a failure, and never red.
- **Ordinary runs are unchanged.** Without `dictRerunEvidence`, no
  command carries an evidence export or an invocation identity
  (`testAnOrdinaryRunCarriesNoEvidenceExportAndNoInvocationIdentity`).

**A verification produces a FILE, and the per-file outcomes are the
authority for it (2026-09-11).** The rerun keeps every observed hash in
`listFileOutcomes` (one `{sPath, sExpected, sObserved, sStatus, sRole}`
per FROZEN manifest entry, in the manifest's order; the assessed rerun
keeps `sStatus` and attaches each entry's graded `sOutcome` and
`sOutcomeReason` from the assessor's report) and renders them into
`REPRODUCED.sha256` at the repository root in `MANIFEST.sha256`'s own
format — ONE header line, same paths, same order, a `# MISSING  <path>`
comment at the same position for a file the shadow did not produce —
so a person compares the two with their own eyes and `diff` works.
Every count, path list and the rendered file derive from that list,
while the pass or fail VERDICT is the assessor's report
(`dictAssessmentReport`, its `iExitCode` and `sVerdict`), never
re-derived from the hash statuses; nothing re-hashes after the shadow
is destroyed or re-reads a manifest the rerun may have mutated. Three
things not to undo: the write ORDER (timestamped copy under
`.vaibify/reproducedManifests/`, then the root file, THEN the record
naming it — a foreign clone's reproduction writes and names the
timestamped copy alone and never the root file, which in that clone is
the author's committed one — shared by both lanes in
`reproductionRecord.flistWriteVerificationOutcome`, so a record never
points at a manifest that was not written); the manifest NEVER pins
`REPRODUCED.sha256`, the history directory or `.vaibify/reproductions/`
(`fbIsReproductionRecordPath`; a reproduced manifest inside the manifest
grades itself); and the viewers' line colors come from the record's
per-file `sOutcome` (its `sStatus` in a record written before the
assessment), never from JavaScript comparing hashes. A clone whose
attestation was last COMMITTED by another identity (tracked at HEAD,
`%ce` differs from the receiving repository's `user.email`; an
unconfigured identity is foreign) records a REPRODUCTION under
`.vaibify/reproductions/` in the report schema and leaves the author's
attestation untouched — `fbRepositoryCarriesForeignAttestation` takes a
git RUNNER because the two lanes ask different repositories (the
container's checkout, the CLI's `--repo` host checkout), and the
records are read on the attestation GET only, never on the poll.
Attestation schema v5 migrates the three new fields to `None`.
`tests/testReproducedManifest.py` is the kill-confirmed guard.
Whose attestation the clone carries is THREE-state (2026-09-12): a
git that cannot answer -- a broken executable, an exec failure, a
tracked file whose committer cannot be read -- raises
`RecordKindUndeterminedError`, never "not foreign", because that
answer is the fail-open that overwrites somebody else's record. Both
lanes settle the question BEFORE the rerun and refuse by name when it
is undetermined; readiness answers `undetermined` so the confirm
dialog says so first. Every step is decided by git's own exit code,
measured: `rev-parse --verify --quiet HEAD` exits 1 with no commits
and 128 outside a repository, `ls-tree` exits 0 with empty output for
an untracked path, `config` exits 1 for an unset key.

**A rerun that reached no verdict is NEVER written as an attestation.**
It used to be: a refusal became `sStatus: "failed"` plus a history
entry, i.e. a scientific claim keyed to a manifest digest saying the
project does not reproduce, on the strength of a precondition the run
could not meet. It also destroyed any earlier passing attestation the
unchanged manifest still entitled the project to. Both lanes now branch
on `bRerunAttempted` — `reproducibilityRoutes._fbRecordOutcome` and
`commandReproduce._fbWriteAttestationFromRun` — and they **must agree,
because they write the same file**. The hub remembers the reason
in-process (`_DICT_LAST_NO_VERDICT`) and the PROOF tab renders it; that
lifetime is the honest one, since nothing was established.

**A verification reports progress, or it reads as a hang.** The tab
polls only while the server says a verification is live, and disarms
when it settles. Until this existed the "started" toast was the last
thing a researcher saw — a 2.5 second refusal and a two-hour rerun were
indistinguishable from the chair. Do not make this a standing cadence,
and do not let the card pulse over a finished run.

**The shadow is destroyed, so the FAILURE RECORD is the only evidence.**
The rerun's status callback used to be `_fnDiscardStatusEvent`: every
step result and every line of output went to the floor, so a failed
rerun produced one sentence — "pipeline rerun exited non-zero" — about
a container that no longer existed. The researcher could not re-run the
shadow, could not read its logs, and could not tell a missing
dependency from a real divergence (researcher-reported, 2026-09-01).
`rerunDiagnostics.ftBuildRerunDiagnosticsCollector` keeps the first
failing step's label, name, exit code and a bounded output tail, and
both lanes persist it as `dictRerunFailure`. Three properties are
load-bearing: the FIRST failure (later steps fail because the first
did), a BOUNDED tail (this record is committed and published to Zenodo
— an unbounded log would put a researcher's whole console into a public
artifact), and unconditional FORWARDING (the CLI prints the same
stream; an observer that swallowed events would break the caller it was
added beside).

**Three states, not a boolean — twice over.** The row said "No current
rebuild attestation. Run this once every other check passes" over a
rerun that HAD run and reported a failing step, because the poll shipped
only `bRebuildAttestationCurrent`. "Never run", "ran and failed" and
"passed but stale" are different things and a researcher acts on them
differently; the poll now ships `dictRebuildAttestation` so the row can
tell them apart. Likewise `null` and `{}` in `dictRerunFailure` are
"this record predates capture" and "no step reported a failure" — the
migrators write `None` for exactly that reason.

**A shared summarizer does not know your new state.**
`fsSummarizeLevelStates` is shared with the Steps banner, so it counted
a `running` row as *nothing assessed* and painted a pulsing `?` over a
rerun plainly under way. `_fdictGroupStateByLevel` maps `running` to
`partial` before summarizing. When adding a level-cell state, check
every aggregation it flows into — the row and its banner are computed
by different code.

## The L3 rerun happens in a shadow container, not the researcher's

**Tier 5 no longer re-runs the workflow in the live project
container.** `shadowRerun.fdictRerunAndVerifyThroughShadow` is the one
entry point both attestation lanes use — the dashboard's
`/level3/verify` route and `vaibify reproduce --rerun`. It creates a
fresh container from the image digest the envelope's environment
snapshot pins,
copies the repository in, drives the shared `rerunVerification`
comparison against **the shadow's** filesystem, and destroys the shadow
with proof.

Two reasons, and the second is the one worth remembering. The rerun
used to overwrite the researcher's real outputs. And it exercised
whatever the project container had *become* — packages from a debugging
session, files from an interactive step — rather than the image
`reproduce.sh` would pull, so it could pass where a stranger's
reproduction would fail. `docs/architecture.md` carries the full model.

**`filesRepoLive` is the source of the image PIN, never the comparison
root.** The parameter is spelled that way on purpose: the older
function beside it takes a `filesRepo` that must be rooted on the
filesystem the rerun writes to, and passing the live adapter into the
comparison is the substitution that makes a verification grade a tree
the rerun never touched — every entry clean, every attestation passing.
`testTheComparisonIsRootedOnTheShadowNeverOnTheLiveRepository` drives
the lane with the two roots made distinct and is kill-confirmed against
exactly that swap.

**The shadow needs its own mutation admission, and forgetting it breaks
every rerun.** The rerun drives the ordinary `DockerConnection`, whose
execs ask the gate about the container id they name. On the dashboard
lane that runs inside a mode-(c) durable carrier opened for the
**project** container, so the shadow's execs are refused —
`MutationNotAdmittedError`, from inside a background task, reported to
the researcher as an unexplained attestation failure. That is not
hypothetical: the lane was written without it.
`commitCarrier.ftOpenDisposableContainerAdmission` is the seam, and it
is narrow in both directions —
`tests/testShadowContainerAdmission.py` asserts that the shadow's
admission reaches nothing but the shadow, and the project's reaches
nothing of the shadow.

**Do not read a green route-test as evidence this lane works.** The
route tests patch the shadow entry point out, precisely so they do not
touch a daemon. What exercises the lane is
`tests/testRerunVerifiesWhatItRan.py`, which builds the host clone, the
live container repo and the shadow copy as three genuinely distinct
directories and runs real commands against them, and
`tests/testDisposableContainerLive.py`, which drives a real daemon.

**The archive's parent directories must be stamped, not left to the
daemon.** A tarball may name `repo/data/file` without naming
`repo/data`, and both `put_archive` and `tar` then create the gap
ROOT-owned — so the container user owns its files and cannot create a
sibling beside them. Verified live: without the synthesized parents the
first write into the copied repository is refused. `disposableSpecification`
emits every parent as its own 1000:1000 member, and
`testFnWriteFileDefaultsToContainerUserOwnership` pins it as the second
tar-building write path.

**The shadow's execs run as the image's declared `USER`.**
`DockerConnection` resolves every exec's user from the image's
`Config.User`, falling back to `researcher`. The create specification
sets the container user numerically (`1000:1000`), which is always
valid, but the per-exec override is by NAME — so a shadow built from an
image that declares no `USER` and has no `researcher` account is refused
by the daemon with "unable to find user", on every step. An L3 envelope
pins the image vaibify built for the project, which carries the
directive, so this is a constraint on what a shadow can be built FROM
rather than a live defect. It was found by pointing the live test at a
stock base image; do not "fix" it by making the exec numeric without
working out what that does to the project-container lane, which relies
on the name.

**`/shadow` is not `/workspace`, and that is deliberate.** The shadow
carries no volumes at all, so borrowing the workspace name would invite
a reader to assume a mount that is not there. Ask
`shadowRerun.ftResolveShadowPaths` rather than composing the path.

**The repository export is coherence-pinned, and BOTH halves of the
check are load-bearing.** `coherentExport.fbaExportRepositoryCoherently`
observes every path git can enumerate immediately before and
immediately after the archive stream, and refuses unless (a) the two
observations are exactly equal AND (b) every archive member matches the
before-observation by an identity recomputed host-side over the
archived bytes. Neither implies the other, which is the thing most
likely to be "simplified" away:

- a file changed after the walk passed it leaves the archive perfectly
  consistent with the before-observation — only (a) sees it;
- a file changed and changed back leaves both observations identical —
  only (b) sees it.

Two registry entries exist precisely to prove that, one per half:
delete either check and exactly one of them survives. Do not collapse
them.

**The exemption is `.git/` only, and its narrowness is a real bug
class.** Git enumerates the working tree, not its own internals, so
`.git/` is exempt from the member check. Writing that test as
`startswith(".git")` instead of `startswith(".git/")` silently exempts
`.gitignore`, `.gitattributes` and `.gitmodules` — real,
manifest-relevant files — and
`testTheGitInternalsExemptionIsNarrow` is the kill-confirmed guard.
Any OTHER unobserved member is refused, naming a checked-out submodule
as the likely cause, because a submodule's files are listed by no
superproject git command.

**A researcher is warned before the copy, and the warning has one
home.** `VaibifyApp.fnConfirmLevel3Verification` is the single opener;
both entry points (the Project block's `verify-l3` action and the PROOF
tab's own Verify button) call it, because a safety warning maintained
in two places is one that drifts. The CLI prints the equivalent notice
rather than prompting — a prompt would break every unattended
`vaibify reproduce --rerun`. Neither is the safety mechanism: the
export refuses a torn copy either way. They exist so a researcher who
meets that refusal was already told what causes it, which is the whole
premise of vaibify — never a baffling debugging session.
`tests/browser/testVerifyWarnsBeforeCopyingTheProject.py` asserts the
ORDER (no POST before the confirm), not the wording.

**Two container facts this lane depends on, both found the hard way.**
`get_archive` cannot read out of a **tmpfs** mount — it answers 404 for
a directory an exec in the same container lists happily. It reads out
of a named **volume** fine, which is what real project repos live in
(verified live). And `DockerConnection` execs `/bin/bash`, so an image
without bash fails with a non-zero exit and empty stderr, which reads
like a broken program rather than a missing shell.

**The disposable lifecycle is SHARED with the Agent Council.**
`vaibify/docker/disposableContainer.py` (the SDK authority),
`disposableSpecification.py` (the pure half) and `daemonCapacity.py`
were extracted from the council's gateway so both lanes have one
container lifecycle. Changes here reach both. The ledger in the gateway
records reservations and outcomes and NOTHING else — admission quotas,
per-provider accounting and the idle-watchdog veto belong to whoever is
spending the resource, and the council wraps this rather than replacing
it. Do not grow those policies into this module.

**Bound a container from the DAEMON's memory, never the host's.** On
Linux they agree and the bug is invisible; on macOS the daemon is a VM
with its own allocation (measured: 16 GB host over an 8.3 GB daemon), so
a container sized from host RAM is over-provisioned and the kill arrives
mid-workflow. `daemonCapacity` keeps the two figures apart by name —
`iHostMemoryBytes` bounds what the hub materializes in its own address
space, `iDaemonMemoryBytes` bounds the container — and
`tests/testDaemonCapacity.py` drives them 16x apart so a test cannot
pass while reading the wrong one.

## Contracts added with the appendix-consistency work

- **Unseeded randomness** is written by the lint into
  `dictStep["dictVerification"]["bUnseededRandomnessWarning"]` and read
  ONLY through `fbStepHasUnseededRandomnessWarning`. Three gates once
  read it from the step's top level, where nothing writes it, and the
  Level 3 check never fired.
- **Scan-found dependencies** (`dependency-unreconciled`, Level 1): a
  scan counts only while everything it examined still holds — every
  script it read (a clean scan included), the scripts the step's
  commands name, and every step's output declarations in step order.
  When any of these has moved, a step reorder included, or the scan
  failed, the step reads "dependency scan not current" — never a pass
  and never a red verdict drawn from old data. A failed scan clears the
  step's findings. Findings and dismissals are keyed by step id.
- **Context is not cause.** A recorded production/rerun context
  difference makes possible causes inspectable; it never explains a
  difference. Messages say "produced under different settings or
  hardware: <both contexts>". Acceptance rests on published
  comparisons only.
- **Byte identity of regenerated outputs is observed, never an
  acceptance rule.** No gate and no test may require a rerun's outputs
  to be byte-identical under either execution profile. Exact integrity
  checks are NOT relaxed: published inputs, scripts, standards, the
  manifest and environment records stay byte-exact, and byte-sensitive
  regression tests remain valid.
- **Policy v1 is frozen.** A changed vector figure cannot pass under v1
  whatever comparisons exist; `S_VECTOR_REMEDY` overstates the options
  and must not be edited in place. Corrections live outside the frozen
  file or in a future policy version.
