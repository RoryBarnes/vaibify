# Testing Model

AI agents can write thousands of lines of working code in minutes, but a
researcher may need days to be convinced that those lines are accurate.
Verification, not code generation, is the bottleneck. Unit tests
help, because they stop a later change from silently breaking a result
that was already checked. But a passing test suite proves only that the
tests and the code **agree**, not that the code is **correct**. When one
agent writes the code, writes the tests and reviews the result, a single
blind spot can author all three.

This page has two halves. The first describes the tests vaibify writes
and runs for **your project**: per-step tests generated from your own
output files, the data formats they can read, and an optional check that
those tests would notice a broken step. The second describes how
**vaibify itself** is tested, as a worked example you can adapt for your
own research code.

## Testing your project

Every step in a project can carry three categories of test. Each lives
in the step's `tests/` directory, with file names suffixed by the step
directory's name (so steps never collide in a flat archive), and each
reads its expectations from a JSON **test standards** file beside it.

| Category | What it checks | Files |
|---|---|---|
| **Integrity** | Each declared output file exists and is not empty. NumPy, CSV, JSON, JSON Lines, HDF5, whitespace-delimited and key-value files are also loaded and checked for the expected shape and, where it held at generation time, for the absence of NaN and infinity. Catches a file an agent wrote in the wrong format or left truncated. | `test_integrity_<step>.py`, `integrity_standards_<step>.json` |
| **Qualitative** | The categorical structure of each output is unchanged: the column names of a table, the top-level keys of a JSON file. | `test_qualitative_<step>.py`, `qualitative_standards_<step>.json` |
| **Quantitative** | Numerical outputs (single values, means, and for stochastic steps, standard deviations and percentiles) match the stored benchmarks within a relative and absolute tolerance. | `test_quantitative_<step>.py`, `quantitative_standards_<step>.json` |

An output in any other format is checked only for being non-empty: a
text file must hold something besides whitespace, and a binary file (an
image, FITS or Parquet file, for example) must hold at least one byte.

The step's **Unit Tests** row in the dashboard expands to show the three
categories, with buttons to generate and run them. In-container agents
reach the same operations through `vaibify-do` (`run-unit-tests` for
all categories, `run-test-category` for one). Test files and standards
are part of the step's Level 1 surface; see
[The PROOF Ladder](proofLadder.md) for how their state feeds the
project's level.

### Test standards

