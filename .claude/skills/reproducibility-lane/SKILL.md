---
name: reproducibility-lane
description: Contracts for vaibify's reproducibility machinery: PROOF levels and their gates, the L3 shadow rerun, attestations, determinism declarations, reproduce.sh, the environment archive, Zenodo deposits, and reproducing a published project. Use when touching anything under vaibify/reproducibility/, the level gates, the PROOF tab, or an attestation lane.
---

# Working on the reproducibility lanes

These are the contracts the attestation machinery rests on. Every one
of them was established by a defect that reached a researcher, and
every one has a kill-confirmed test named beside it. Read the sections
that cover the lane you are touching before you edit it.

The repository-wide rules in `AGENTS.md` still apply; this file is
the detail for this subsystem.

**Determinism is THREE questions, and answering is the criterion.**
The L3 determinism gate was an OR until 2026-08-30 — any one of a BLAS
waiver, a pinned thread count or an MKL mode satisfied it — so a
project could attest at Level 3 having answered a third of the
question. The researcher's ruling made them three requirements with
three markers. A DECLINING answer passes ("I do not accept last-digit
differences", "the thread count is not fixed", "this project does not
use Intel MKL"); only silence fails, exactly as for Personal AI
Configuration. Two things not to undo: answers live in their own keys
because a VALUE cannot express consideration (`bAcceptBlasVariance:
false` is what the old form wrote when nothing was ticked, so it means
"unanswered" and "declined" at once), and an answer naming a pinned
value must carry it. The schema-v13 migration promotes only
unambiguous legacy values — never the `false` waiver — and spells the
key names as literals because this module may import only leaf
modules, so `testTheMigrationSpellsTheSameKeysTheGateReads` pins them
to the gate's constants. Researcher-facing wording lives in
`LIST_DETERMINISM_QUESTIONS` beside the gate and must never carry a
schema key.

**`reproduce.sh` carries the SAME determinism guarantees the runner
does, and it reads the epoch from the ENVELOPE.** The runner prefixes
every step with `SOURCE_DATE_EPOCH` and a matplotlib `svg.hashsalt`
derived from it; the generated script exported neither until
2026-09-05, so the artefact vaibify hands the world rendered every PDF,
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
  (`determinismEnvironment.fsBuildMatplotlibSaltShell`), which takes a
  shell WORD so both lanes can call it. A private copy in the generator
  would drift, and the two lanes disagreeing is the defect being fixed.
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
of reference it is and what to do — recognised by the SDK's exception
CLASS, never by matching "404" in a message, because an unreachable
daemon carries 404s from other causes — and the remedy it names for a
vanished local-only image is rebuild-or-load-the-deposit, never
publish. `tests/testRegistryIsNotAnL3Requirement.py`.

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
report is the reproducer's own artefact, never an attestation, never
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
RUNNING job is never discarded out from under its shadow. The three
routes are catalog entries with `bAgentSafe: False` AND their own
agent-lane rejection, because a host-filesystem capability is not
something the catalog can express. The run is NOT in the durable-task
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
the guard for it is STRUCTURAL — a racy test serialises often enough to
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
before any load; tarball removed on every exit path), then a copy on
this daemon (author-only, and it says so) — the order is a ruling and
is never reordered or extended. The three links that touch the daemon
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
reproducer's artefact under `~/.vaibify/reproductions/reports/`, apart
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
and `POST /api/reproductions/{sJobId}/run` (`reproductionRoutes`) are
browser-hub control-plane routes, excluded from the agent catalog AND
rejecting the agent lane by name, declared `separate-authority`
because the only container they touch is the shadow `shadowRerun`
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
through. Five things not to "simplify", each already pinned by a
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
  `MANIFEST.sha256` and that script ends by verifying it — and the
  check reports vacuous rather than passed.

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
nor labelled derived) reads as absent, so a `docker build` outside
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

**A verification produces a FILE, and the per-file outcomes are the
authority (2026-09-11).** The rerun keeps every observed hash in
`listFileOutcomes` (one `{sPath, sExpected, sObserved, sStatus}` per
FROZEN manifest entry, in the manifest's order) and renders them into
`REPRODUCED.sha256` at the repository root in `MANIFEST.sha256`'s own
format — ONE header line, same paths, same order, a `# MISSING  <path>`
comment at the same position for a file the shadow did not produce —
so a person compares the two with their own eyes and `diff` works.
Every count, path list and the rendered file derive from that list;
nothing re-hashes after the shadow is destroyed or re-reads a manifest
the rerun may have mutated. Three things not to undo: the write ORDER
(timestamped copy under `.vaibify/reproducedManifests/`, then the root
file, THEN the record naming it, shared by both lanes in
`reproductionRecord.flistWriteVerificationOutcome`, so a record never
points at a manifest that was not written); the manifest NEVER pins
`REPRODUCED.sha256`, the history directory or `.vaibify/reproductions/`
(`fbIsReproductionRecordPath`; a reproduced manifest inside the manifest
grades itself); and the viewers' line colours come from the record's
verdict, never from JavaScript comparing hashes. A clone whose
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
on `bRerunAttempted` — `reproducibilityRoutes._fnRecordOutcome` and
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
artefact), and unconditional FORWARDING (the CLI prints the same
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
`iHostMemoryBytes` bounds what the hub materialises in its own address
space, `iDaemonMemoryBytes` bounds the container — and
`tests/testDaemonCapacity.py` drives them 16x apart so a test cannot
pass while reading the wrong one.