A quantitative standards file holds a default relative tolerance
(`fDefaultRtol`) and a list of entries. Each entry names the value
(`sName`), the file it comes from (`sDataFile`), how to find it in that
file (`sAccessPath`, see [Access path syntax](#access-path-syntax)), the
expected value (`fValue`) and its unit (`sUnit`). Optional fields set a
per-entry relative tolerance (`fRtol`), an absolute tolerance (`fAtol`,
default `1e-8`), an explicit file format (`sFormat`), and a note
(`sNote`). The default relative tolerance comes from the project's
tolerance setting, `1e-6` unless you change it.

When tests are regenerated, the per-entry `fRtol`, `fAtol`, `sNote` and
`sUnit` you edited by hand are kept for every entry whose `sName` still
exists. A test file you have customized is never overwritten without
asking: generation stops and names the edited files, and only an
explicit confirmation replaces them.

### How tests are generated

Test generation is **deterministic**. Unit tests should not be produced
by prompting a model, whose output varies from run to run; they should
be computed from the results. When you press **Generate**, a
self-contained Python introspection script runs inside the container,
reads each of the step's declared output files, and records what it
finds: shape, data type, NaN and infinity counts, column names, JSON
keys, and benchmark values. vaibify then writes the three standards
files and the three test files mechanically from that report. No
language model is involved.

The generator also classifies each step's randomness, because a
tolerance that suits a deterministic calculation is meaningless for a
sampler:

| Classification | When | Quantitative entries and tolerance |
|---|---|---|
| `deterministic` | No random-number framework is used in the step's scripts, or no array is large enough to summarize | Single values and means, at the default tolerance |
| `stochastic` | A random-number framework is used and an output array holds at least 64 samples | Means, standard deviations and percentiles, each with a tolerance derived from its sampling standard error at three sigma (following Oberkampf & Roy 2010 and Vehtari et al. 2021) |
| `stochastic_unseeded` | As above, and the step's randomness is flagged as unseeded | Mean and median only, at a placeholder 10% tolerance with a note asking you to seed the source of randomness |
| `unintrospectable` | Output files exist but yield no numeric benchmark | None; the introspector's errors are recorded instead |

The generator is a foundation, not a complete answer. Generating
meaningful tests for arbitrary data structures is hard, so you, or an
agent you direct, are expected to extend the standards for what matters
in your science. An agent asked for tests is steered to the
deterministic generator (`generate-tests-deterministic`), which pins
what is actually in the data and never overwrites an edited file.

Two other paths exist. A quantitative standards file can be refreshed
from live data outside the dashboard, from the command line; see
[CLI Reference](cli.md). And the generation route can still ask a language model (the
in-container Claude Code, or a provider API key you have stored) to
write tests, but only when a caller explicitly turns the deterministic
mode off; the dashboard's **Generate** button does not. That route is
researcher-only, because model-written test content must be reviewed.

### Checking that your tests have teeth

A test that passes is not evidence that it would fail on broken code.
The expanded quantitative-tests block carries a **Falsification** row
with a **Check test teeth** button. It mutation-tests the step's own
Python code with `cosmic-ray` (deliberately breaking it, for example by
flipping a `<` to a `>`), re-runs the step and its quantitative tests
for each mutant, and records the **kill rate**: the fraction of
mutations the tests noticed.

- The kill rate measures the tests' sensitivity to faults, never the
  accuracy of the result.
- It is **non-gating**: no PROOF level reads it. Some mutations change
  nothing observable (equivalent mutants), so 100% is unreachable in
  general and a hard pass/fail would be dishonest.
- It applies only to a step whose computation is Python source and
  whose quantitative standards are classified `deterministic` with at
  least one benchmark. Any other step reads **not applicable**, never
  green, with the reason named.
- The record is keyed to a digest of the step's scripts and standards,
  so any edit to either invalidates it.
- Runs happen only on demand; the cost is roughly the number of mutants
  times the step's run time, with a per-mutant time limit.

The applicability check and the mutation run look for the unsuffixed
names `tests/quantitative_standards.json` and
`tests/test_quantitative.py`, so a step whose generated tests carry the
step-name suffix reads **not applicable**.

## Supported data formats

The test generator and the quantitative tests read output files through
a common set of loaders. This section is the reference for which files
they can read and how to point a benchmark at a value inside one.

### Format table

| Format | Extensions | Library required | Domain |
|---|---|---|---|
| NumPy array | `.npy` | numpy | General |
| NumPy archive | `.npz` | numpy | General |
| JSON | `.json` | (standard library) | General |
| JSON Lines | `.jsonl`, `.ndjson` | (standard library) | General |
| CSV | `.csv` | (standard library) | General |
| HDF5 | `.h5`, `.hdf5` | h5py | General |
| Whitespace-delimited | `.dat`, `.txt` | (standard library) | General |
| Key-value text | detected in `.dat`/`.txt`, or `sFormat: keyvalue` | (standard library) | General |
| Fixed-width text | `sFormat: fixedwidth` only | (standard library) | General |
| Multi-table text | `sFormat: multitable` only | (standard library) | General |
| Excel | `.xlsx`, `.xls` | openpyxl | General |
| Parquet | `.parquet` | pyarrow | Data science |
| Image | `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif` | Pillow | General |
| FITS | `.fits`, `.fit` | astropy | Astronomy |
| VOTable | `.vot` | astropy | Astronomy |
| IPAC table | `.ipac` | astropy | Astronomy |
| MATLAB | `.mat` | scipy | Engineering |
| Fortran unformatted | `.unf` | scipy | Engineering |
| VTK mesh | `.vtk`, `.vtu` | pyvista | Engineering |
| CGNS | `.cgns` | h5py | Engineering |
| FASTA | `.fasta`, `.fa` | (standard library) | Biology |
| FASTQ | `.fastq`, `.fq` | (standard library) | Biology |
| VCF | `.vcf` | (standard library) | Biology |
| BED | `.bed` | (standard library) | Biology |
| GFF/GTF | `.gff`, `.gtf`, `.gff3` | (standard library) | Biology |
| SAM | `.sam` | (standard library) | Biology |
| BAM | `.bam` | pysam | Biology |
| SPSS | `.sav` | pyreadstat | Social science |
| Stata | `.dta` | pyreadstat | Social science |
| SAS | `.sas7bdat` | pyreadstat | Social science |
| R data | `.rds`, `.rdata`, `.rda` | pyreadr | Social science |
| Safetensors | `.safetensors` | safetensors | AI/ML |
| TFRecord | `.tfrecord` | tfrecord | AI/ML |
| Syslog | `.log` | (standard library) | Security |
| CEF | `.cef` | (standard library) | Security |
| PCAP | `.pcap`, `.pcapng` | scapy | Security |

Extensions are matched without regard to case, so `.RData` and `.FITS`
are recognized.

### How format detection works

1. The file's extension, lower-cased, is looked up in the table above.
2. A `.txt` or `.dat` file is checked for key-value structure. Ignoring
   blank lines, `#` comments and divider lines (a line of one repeated
   character), if more than a third of the lines contain `=`, the file
   is treated as key-value rather than whitespace-delimited. At test
   time, a `.txt` or `.dat` benchmark whose access path uses `key:` is
   also read as key-value.
3. An unknown extension is inspected: if any of its first four bytes
   lies outside the ASCII range, it is reported as an unsupported binary
   format; otherwise it is read as whitespace-delimited text.

### Optional libraries

Formats marked "(standard library)" need no extra packages. Every other
library is imported only when a file of that format is read, so a
missing library never breaks generation: that file is reported with an
error naming the package to install, and the remaining files are
processed normally.

### Overriding format detection

When an extension is ambiguous (for example, a `.txt` file with
fixed-width columns), set `sFormat` on the entry in the quantitative
standards file to override detection:

```json
{
    "sName": "fFinalValue",
    "sDataFile": "results.txt",
    "sAccessPath": "column:value,index:-1",
    "sFormat": "fixedwidth",
    "fValue": 1.25,
    "sUnit": ""
}
```

Valid `sFormat` values are `npy`, `npz`, `json`, `jsonl`, `csv`,
`hdf5`, `whitespace`, `keyvalue`, `fixedwidth`, `multitable`, `excel`,
`parquet`, `image`, `fits`, `votable`, `ipac`, `matlab`, `fortran`,
`vtk`, `cgns`, `fasta`, `fastq`, `vcf`, `bed`, `gff`, `sam`, `bam`,
`spss`, `stata`, `sas`, `rdata`, `safetensors`, `tfrecord`, `syslog`,
`cef` and `pcap`.

### Access path syntax

Each quantitative benchmark carries an access path, a comma-separated
list of `name:value` fields that tells the test where a value lives in
its file. The fields are `key:`, `column:`, `dataset:`, `hdu:`,
`section:` and `index:`.

`index:` takes either an integer position (negative counts from the
end; several comma-separated integers address a multi-dimensional
array) or an aggregate: `mean`, `min`, `max`, `std`, `p5`, `p25`, `p50`,
`p75` or `p95`. Without `index:`, a tabular or array value defaults to
the last element.

| Format family | Example | Meaning |
|---|---|---|
| CSV, whitespace, Excel, SPSS, Stata, SAS, VOTable, IPAC | `column:value,index:-1` | Last row of the `value` column |
| CSV, whitespace | `column:value,index:mean` | Mean of the `value` column |
| NumPy archive, MATLAB, safetensors | `key:arrayName,index:0` | First element of the named array |
| NumPy archive, MATLAB, safetensors | `key:arrayName,index:mean` | Mean of the named array |
| NumPy array (`.npy`) | `index:0` | First element (flattened) |
| JSON | `key:path.to.field` | Nested key traversal; a numeric segment indexes a list |
| JSON | `key:listName,index:0` | First element of a JSON list |
| JSON | `key:listName,index:mean` | Mean of a JSON list |
| HDF5, CGNS | `dataset:/group/name,index:0` | First element of a dataset |
| FITS | `hdu:1,column:flux,index:0` | First row of a column in HDU 1 |
| FITS | `hdu:0,index:mean` | Mean of the image data in HDU 0 |
| FASTA, FASTQ | `index:mean` | Mean sequence length |
| VCF, BED, GFF, SAM | `column:POS,index:0` | First value in a column |
| Key-value | `key:parameterName` | Value associated with the key |
| PCAP | `index:mean` | Mean packet length |
| Syslog, CEF | `index:0` | Line or record count |
| Multi-table | `section:0,column:x,index:0` | First value in column `x` of the first table |

A `key:` value may itself contain commas; it extends until the next
recognized field name.

### Security limits

- NumPy files are always loaded with `allow_pickle=False`, so a
  malicious `.npy` or `.npz` cannot execute code.
- PyTorch checkpoints (`.pt`, `.pth`) are deliberately unsupported,
  because they deserialize with pickle. Use safetensors instead.
- The introspection script refuses any output path that resolves
  outside the step directory.
- Files larger than 500 MB are not introspected.
- JSON traversal during introspection stops at ten levels of nesting.
- Each file contributes at most 250 benchmark entries, so a wide
  dataset cannot explode the test suite.

### Unsupported files

A file that cannot be introspected (an unrecognized binary format, one
over the size limit, or one whose loader fails) is reported as not
loadable with the reason, and no benchmarks are generated from it. If
every output of a step is like this, the quantitative standards are
classified `unintrospectable` and carry the collected errors. A text
file with an unknown extension falls back to whitespace-delimited
parsing.

Adding a new format takes a loader in `vaibify/gui/dataLoaders.py`, a
matching benchmarker in the container-side introspection script, and
integrity and no-NaN support; the two format maps must be kept in step
by hand, because the container script cannot import from the host.
Every new library import must degrade gracefully when the library is
absent.

## How vaibify is tested

vaibify was written entirely by AI agents, so its own test suite
carries the weight a human author's understanding would otherwise
carry. Its tests fall into kinds distinguished not by where they live
(nearly all are `pytest` tests under `tests/`) but by the question each
answers.

| Kind | Question it answers | Where |
|---|---|---|
| **Unit tests** | Does input *X* produce output *Y*? | `tests/` |
| **Architectural invariants** | Is the codebase wired together the way it must be? | `tests/testArchitecturalInvariants.py` |
| **Security invariants** | Do the security boundaries hold? | the suite named in `security.yml` |
| **Style invariants** | Do names and signatures follow the style contract? | `tests/testStyleInvariants.py` |
| **Browser tests** | Does the real dashboard behave in a real browser? | `tests/browser/`, marked `browser` |
| **Falsification tests** | If a safety guard broke, would any test notice? | marked `@pytest.mark.falsification` across `tests/` |

Counts are deliberately not written here, because a hand-typed count
drifts from the code. The live numbers are on the README badges, which
`badges.yml` recomputes after every merge by collecting the suites. To
count them yourself:

```bash
python -m pytest tests/ -m "not docker and not docker_live" --collect-only -q | grep -c "::"
python -m pytest tests/testArchitecturalInvariants.py --collect-only -q | grep -c "::"
python -m pytest -m falsification --collect-only -q | grep -c "::"
python -m pytest tests/browser -m browser --collect-only -q | grep -c "::"
```

**Unit tests** check individual outcomes along a code path, on every
supported operating system and Python version; line coverage is
reported to Codecov (the README badge).

**Invariant tests** enforce design decisions rather than behavior. A new
agent, with a short context window, will not know a decision exists
unless it is written down *and* enforced, so these tests stop an agent
from breaking a design choice even when its output is correct.
Architectural invariants govern how modules may import one another, how
paths are handled, how routes are registered and which routes an agent
may reach, and keep science-specific identifiers out of the source.
Security invariants cover authorization at the browser, agent and
WebSocket boundaries (see [Security Model](security.md)). Style
invariants enforce the naming contract (type-prefixed variable and
function names) against a frozen inventory of grandfathered exceptions
whose budget may only fall. All three live under `tests/`, so they run
in every unit-test cell; each also has its own named lane so that its
result is visible on its own line. A separate `lint` check runs
error-grade static analysis (pyflakes and `pylint --errors-only`)
against a recorded baseline.

**Browser tests** drive the dashboard with Playwright in Chromium,
Firefox and WebKit against a real hub; see
[The three execution lanes](#the-three-execution-lanes).

## Falsification tests

A **falsification test** is the software equivalent of a laboratory
**negative control**. An ordinary test is a positive result: "given good
code, the answer is right." A falsification test also proves the
negative control: "given deliberately **broken** code, the test
**fails**." A test that stays green when its guard is sabotaged is an
assay with no working negative control; it would never catch the real
bug either. This is mutation testing (DeMillo, Lipton & Sayward 1978),
but the breaks are chosen deliberately rather than at random, hence the
name.

Each falsification test is **kill-confirmed**: it has been shown to fail
when one specific mutation is applied to the code it defends, and to
pass again once the mutation is reverted. Four pieces keep that
guarantee re-checkable:

1. **The marker.** Falsification tests carry
   `@pytest.mark.falsification`. Dedicated files mark every test at
   module level; mixed files mark only the falsification tests.
2. **The `Kills:` line.** Every falsification test names, in its
   docstring, the mutation it is proven to catch.
3. **The registry.** `tests/falsificationRegistry.py` records each
   mutation in machine-applicable form:
   `Falsification(nodeid, source, old, new)`, where `old` is the exact
   text to replace and `new` is the break. `old` must occur exactly
   `iExpectedOccurrences` times (default one). A guard checked
   deliberately in more than one place needs every copy mutated, or the
   other copy still refuses and the entry reads as undefended.
4. **The re-kill harness.** `tools/reconfirmFalsification.py` is the
   standing negative control. For each registry entry it requires the
   test to pass on clean code, applies the mutation in a disposable git
   worktree, requires the test then to fail in its call phase, and
   restores the source. A collection error, a fixture error, a mutant
   that does not compile (or, for JavaScript, does not pass
   `node --check`) and a hang are each reported as not a kill. Each
   entry has a wall-clock limit (`--entry-timeout`, 600 seconds by
   default). The harness also reports any marked test with no registry
   entry, and exits nonzero on any gap.

Entries are divided by what they hold. An entry whose test binds a port,
opens a Unix socket, drives the Docker daemon or starts a browser is
`exclusive` and runs alone; every other entry is `shareable` and may run
under parallel workers (`--workers`), each in its own worktree. The
`exclusive` marker is applied per file and enforced: a file that binds a
port without it fails the build. In CI the shareable class is also split
across machines (`--shard I/N`). A single shard cannot speak for the
whole registry and says so; the `falsification:summary` job adds the
shards up, checks registry completeness, requires every declared shard
to have reported, and names any surviving mutation by leg and shard.

An entry whose test drives a real container cannot be judged without
a Docker daemon; the harness reports it by name as NOT EVALUATED rather
than counting a skip as a survivor. The Linux CI legs set
`VAIBIFY_REQUIRE_DOCKER_DAEMON`, so there a missing daemon is a red
lane, not a deferral.

Three architectural invariants keep the class from decaying:
`testFalsificationFilesDeclareMarker`,
`testFalsificationTestsRecordTheKilledMutation` and
`testFalsificationRegistryIsWellFormed`.

### The independent-oracle rule

Kill-confirmation proves a test is **sensitive** to change; it does not
prove that the value it asserts is **correct**. A test written against
buggy code freezes the bug in its oracle, and it will still catch a
deliberate break. A falsification test is trustworthy only when its
expected value comes from somewhere independent of the code (a
specification, an analytic result, a conservation law, a published
benchmark) **and** it is kill-confirmed. Neither alone is enough. The
rule is stated in the registry's docstring; do not weaken it.

## The mutation gate

The mutation gate and the falsification suite both use mutation
testing, but they answer different questions:

| | **Falsification** (`falsification.yml`) | **Mutation gate** (`mutation.yml`, cosmic-ray) |
|---|---|---|
| What it mutates | guards that existing falsification tests already defend | the lines a branch changed against a chosen base |
| What it answers | "do our guard tests still catch their known breaks?" | "did this branch add code that no test defends?" |
| Direction | backward-looking: maintains the committed suite | forward-looking: discovers new gaps |
| When it runs | on every pull request | manually only |
| On failure | fails the job | warns only |

The mutation gate runs only on `workflow_dispatch`, so Python can merge
with no mutation feedback at all; falsification and the invariants are
the per-PR guarantees. Run it from the Actions tab or with
`gh workflow run mutation.yml`, choosing `base_ref` (default `main`) and
`max_mutants` (default 300; `0` means uncapped). Mutants dropped by the
cap are reported, never silently discarded.

It warns rather than fails because mutation testing inevitably produces
equivalent mutants, changes no test could ever detect, and failing on
every survivor would train everyone to ignore it. Survivors appear as
warning annotations on the changed lines and as a job-summary table.

They overlap only where a branch edits an already-guarded line:
falsification stops old guarantees from decaying, and the mutation gate
flags new code that arrived without one.

## The three execution lanes

Most of the suite runs in one process, with no Docker daemon and no
browser. That leaves two real boundaries unexercised, and three lanes
exist to cover them.

**The browser lanes** (`browser-chromium.yml`, `browser-firefox.yml`,
`browser-webkit.yml`) load the real dashboard in a real browser against
a real uvicorn hub, recording every console error and page error. Each
runs on one Linux and Python cell: a browser journey does not become
more trustworthy by running across the whole operating-system and
Python matrix. Their Docker adapter is a **fail-closed fake**: every
command it answers is declared in its contract, and anything else
raises rather than returning a default. Firefox and WebKit deselect the
tests marked `clipboardPermissions`, which need permissions only
Chromium grants; those tests still run on Chromium for every pull
request.

**The container-acceptance lane** (`containerAcceptance.yml`) puts each
command the fake models to a real container, so a fake that drifts from
the daemon is caught rather than believed. Every entry in the fake's
contract names an assertion in `tests/testContainerAcceptance.py`, and
`testEveryNamedLaneTwoAssertionExists` fails if one of those names is
fiction. It also checks repository and workflow discovery, missing-file
behavior, atomic copy and rename, the unprivileged container-user
declaration, file modification times and SHA-256 reads, Python
execution, and rerun-manifest verification. It runs nightly, so drift
is caught up to a day late.

**The fresh-image lane** (`freshImageBuild.yml`) builds the image from
scratch, with no cache, on an amd64 and an arm64 runner, then runs the
acceptance assertions against it and confirms that the image's default
user is not root. The nightly acceptance lane reuses a cached image
keyed by a hash of every build input (`tools/computeBuildInputHash.py`),
so on its own it says nothing about whether the image still builds.

No lane may skip itself green. `VAIBIFY_REQUIRE_DOCKER_DAEMON` and
`VAIBIFY_REQUIRE_BROWSER` turn each lane's convenience skip into a
failure in CI, because a skipped lane that reports success has run
nothing. The shell-completion tests follow the same rule with
`VAIBIFY_REQUIRE_SHELLS`: they drive the shipped scripts in real bash,
zsh and fish, skip a missing shell on a developer's machine, and fail
on the unit workflows, which install the shells.

A green browser lane does **not** mean the frontend is verified. Its
fake says nothing about container launch, file ownership on write, the
real transport, terminal content or figure rendering.

## Continuous integration

Every merge into `main` goes through GitHub Actions, which starts each
run on a fresh machine and blocks the merge if any required check
fails. Each workflow runs **either** before a merge or after it, never
both: the test suites gate the merge, and documentation, badges and
distributions are built from `main` afterwards.
`tests/testWorkflowMergeGateSplit.py` fails if a workflow drifts onto
both sides. Running the gates only before the merge is safe only while
the branch ruleset requires branches to be up to date with `main`
before merging, so two individually green pull requests cannot merge
onto a combination that never ran.

**Before a merge.** These decide whether a change may land. Each runs on
every pull request (and can be started by hand):

| Workflow | Runs | Matrix |
|---|---|---|
| `tests-linux.yml` | the unit suite (including the invariants and falsification tests); a named `invariants` check; a `lint` check; and `docker-smoke`, the tests that need a live Docker daemon | Ubuntu 22.04 and 24.04 × Python 3.9–3.14 for the unit suite; one cell for each named check |
| `tests-macos.yml` | the same unit suite | macOS 26 × Python 3.9–3.14, and macOS 15 × Python 3.9 and 3.14 |
| `falsification.yml` | the architectural invariants, the falsification tests, and the re-kill harness, with a summary job over the union | Ubuntu 24.04 and macOS 26 × Python 3.9 and 3.14; the Linux shareable class split four ways, the exclusive class in its own Linux job |
| `security.yml` | the security-boundary suite, in its own named lane so a green architectural badge can never hide a red security one | Ubuntu 24.04 and macOS 26 × Python 3.9 and 3.14 |
| `styleContract.yml` | `tests/testStyleInvariants.py`, then `tools/generateStyleInventory.py --check` for inventory drift | one Linux cell |
| `browser-chromium.yml` | the dashboard in real Chromium against a real hub | one Linux cell |
| `browser-firefox.yml` | the same suite in Firefox, minus the `clipboardPermissions` tests | one Linux cell |
| `browser-webkit.yml` | the same suite in WebKit (Safari's engine), same deselection | one Linux cell |
| `agentDocsPathCheck.yml` | that every path referenced in an `AGENTS.md` or skill file resolves | one Linux cell |
| `remoteSsh.yml` | the remote transport against a real `sshd`, with `VAIBIFY_REQUIRE_REMOTE_SSH` turning the no-daemon skip into a failure | one Linux cell |

**After a merge.** These publish the state of `main`:

| Workflow | Runs | Matrix |
|---|---|---|
| `docs.yml` | the Sphinx build (warnings are errors), published to `gh-pages` | one Linux cell, on push to `main` |
| `badges.yml` | recomputes the count badges and resolves the status badges | one Linux cell, on push to `main` (and manual) |

**When a version is cut:**

| Workflow | Runs | Matrix |
|---|---|---|
| `pip-install.yml` | builds the source and wheel distributions, runs `tools/checkInstalledDistribution.py` against each, then uploads to PyPI | the full Ubuntu and macOS × Python 3.9–3.14 matrix on a published release; Ubuntu 24.04 and macOS 26 × Python 3.9 and 3.14 on a manual run |

The upload needs both the build and the install test, so a packaging
break blocks the release rather than being published. Run it by hand
after touching packaging, `vaibify/resources.py`, the template tree or
the image's file set, and never make it a required check: it cannot
report on a pull request.

**On their own schedule.** Neither gate nor publisher:

| Workflow | Runs | Matrix |
|---|---|---|
| `mutation.yml` | the cosmic-ray gate on a branch's changed lines (warn-only) | manual (`workflow_dispatch`) |
| `tests-macos-nightly.yml` | the full macOS unit matrix, so the cells the pull-request lane leaves out (macOS 15 on Python 3.10–3.13) still run within a day | macOS 15 and 26 × Python 3.9–3.14; nightly and manual |
| `containerAcceptance.yml` | the modeled container commands and core container behavior, against a real container | one Linux cell; nightly and manual |
| `freshImageBuild.yml` | a full image build from scratch, then acceptance | amd64 and arm64; weekly, manual, and on pull requests that touch the image, its build code, or the documents staged into it |
| `publishedReproduction.yml` | reproduces a published project in its author's environment and fails unless the report's verdict is `reproduced` | one Linux cell; weekly and manual |
| `toolchainEpoch.yml` | asks whether Ubuntu has moved past the pinned toolchain snapshot and opens or updates a standing issue describing the change | one Linux cell; monthly and manual |

### Check names and required checks

A branch ruleset matches checks by **job name**, so a check nobody can
find in the required-checks picker is an unprotected lane. Names that
vary across a matrix follow `<kind>:<os>:python-<version>` (for example
`unit:ubuntu-24.04:python-3.14`); single checks use a bare noun
(`invariants`, `lint`, `docker-smoke`, `style`, `ssh`, `agent-docs`).
`testNoTwoMergeGateLanesProduceTheSameCheckName` fails if two workflows
emit the same name, because a requirement satisfied by whichever
reports first gates neither.

Requiring only some checks lets a pull request merge while the rest are
still running, so the required set is derived from the workflows:

```bash
python tools/syncRequiredChecks.py           # print, change nothing
python tools/syncRequiredChecks.py --apply   # write the ruleset
```

`--apply` also re-asserts the up-to-date requirement. Renaming a job or
dropping a matrix cell orphans the ruleset entry that named it, so run
`--apply` **before** merging such a change. The `results:*` checks from
the test-results action are deliberately not required: they report the
same run the matching `unit:` check already gates.

### What the README badges mean

**Count badges** are computed by `badges.yml` after every merge by
*collecting* each suite, not running it. They say how much test there
is, never whether it passed. A count of zero fails the workflow instead
of publishing, because zero means a marker was renamed or a directory
moved.

**Status badges** for the merge gates report the checks that gated
**the last merge into `main`**, read from the runs on the merged pull
request's head commit; nothing is re-run. A lane with no run against
that pull request renders **did not run** in gray, never green. GitHub's
own workflow badges are not used for these lanes, because they show the
newest run on any branch, so one contributor's failing pull request
would redden the README while `main` is healthy. The scheduled
container-acceptance and fresh-image lanes do keep GitHub's badges,
because they run on `main`, and for a nightly or weekly run the badge is
often the only place a failure becomes visible.

## Running the suites locally

```bash
pip install -e ".[dev]"

# the unit suite, with the invariants and falsification tests:
python -m pytest tests/ -m "not docker and not docker_live and not browser"

# just the falsification tests, or just the invariants:
python -m pytest -m falsification
python -m pytest tests/testArchitecturalInvariants.py
python -m pytest tests/testStyleInvariants.py

# the browser lane:
pip install -e ".[browser]" && python -m playwright install chromium
python -m pytest tests/browser -m browser

# the standing negative control (re-break each guard, confirm it is caught):
python tools/reconfirmFalsification.py

# re-confirm only the entries you just wrote, then check completeness:
python tools/reconfirmFalsification.py --only <substring>
python tools/reconfirmFalsification.py --completeness-only

# the mutation gate (a separate extra):
pip install -e ".[mutation]"
cosmic-ray init cosmic-ray.toml session.sqlite && cosmic-ray exec cosmic-ray.toml session.sqlite && cr-rate session.sqlite
```

The re-kill harness mutates source in disposable worktrees, so it is not
collected by `pytest tests/`. It refuses a checkout with uncommitted
changes, because it would otherwise verify code you do not have; pass
`--include-local-diff` to replay your edits into the worktree. `--only`
re-confirms a subset and says so: its result is not the standing
negative control, and it does not check registry completeness.

Running everything on one machine is slow. That cost buys two things:
an agent cannot silently break behavior outside the context of its
prompt, and because the checks are deterministic, a failure points
straight at the problem without spending model tokens to find it.
