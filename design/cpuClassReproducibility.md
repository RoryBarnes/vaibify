# Level 3 as an assessed rerun: exact integrity, honest agreement, one effort question

**Audience: an agent picking this up fresh, and the researcher who
will test it.** This file is the design record. Build from
[cpuClassReproducibilityImplementation.md](cpuClassReproducibilityImplementation.md),
the sequenced guide with code entry points. The design is approved to
build. Every decision below
is **provisional**: the researcher asked for best-judgment choices and
will test them once built. Each decision states what would reverse it.
Every stage touches the reproducibility pipeline, which `CLAUDE.md`
lists under "Ask first". The approval covers building this plan, not
departures from it; anything outside it goes back to the researcher.

Read "The claim", "Rulings and adopted framework", "Facts already
verified", "Decisions taken" and "Precedence" before anything else.
Re-deriving them costs a round trip to the researcher.

Load the skills `CLAUDE.md` routes you to before editing:
`reproducibility-lane` (gates, `reproduce.sh`, shadow rerun,
attestations), `dashboard-state-honesty` (every row, verdict and poll
payload this plan changes), and `container-mutations` for any new exec.

**History:**
- 2026-10-16: drafted after a researcher's QuickStart walkthrough on a
  second machine failed the manifest check inside the author's pinned
  image.
- 2026-10-16: rewritten around an independent review. The first draft
  let the chosen setting decide the acceptance standard, and let
  CPU-class equality switch between byte and test grading.
- 2026-10-16: revised with the researcher's instruction to decide
  D1–D8, using thread-count measurements (facts 16–18).
- 2026-10-16: revised after a second review:
  - the figure rule no longer has a route to a false pass;
  - the execution profile and comparison policy are frozen and
    replayed;
  - failure precedence is specified;
  - historical attestations are not upgraded;
  - claims are trimmed to the evidence.
- 2026-10-17: revised after a review of the implementation guide
  (facts 22–24):
  - `reproduce.sh` clears what it regenerates, as the shadow does, and
    every lane runs commands in the runner's order with its failure
    handling;
  - structural checks (integrity, qualitative) can no longer stand in
    for agreement comparisons;
  - evidence is bound to the step and the command that produced it,
    and every command's exit status is accounted for;
  - the recorded comparison policy is selected from vaibify's own
    frozen code;
  - provenance records only the outputs a run's executed sections
    wrote;
  - the image check states which encodings it supports;
  - processor capabilities are read before any library is loaded under
    the profile.
- 2026-10-17: Phase 0 spikes recorded as facts 25 and 26. Fact 26
  carries one surprise (automatic contour levels), which fails closed
  and changes no decision.

---

## What you are building, in one paragraph

A pinned container image pins software, not the processor. NumPy, the
OpenBLAS bundled in its wheel, and glibc's math library each choose
their code paths when a program starts, according to the CPU's
instructions. OpenBLAS's results also depend on how many threads it
uses, which by default is the number of cores. So the same image can
produce different bytes on different computers, and Level 3's promise
that "every byte of their recreation matches" fails for ordinary
readers.

This work makes Level 3 claim only what vaibify can verify: a
preserved environment and procedure, plus a recorded clean rerun
assessed against published criteria. Byte identity is reported as
additional evidence. Every verification is split into two questions:
- **Integrity**: whether the published inputs are unchanged. This is
  checked exactly, always.
- **Agreement**: whether the regenerated outputs match the published
  ones, byte for byte or within the declared comparisons. This is
  graded under a fixed precedence.

The three determinism questions become one two-choice question about
trading speed for repeatability. The answer selects an execution
profile, which is recorded concretely and replayed. It never changes
what Level 3 requires. Hardware context is recorded when outputs are
produced, so it can explain a difference. It never decides whether a
difference is acceptable.

---

## The claim (D1, decided)

> **Level 3, Reproducible: a preserved execution environment and
> procedure, with a recorded clean rerun assessed against published
> criteria.**

`docs/proofLadder.md`'s summary table becomes:
- **Adds:** "A preserved environment and procedure, and a clean rerun
  assessed against published criteria, attested by the authors."
- **Does not establish:** "Byte-identical results on other computers;
  that inputs trace to raw data, or a public record of all AI
  assistance."

Byte identity is valuable additional evidence, reported whenever it
holds. It is never the Level 3 criterion, and Level 3 never promises
it on other computers. A passing verification establishes what
happened in the environment that was tested. It does not predict what
will happen on every future computer.

---

## Rulings and adopted framework

### Researcher rulings (2026-10-16)

1. **Level 3 must not promise byte identity across hardware.** Pinning
   the environment and offering stricter execution settings remain
   valuable.
2. **Determinism becomes ONE question about effort.** This supersedes
   the 2026-08-30 ruling that determinism is three separately answered
   questions. "Answering is the criterion" carries over: every answer
   satisfies the declaration requirement. Answering never establishes
   reproducibility.
3. **Working on two computers is a primary workflow, not an edge
   case.** A desktop at the office and a laptop at home, pushing to
   GitHub between them, must produce clear explanations, never a
   baffling failure.
4. **Multithreading is vaibify's job.** The question does not ask about
   threads.
5. **Level 3 must stay worth it** for a scientist who only processes
   data and makes figures. Nothing may require them to write anything
   they do not already write.
6. **Decide D1–D8 by best judgment; the researcher tests once built.**

### Framework adopted from the reviews (2026-10-16)

- **F1. Integrity and agreement are separate questions.** Published
  inputs, scripts, tests, reference standards, tolerances and
  environment records are checked exactly under every profile.
- **F2. The selected effort is not the observed outcome.**
- **F3. Hardware context explains; it never decides.** Matching
  contexts do not guarantee equal bytes, and differing contexts do not
  guarantee different bytes. No acceptance rule reads context
  equality.
- **F4. Every verdict other than "byte-identical" carries an evidence
  contract**, and every expected comparison is identified individually.
- **F5. Parity across lanes.** The dashboard, the CLI and
  `reproduce.sh` run the same comparisons, apply the same precedence,
  and exit with the same codes. No lane ever asks a reader to
  regenerate the manifest they came to verify.
- **F6. Settle the claim before the settings.**
- **F7. Requested, probe-observed and execution-observed settings are
  three different facts.** Each is labeled as what it is, and none is
  presented as another.
- **F8. Changing the applied settings retires the current attestation;
  the history is kept.**
- **F9. The concrete execution profile and the comparison policy are
  frozen in the envelope and replayed.** A later vaibify never
  reinterprets an old answer through a new settings table or a new
  threshold.
- **F10. A failing or missing expected comparison is never overridden
  by byte equality.**
- **F11. An old attestation is historical evidence of the check that
  produced it.** It is never presented as satisfying a newer contract.

---

## Facts already verified

Measured on an x86-64 host with AVX-512 and 8 cores on 2026-10-16,
inside the QuickStart example's pinned image (amd64, NumPy 2.5.3,
matplotlib 3.11.2, glibc 2.39), using throwaway containers with
`--network none`. Each fact describes one experiment and claims no
more than it showed.

**CPU instructions**

1. **Symptom.** Clean Outputs, then Run All Steps, then Check Files
   Against Manifest gave 3 of 27 files different: every regenerated
   data file. Both figures matched. On an Apple Silicon Mac emulating
   amd64, all 27 matched. The differences were in the last 3 to 4
   significant digits of a least-squares fit.
2. **Clean Outputs is not involved.** Deleting the outputs and rerunning
   the data and plot commands reproduced the same 3 failures.
3. **OpenBLAS picks its kernel by CPU.** It selected `SkylakeX` here.
   Forcing `OPENBLAS_CORETYPE` to `Haswell`, `Sandybridge`, `Nehalem` or
   `Prescott` reproduced the manifest's fit exactly. NumPy's bundled
   OpenBLAS is a dynamic-architecture build; the review notes the
   variable is documented only for such builds.
4. **NumPy's own dispatch matters for vectorized math.** With no
   settings, 3 files differed. With the OpenBLAS setting alone, 1
   differed. Adding `NPY_DISABLE_CPU_FEATURES=AVX512_ICL,X86_V4` brought
   it to 0.
5. **glibc `libm` dispatches too.**
   `GLIBC_TUNABLES=glibc.cpu.hwcaps=-AVX512F,-AVX2,-FMA,-FMA4` changed
   between 13 and 281 of 400,000 results of `exp`, `log`, `sin` and
   `pow`, each by exactly 1 ulp. Two unmasked runs were bit-identical.
6. **NumPy's floor is fixed when the wheel is built.** It is `X86_V2`
   (`numpy.show_runtime()`).
7. **Cost of the floor alone** (one thread in both rows, 3000×3000
   matrices, 2×10⁷-element vectors):

   | Floor | `A @ B` | `solve` | `exp`, `log` |
   |---|---|---|---|
   | none (AVX-512) | 0.87 s | 1.51 s | 0.026 s, 0.030 s |
   | `x86-64-v3` | 0.95 s | 1.44 s | 0.082 s, 0.079 s |
   | `x86-64-v2` | 3.28 s | 4.47 s | 0.087 s, 0.081 s |

**Threads** (OpenBLAS kernel fixed; hashes of `matmul`, `solve`,
`lstsq`, `dot`, matrix-vector, `eigh`, `svd` and `numpy.sum` at 1, 2, 4
and 8 threads)

16. **Thread count changed the bytes of almost every linear-algebra
    result tested.** `matmul`, `solve`, `lstsq`, `dot`, matrix-vector
    and `eigh` gave a different hash at each thread count, at both the
    `SkylakeX` and `Haswell` kernels. Only `svd` at 600×600 and
    `numpy.sum` were unaffected.
17. **In this experiment, two runs on the same computer agreed.** Two
    runs at the default thread count gave identical hashes for every
    routine tested. This supports, and does not establish, that
    same-computer reruns of such code repeat.
18. **The thread count followed the cores OpenBLAS could see, not
    vaibify's CPU limit.**
    - With `--cpus 2`, OpenBLAS still used all 8 host cores (and gave
      the 8-thread hashes).
    - A requested `OPENBLAS_NUM_THREADS=4` gave identical hashes with 8
      or 4 visible cores. With 2 visible cores (`--cpuset-cpus 0-1`) it
      was silently reduced to 2, and gave the 2-thread hashes.
19. **Cost of the whole "across computers" profile** (floor `v3`, one
    thread, glibc AVX-512 masked) against full speed (native kernel,
    all 8 threads), on this machine:

    | | `A @ B` | `solve` | `exp` | `log` |
    |---|---|---|---|---|
    | full speed | 0.27 s | 0.52 s | 0.026 s | 0.031 s |
    | across profile | 0.98 s | 1.50 s | 0.085 s | 0.081 s |

    That is about 3–4× slower for dense linear algebra and about 3× for
    vectorized `exp`/`log`. The linear-algebra penalty should grow with
    core count, though that is not measured.

**Figures** (an 800×350 matplotlib PNG with a line, a scatter marker,
filled contours, a contour line and axis labels, redrawn under each
change and compared pixel by pixel in RGBA)

20. **Rounding-level data changes barely touch the pixels.**

    | Change | RMS (0–255) | Largest pixel change | Pixels > 2 levels |
    |---|---|---|---|
    | data ×(1+10⁻¹³) | 0 | 0 | 0 |
    | data ×(1+10⁻⁹) | 0 | 0 | 0 |
    | data ×(1+10⁻⁶) | 0.003 | 1 | 0 |
    | data ×(1+10⁻³) | 13.2 | 255 | 53,065 |
    | marker moved 0.05 data units | 1.04 | 197 | 42 |
    | one axis-label character changed | 1.35 | 252 | 50 |
    | 16 pixels inverted | 1.63 | 255 | 16 |

    The root-mean-square difference is diluted by image size. The
    review computed that 16 pixels changed fully in a 1024×1024 image
    give RMS ≈ 0.996, and here a moved marker and an edited label sat
    barely above 1.0. RMS cannot be the metric. The largest per-pixel
    change separates the cases by two orders of magnitude.

**Today's code**

8. **The three determinism answers change nothing that runs.**
   `S_BLAS_ACCEPTED` has no consumer outside `determinismGate.py` and
   `workflowMigrations.py`. Nothing under `vaibify/` exports a thread
   count or `MKL_CBWR`.
9. **The attestation rerun runs tests but grades only bytes.**
   `S_RUN_MODE_RERUN` runs each step's test commands, but failing tests
   never fail a step, and `rerunVerification.fdictVerifyRerunOutputs`
   grades hashes alone. The lane also keeps none of the tests'
   results: the `testResult` events are dropped by
   `rerunDiagnostics._fnRecordEvent`. An attestation under today's
   verifier can therefore pass with failing tests (F11).
10. **The verifier already separates pinned inputs from regenerated
    outputs** (`iPinnedInputsUnchanged`, `iPinnedInputsTotal`).
11. **`reproduce.sh` runs only data and plot commands.**
    `_flistGatherStepCommands` reads `saDataCommands` and
    `saPlotCommands`, and the script ends with an unconditional
    `sha256sum -c MANIFEST.sha256` (`_S_SCRIPT_EPILOGUE`).
12. **Test markers cannot be the evidence source.**
    - `.vaibify/test_markers/<workflow>/<step>.json` is tracked in git.
      It holds `dictRuns`, whose output and input hashes are recorded
      when tests run, and per-node outcomes whose `sRunId` the plugin
      mints itself per pytest process (it never sees a vaibify run ID).
    - The plugin that writes markers lives in `.vaibify/pytestPlugin/`,
      which ignores itself. So it is absent from a reader's fresh clone,
      where `reproduce.sh` runs.
    - The shadow does receive it, because the export streams the whole
      project directory, ignored files included. The shadow therefore
      starts with the author's historical markers and then appends new
      ones.
    - Pytest's built-in JUnit report, enabled through `PYTEST_ADDOPTS`,
      works in every lane. Verified: with
      `PYTEST_ADDOPTS="--junitxml=<fresh file>"` a declared test
      command ran unchanged, and the report named each standard as
      `test_quantitative_benchmark[<sName>]` with its outcome.
    - `.vaibify/pipeline_state.json` is git-ignored and does not travel
      between computers.
13. **Quantitative standards are structured, and each runs as one named
    test.**
    - Each entry in `tests/quantitative_standards_<step>.json` names
      `sDataFile`, `sAccessPath`, a reference `fValue` and `fRtol`.
    - The generated test parametrizes with `ids=[s["sName"] ...]`, so a
      standard `<sName>` is node
      `test_quantitative_benchmark[<sName>]`.
    - Qualitative and integrity checks are nodes
      `test_qualitative_check[<sFileName>]` and
      `test_integrity_check[<sFileName>]`.
    - In the QuickStart example, every data output is covered by
      between 6 and 18 standards, and no figure by any.
14. **The integrity standard's `iExpectedByteSize` is recorded but not
    asserted** by the generated integrity test. It must stay
    unasserted, because byte size is a byte check in disguise.
15. **Images can already be read as numbers.** `dataLoaders` loads
    `.png`, `.jpg` and `.tiff` with Pillow as float pixel arrays. Pillow
    is a hard dependency of matplotlib.
21. **glibc's scalar math library dispatches on AVX2/FMA, not AVX-512.**
    Masking AVX-512 alone changed none of the 400,000 results of `exp`,
    `log`, `sin` and `pow`. Masking AVX2 and FMA, with AVX-512 left on,
    changed the same results fact 5 found. So at the `x86-64-v3` floor
    glibc needs no setting: every computer at that level takes the same
    FMA path.

**Evidence, images and run order** (2026-10-17; pytest 9.1.1 and
Pillow 9.0.1 on the host, not the image's versions)

22. **A JUnit testcase's `classname` depends on the project's pytest
    configuration, and a command's own `--junitxml` wins.**
    - `python3 -m pytest tests/test_quantitative.py`, run from a step
      directory, reported classname `tests.test_quantitative` with no
      configuration file at the repository root, and
      `StepOne.tests.test_quantitative` once a root `pyproject.toml`
      existed. With no root configuration, two steps' identically named
      test files give identical classnames.
    - The default report format carries no `file` attribute.
    - With `PYTEST_ADDOPTS="--junitxml=A"` and the command's own
      `--junitxml=B`, only `B` was written.
    - Generated test files carry a step suffix
      (`test_quantitative_<step>.py`, `testGenerator.fsStepTestSuffix`),
      but the templates still serve legacy unsuffixed names, and custom
      tests can be named anything.
23. **Pillow's mode does not describe what a file encodes.**
    - Two palette PNGs with identical pixel indices and different
      palettes (one black, one white) gave `ImageChops.difference`
      extrema `(0, 0)`. Converted to RGBA first, they differed by 255.
      Converting a palette image with `tRNS` transparency to RGBA
      resolved the transparency.
    - A 16-bit-per-channel RGB PNG opened as mode `RGB` (raw mode
      `RGB;16B`), silently reduced to 8 bits. Two such files differing
      only in the low byte compared as identical. The PNG header's
      bit-depth byte still said 16.
    - A 16-bit grayscale PNG opened as mode `I`, and
      `ImageChops.difference` raised `ValueError`.
    - A two-frame TIFF compared only its first frame. A float TIFF
      opened as mode `F` (32 bits per sample, sample format 3).
    - A matplotlib PNG is 8-bit RGBA (header bit depth 8, color type 6).
24. **The runner's order and failure handling**
    (`pipelineRunner.ftRunStepCommands`, `_fiRunStepList`,
    `pipelineTestRunner`):
    - each step runs data, then plots, then tests; the rerun also runs
      a `bPlotOnly` step's data;
    - a failing data command ends the step; a failing plot command
      leaves its tests unrun;
    - tests run as groups in `fdictResolveTestCommandGroups` order
      (integrity, qualitative, quantitative, then the legacy list); a
      failing command ends its group, and every group runs;
    - failing tests never fail a step;
    - **a failed step does not stop the run.** Later steps still run,
      and the run returns a non-zero code;
    - each command is its own `/bin/bash -c` exec with bash's default
      options (`dockerConnection._fdictBuildExecCreateKwargs`), while
      today's `reproduce.sh` runs step commands under
      `set -euo pipefail`.

**Spikes in the image** (2026-10-17; the QuickStart example's pinned
image: Python 3.12.3, pytest 9.1.1, Pillow 12.3.0, matplotlib 3.11.2,
NumPy 2.5.3; throwaway containers with `--network none`)

25. **Spike A: a JUnit report through the runner's exec shape.** A
    generated quantitative test command was run as the runner builds it
    (run-ID export, the determinism prefix, a `PYTEST_ADDOPTS` export,
    then the `/usr/bin/time` wrapper around `/bin/bash -c`), from the
    step directory:
    - the report landed at the exported path, and its testcases were
      named `test_quantitative_benchmark[<sName>]`; a passing case has
      no child element, and `<failure>`, `<skipped>` and `<error>` mark
      the other three outcomes;
    - with no root configuration, two steps' `tests/test_quantitative.py`
      both reported classname `tests.test_quantitative`; a root
      `pyproject.toml` made it `StepOne.tests.test_quantitative`;
    - a command's own `--junitxml=B` wrote only `B`, never the exported
      path;
    - a command that collected nothing exited 5 and wrote a report with
      zero testcases;
    - every report carries the container's `hostname` attribute. The
      assessor never copies it (I7).
26. **Spike B: the image check under the image's own Pillow.**
    - On an 800×350 matplotlib PNG (8-bit RGBA) whose data sat away from
      a contour-level boundary, RGBA per-band
      `ImageChops.difference(...).getextrema()` and the histogram of
      the per-pixel band maximum reproduced fact 20's shape: ×(1+10⁻¹³)
      and ×(1+10⁻⁹) changed nothing; ×(1+10⁻⁶) moved 28 pixels by at
      most 1 level; a moved marker (134 levels), a one-character label
      change (255) and 16 inverted pixels (255, exactly 16 changed) all
      exceed 2 levels. At 1024×1024, 16 changed pixels are counted as
      exactly 16.
    - **A surprise, which fails closed.** The same figure with its data
      maximum at exactly 1.0 moved 162,021 pixels by up to 33 levels
      under ×(1+10⁻⁹): matplotlib's automatic contour levels
      (`MaxNLocator`) jumped from ±1.0 to ±1.25, recoloring every
      filled contour. A rounding-level change that crosses an
      automatically chosen boundary is therefore a large pixel change,
      and D2 reports it as insufficient evidence, never as a pass. A
      figure with fixed levels does not have this sensitivity. This is
      the "would reverse" condition of D2 in its safe direction: it
      cannot produce a false pass, only a figure vaibify will not vouch
      for.
    - Fact 23's cases behave the same under Pillow 12.3.0, with one
      difference: a 16-bit grayscale PNG opens as mode `I;16` (not
      `I`), and `ImageChops.difference` still raises `ValueError`. The
      PNG header's bit depth still says 16, so reading the encoding from
      the file's bytes is unaffected. A 16-bit RGB PNG opens as `RGB`
      and two that differ only in the low byte still compare as
      identical; palette images still need RGBA conversion, and `tRNS`
      transparency resolves; a two-frame TIFF reports `n_frames` 2; a
      float TIFF opens as `F` with `BitsPerSample` 32 and
      `SampleFormat` 3; a CMYK JPEG opens as `CMYK`.

---

## Decisions taken (provisional; each tested once built)

- **D1. Wording.**
  - **The claim:** as above.
  - **The question and the verdicts:** as given in the sections below.
  - **Would reverse:** the researcher finding the words unclear in use.
- **D2. Figures.**
  - **Raster figures** (`.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`) that
    differ in bytes get vaibify's **built-in image-difference check**
    against the published figure. It passes only when:
    - both files are a **supported encoding** (below), in the same
      format, with the same dimensions and the same decoded mode;
    - both are decoded to RGBA, which resolves palettes and palette
      transparency (fact 23), and **no channel of any pixel differs by
      more than 2 levels on the 0–255 scale.**
  - **Supported encodings, read from the file itself, never from
    Pillow's mode** (fact 23):
    - exactly one frame;
    - PNG whose header bit depth is 8 or less, for any color type;
    - JPEG decoding to `L` or `RGB`;
    - TIFF whose every `BitsPerSample` is 8 or less, with unsigned
      integer samples, decoding to `1`, `L`, `LA`, `P`, `RGB` or
      `RGBA`.

    Anything else is **insufficient evidence**, with its reason named:
    16-bit or floating-point samples, CMYK, several frames, a decoding
    error, or Pillow missing. A higher-precision image is never reduced
    to 8 bits to make it comparable; the remedy is a quantitative
    standard over the data it shows.

    The claim is narrow and labeled as such: "within vaibify's
    image-difference threshold (policy v1)". It is never presented as
    an author-declared comparison, and never as proof that no
    meaningful change occurred. The report always gives the largest
    pixel change and the count of changed pixels.
  - **Failing the threshold is "insufficient evidence", not "comparisons
    failed".** The threshold is vaibify's rule, not the author's
    scientific criterion, so failing it means vaibify cannot vouch for
    the figure. It still blocks Level 3, so the rule fails closed.
  - **Vector figures** (`.pdf`, `.eps`, `.svg`) that differ in bytes are
    **insufficient evidence**. Vaibify has no assessment for them, and
    there is no exemption for steps whose data passed, which also
    removes the empty-set problem of plot-only steps. The report names
    the remedy: the "across computers" answer, a raster companion
    figure, or a quantitative standard over the plotted data.
  - **The reference** is the published figure, read before the rerun
    deletes outputs and only after its manifest hash is confirmed.
  - **Rationale:** in fact 20, data changes up to 10⁻⁶ relative moved
    no pixel by more than 1 level, while every meaningful edit tested
    moved some pixel by 197 or more.
  - **Disclosed limit:** a change confined to pixels that move by 2
    levels or less passes, such as an element drawn at under 1%
    opacity.
  - **Would reverse:** a rounding-only difference exceeding 2 levels in
    real projects, or a meaningful change that stays within it.
- **D3. Two choices, not three.** The reason is simplicity and
  usefulness. A middle "repeatable on this computer" choice would
  differ from "Prioritize speed" only by a fixed thread count. Fact 17
  is consistent with that difference rarely mattering on one computer,
  but does not establish it.
  - **Would reverse:** projects whose same-computer reruns differ for a
    reason the thread count controls.
- **D4. No pre-selected answer.** "Repeatable across computers" carries
  a **"Recommended for most projects"** label, because two computers are
  the primary workflow (ruling 3). At full speed, a rerun on a computer
  with a different processor or core count can change bytes (facts 1,
  16, 18). When it does, each affected step's sign-off reads stale. The
  cost is stated in the choice itself (fact 19). The radio buttons stay
  unselected, matching every other answered question in vaibify.
  - **Would reverse:** projects choosing the recommendation and finding
    the measured slowdown unacceptable in practice.
- **D5. Floor `x86-64-v3`, as profile `across-v1`.**
  - OpenBLAS: `Haswell`.
  - NumPy: `NPY_DISABLE_CPU_FEATURES=AVX512_ICL,X86_V4`.
  - glibc: no setting (fact 21).
  - The exact NumPy names vary by build, and the probe confirms them
    (F7).

  The floor is part of a **versioned profile**, not a loose constant.
  Moving to `v2` would be a new profile, `across-v2`; envelopes recorded
  under `across-v1` keep replaying `across-v1` (F9).
  - **Would reverse:** Stage 5's check of the Mac emulator (0a) showing
    it lacks AVX2 or FMA. That leads to `across-v2`: `Nehalem`, NumPy
    without `X86_V3`, and glibc also masking AVX, AVX2 and FMA.
- **D6. Exit codes for the assessment**, shared by `reproduce.sh`,
  `vaibify reproduce --rerun` and `--from`, and the report:
  - **0:** the Level 3 assessment passed (byte-identical, or
    comparisons passed with byte differences; the verdict line says
    which)
  - **1:** a comparison failed, an output is missing, or a step's data
    or plot command failed
  - **3:** published inputs changed
  - **4:** could not run
  - **5:** insufficient evidence

  **2 is reserved.** Click exits 2 on a usage error, and today's
  `reproduce.sh` exits 2 when it cannot obtain the image (that becomes
  4). Revised from the first assignment, which gave 2 to insufficient
  evidence.

  **Would reverse:** a CI consumer needing "byte-identical" separated
  from "comparisons passed" (then a new code 6).
- **D7. Migration and historical attestations.**
  - Schema v13 → v14 leaves every existing project's determinism
    question unanswered, because no legacy answer maps unambiguously to
    a choice.
  - **An existing attestation stays valid as historical evidence of the
    old byte check**, displayed as "Byte-identical under the previous
    check (before <date>)". It does **not** satisfy the new Level 3
    rebuild requirement, because the old verifier could pass with
    failing tests (fact 9, F11).
  - Every existing Level 3 project therefore needs one fresh assessment.
    That is acceptable before release.
  - Retirement under F8 is keyed to the applied settings, so answering
    "Prioritize speed" on a legacy project does not by itself retire
    anything. The new contract still needs the fresh assessment.
- **D8. One thread.** The "across computers" profile sets
  `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`. The reasons:
  - thread count changed nearly every linear-algebra result tested
    (fact 16);
  - a count above 1 was silently reduced on a computer with fewer cores
    (fact 18);
  - one thread is the only count every computer can honor.

  For a researcher's own code, `OMP_NUM_THREADS=1` removes thread-count
  variation only from OpenMP code that honors the variable. It does not
  cover code that sets its own thread count, other threading libraries,
  or any other source of nondeterminism.
  - **Would reverse:** evidence that a fixed count of 2 is honored on
    every computer a project meets, buying back some speed at the price
    of a refusal path for single-CPU machines.

---

## The frozen execution profile and comparison policy (F7, F9)

The envelope records, and every lane replays, these concrete facts:
- **Profile identity:** the profile name and version (`speed-v1`,
  `across-v1`).
- **Requested settings:** the exact environment variables and values
  the runner exported. Replay sets these values; it never looks the
  answer up in the current table.
- **Probe-observed settings:** in a separate process inside the
  container, immediately before the steps, read back OpenBLAS's core
  name and thread count, NumPy's disabled features, and whether glibc
  accepted the tunables. Each item is "applied", "not applied", or
  "fixed at build".
- **Execution-observed settings: not claimed.** The probe establishes
  what the probe process saw. Steps inherit the same environment, but a
  step that changes its own settings, for example by calling
  `threadpoolctl` or rewriting `os.environ` before importing NumPy, is
  not detected. Reports say "requested, and confirmed in a probe
  process", never "used by every step".
- **Comparison-policy version:** `comparison-v1` fixes:
  - the precedence rules below;
  - the image-difference threshold (2 levels);
  - which file types count as raster or vector;
  - the node-ID mapping of fact 13.

  A reproduction grades under the version recorded with the manifest,
  and also reports the current version if it differs.
- **How the recorded policy is kept and selected.**
  - **One record:** a header line in the pinned assessor file
    `.vaibify/rerunAssessment.py`, which `reproduce.sh` runs and the
    manifest pins.
  - **The dashboard lanes read that line and run vaibify's own frozen
    code for that version**, never the repository's copy. Each policy
    version is a separate module whose bytes a test pins, so a later
    vaibify cannot change what `comparison-v1` means. A fix is a new
    version.
  - **A version this vaibify does not have** gives "Could not run: this
    project was assessed under ⟨policy⟩, which this version of vaibify
    does not include; upgrade vaibify." Exit 4.
  - **A project with no recorded policy** is graded under the current
    one, and the report says none was recorded.
  - **Reproduction and eligibility are separate questions.** A
    reproduction grades under the recorded policy. The Level 3 rebuild
    requirement accepts only an attestation made under the current
    policy (D7), so an author regenerates `reproduce.sh` to move to a
    new one.
- **Unsupported settings are reported explicitly, and capabilities are
  read first.**
  - Every lane reads the processor's capabilities in a process that
    loads no numerical library, before any step or probe runs under the
    profile. A computer that cannot honor part of the recorded profile,
    for example a processor without `x86-64-v3`, then runs without that
    part and records "floor not applied: this processor lacks
    x86-64-v3".
  - Only after that decision does the probe load NumPy and OpenBLAS,
    under exactly the settings the steps will get. NumPy absent, or no
    OpenBLAS the probe can find, is recorded as such, never guessed.
  - The assessment is then graded exactly as always. For the author,
    the dashboard says so before running.

---

## The outcome model and precedence (F1, F2, F4, F10)

### Precedence, applied in order

1. **Could not run**: the rerun could not be started or graded (the
   image, the container, a refused preparation, or the assessor itself
   failed). No verdict, exit 4. A published step command that ran and
   failed is not this case; it is graded under rule 3.
2. **Published inputs changed**: any pinned input differs **or is
   missing**, including a deleted test file, standard or script. The
   paths are named, and no agreement verdict is given. Exit 3.
3. **Per regenerated output**, the first matching rule wins:

   | Rule | Outcome |
   |---|---|
   | The step section that writes it (data or plot) did not finish with every command exiting 0, including a section skipped because the one before it failed | **Failed** (the command and its exit status are named) |
   | The rerun did not write it | **Failed** (missing) |
   | Any expected check for it executed and failed, even if its bytes match | **Failed** |
   | Any expected check for it did not execute (skipped, not collected, errored before asserting, or its command never ran) | **Insufficient evidence** |
   | Bytes identical, and every expected check executed and passed (or none is expected) | **Byte-identical** |
   | Bytes differ; at least one agreement comparison for it exists; every expected check executed and passed | **Comparisons passed** |
   | Bytes differ, and no agreement comparison exists (required checks alone never count), or a raster figure fails D2's threshold or cannot be assessed | **Insufficient evidence** |

4. **Step-level checks not attributable to one output** (a custom test
   or test command that names no output file, a generated file's
   placeholder test):
   - an executed failure makes the overall verdict **Failed**;
   - a check that did not execute (skipped, errored, not collected, or
     its command never ran) makes it at least **Insufficient
     evidence**;
   - they are required checks, and never establish agreement for any
     output.
5. **Overall:**
   - **Failed** if any output or step-level check failed;
   - else **Insufficient evidence** if any output or step-level check
     lacks evidence;
   - else **Comparisons passed, with byte differences** if any output
     differs in bytes;
   - else **Byte-identical**.

   The tally beside the verdict always lists every category, so a
   mixed result (some outputs failed, others lacking evidence) shows
   both.

**An output's expected checks are of two kinds:**
- **Agreement comparisons**, the only evidence that changed bytes still
  agree with the published ones:
  - every quantitative standard whose `sDataFile` names the output;
  - D2's image check, for a raster figure whose bytes differ. It never
    yields Failed: failing its threshold, or being unable to assess the
    figure, is Insufficient evidence (D2).
- **Required checks**, which must execute and pass but say nothing
  about agreement: the integrity and qualitative checks naming the
  output. The generated ones assert existence, shape, finiteness,
  columns and keys (`templateManager`), never values.

### Verdicts as shown

| Overall verdict | Wording | Exit |
|---|---|---|
| Byte-identical | "Reproduced: every file byte-identical" | 0 |
| Comparisons passed | "Reproduced: published comparisons passed; ⟨n⟩ of ⟨m⟩ files differ in their bytes" | 0 |
| Failed | "Not reproduced: ⟨files⟩ failed", each with its reason (a comparison failed, a command failed, or the file is missing) | 1 |
| Insufficient evidence | "Not enough evidence: ⟨files⟩ were not assessed" | 5 |
| Inputs changed | "Published inputs changed: ⟨paths⟩" | 3 |
| Could not run | "Could not run: ⟨reason⟩" | 4 |

Level 3's rebuild requirement is met by exit 0, under either answer to
the question and only under the current comparison policy (D7).

### The evidence contract (F4)

- **Each expected check is identified individually** by its step, its
  test category, its test file's repository path and its testcase name
  (fact 13), or by D2's check record. A passing test category, a
  passing file, or a standard that exists but was never evaluated is
  not evidence.
- **Evidence is bound to the command that produced it.** Every command
  the rerun runs (data, plot and test) has an identity: its step, its
  section or test group, and its position. Each test command writes its
  own JUnit report, named by that identity. An expected check is looked
  for only in the reports of its own step's commands for its own
  category, so one step's report can never satisfy another's check
  (fact 22).
- **Every command is accounted for.** The rerun records each command's
  exit status. A command with no record never ran.
  - A test command expected to report checks (a generated category's
    command) that writes no report, or whose report cannot be read,
    leaves those checks unexecuted, with the reason named, for example
    that the command named its own `--junitxml`.
  - A command expected to report nothing (a custom test command) is
    judged by its exit status alone, as a required step-level check.
  - A test command that exits non-zero with no failure in its report
    did not assert anything: pytest interrupted, errored, or collected
    nothing.
- **Repeated results combine worst first.** When a check appears in
  more than one report, a failure anywhere fails it, else any
  non-passing result leaves it unexecuted. A later pass never erases an
  earlier failure.
- **Ambiguity is never resolved by guessing.** When a reported testcase
  could belong to two of the step's expected test files, or a
  standards file repeats an `sName`, every check involved reads
  "insufficient evidence", naming the cause.
- **References and tolerances are pinned inputs**, checked exactly in
  step 2 of the precedence.
- **Evidence is fresh.** Node outcomes come only from JUnit reports
  that this execution writes into a private, newly created directory,
  through `PYTEST_ADDOPTS` (fact 12). Test markers are never read for
  the assessment. Pinned inputs are hashed before and after execution,
  and a change while running is "Published inputs changed".
- **Without a quantitative standard or D2's check, changed bytes are
  never accepted.** Integrity, qualitative and custom tests that pass
  leave a byte-differing output at "insufficient evidence".

---

## Hardware and settings provenance (F3)

- **Recorded at production time, not at manifest time, for exactly
  what was produced.** When a step's data section finishes with every
  command exiting 0, the runner records its data outputs; when its plot
  section does, its plot outputs. A section that did not run records
  nothing: a plots-only run, or a `bPlotOnly` step's skipped data,
  leaves the data outputs' records alone, and a tests-only run records
  nothing at all. For each recorded output it keeps:
  - its digest;
  - the hardware context: architecture, x86-64 level, vendor, OpenBLAS
    core name, NumPy's active features, glibc's active hwcaps, and the
    visible core count;
  - the profile identity, requested settings and probe-observed
    settings;
  - the run ID.
- **In a new tracked file** (for example
  `.vaibify/outputProvenance.json`) that only the producing run writes.
  Test markers are the wrong home: they record output hashes at test
  time, so a laptop test run would relabel outputs the desktop made.
- **Into the envelope at manifest time.** Writing the manifest copies
  each pinned output's record, bound by digest, into
  `.vaibify/environment.json`, which is itself pinned. A pinned output
  with no record reads **"production context unknown"**, never a guess.
- **Explanation only.** A byte difference is reported with both
  contexts, for example "made with 16 cores and AVX-512 under
  speed-v1; rerun with 8 cores under speed-v1". Vaibify never predicts
  whether bytes will match.
- **Two-computer messaging** is neutral and says only what the record
  shows: "This output was produced under different execution settings
  or hardware" is information, never colored as a failure. Vaibify does
  not claim it was a different computer.

---

## The question

> **How much speed should vaibify trade for repeatable files?**
>
> - **Prioritize speed.** Vaibify leaves your math libraries at full
>   speed. Files made on another computer can differ in their last
>   digits; Level 3 judges them by your published comparisons.
> - **Repeatable across computers** — *Recommended for most projects.*
>   Vaibify runs NumPy, OpenBLAS and the C math library on one thread,
>   using instructions that supported computers share. Your computers
>   are then more likely to produce identical files. Heavy numerical
>   work can run several times slower. This reduces differences between
>   computers; it cannot eliminate them.
>
> *Your answer changes how steps run, not what Level 3 requires.
> Changing it later means rerunning every step once, and it retires
> your current Level 3 attestation.*

A collapsed "Why does this matter?" panel says, in three sentences,
that the container pins the software but not the processor, so last
digits can differ between computers.

| | `speed-v1` | `across-v1` |
|---|---|---|
| `SOURCE_DATE_EPOCH` and SVG salt (today's) | yes | yes |
| `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1` | no | yes |
| Floor `x86-64-v3` for NumPy and OpenBLAS (glibc needs none, fact 21) | no | yes |
| Measured cost on one 8-core machine (fact 19) | none | ~3–4× dense linear algebra, ~3× vectorized `exp`/`log` |
| What Level 3 requires | exit 0 under `comparison-v1` | the same |

- **One builder, both lanes.** The runner's prefix
  (`determinismEnvironment._fsBuildDeterminismEnvPrefix`,
  `_fdictBuildHostDeterminismOverlay`) and `reproduce.sh`'s preamble
  (`reproduceScriptGenerator._fsBuildContainerPreamble`) take their
  settings from one versioned table and one builder, as
  `fsBuildMatplotlibSaltShell` already does for the salt.
  `reproduce.sh` replays the requested settings recorded in the
  envelope (F9).
- **F8.** The attestation records the profile identity and the
  requested settings. A change to either retires it, while the history
  is kept.

---

## Stages, in order

### Stage 1: the outcome model, precedence and evidence contract

No CPU settings are touched yet.
- **`rerunVerification.fdictVerifyRerunOutputs`:** the precedence above,
  per-output outcomes, node-level evidence, the overall roll-up, and
  D2's image check.
- **`shadowRerun`:**
  - before the run: confirm each published figure against its manifest
    hash and set it aside, and hash the tests and standards;
  - during the run: point `PYTEST_ADDOPTS` at a private evidence
    directory;
  - after the run, before teardown: read the JUnit reports and hash the
    tests and standards again.
- **`reproductionReport`:** verdict constants beside
  `S_VERDICT_REPRODUCED`, `S_VERDICT_DIVERGED` and
  `S_VERDICT_NO_VERDICT`, rendered with the wording above in
  `fsRenderVerdict`.
- **`l3Attestation`:** a schema bump recording:
  - the precedence results and per-output outcomes;
  - the node-level evidence;
  - the comparison-policy version, profile identity, requested and
    probe-observed settings, and production contexts.

  Migrators write `None` ("predates capture") and mark old records
  historical (D7).
- **`levelGates` and the PROOF tab:** the rebuild-attestation row reads
  the new overall verdict and its policy version, and shows a
  historical attestation as historical. Follow `dashboard-state-honesty`
  throughout.

### Stage 2: reader-path parity (F5)

- **`reproduce.sh`:**
  1. checks integrity (hashes of pinned inputs only, including test
     files);
  2. sets the published figures aside;
  3. **deletes what the rerun regenerates**, with the shadow's own
     classification and refusals (carried outputs and protected files
     are never touched). Without this, a command that exits 0 and
     writes nothing would pass on the published file;
  4. reads the processor's capabilities, then replays the recorded
     requested settings it can honor;
  5. runs every step in the runner's order with its failure handling
     (fact 24), recording each command's exit status;
  6. applies the recorded comparison policy, prints the dashboard's
     verdict and tally, and exits with D6's code. Every exit path ends
     in one of D6's codes; a shell error is "Could not run".

  It cannot import vaibify, so its comparison logic must be emitted
  from the same source the dashboard uses, never a second copy. The
  `reproducibility-lane` skill records a past drift between these two
  lanes.
- **This supersedes the 2026-09-01 decision**, recorded at
  `pipelineUtils.T_EXECUTED_COMMAND_KEYS`, that `reproduce.sh` neither
  installs pytest nor runs tests. F5 requires both.
- **`vaibify reproduce` and Reproduce a published project** report
  through the same seams.
- **No lane asks a reader to regenerate the manifest.** For the author,
  "rerun every step here and regenerate the manifest" is offered only
  as an option.

### Stage 3: production-time provenance

As specified above, plus the envelope copy at manifest time.

### Stage 4: one question replaces three

- **The question:** `determinismGate.LIST_DETERMINISM_QUESTIONS` becomes
  one two-answer entry, with no schema keys in its wording.
- **Readers to update**, from a grep of the current keys:
  - `levelGates.fbVerifyDeterminismDeclared`;
  - `reproducibilityRoutes`: `_fdictValidateDeterminismBody`, its type
    table, and the declare and delete routes;
  - the `actionCatalog.py` entry;
  - `static/scriptWorkflowRequirements.js`;
  - `cli/commandReproduce.py`;
  - `replayGate.py`;
  - a comment in `pipelineRoutes.py`.
- **Migration v13 → v14** in `workflowMigrations.py`, per D7.
- **Docs and skills** that describe three questions:
  - `docs/environmentsAndProjects.md`, `docs/architecture.md` and
    `docs/proofLadder.md`;
  - the `reproducibility-lane` and `dashboard-state-honesty` skills.
    Editing a `SKILL.md` triggers the sensitive-edit hook and requires
    `tests/testSkillIntegrity.py` plus the trigger and outcome harnesses.

### Stage 5: the profiles, builder and probe

- The versioned profile table (`speed-v1`, `across-v1`) and the builder,
  wired into both lanes.
- The probe.
- The not-applied path.
- The remaining measurements, which do not block building:
  - **0a.** In an amd64 container on the Mac, record `/proc/cpuinfo`
    flags, `numpy.show_runtime()` and OpenBLAS's core name, under
    colima's QEMU and under Rosetta. This confirms `across-v1` or leads
    to `across-v2` (D5).
  - **0b.** Force a kernel the CPU lacks (`SkylakeX` on the Mac). Does
    it crash or fall back? The probe must run before any step either
    way.
  - **0c.** Does SciPy's separately bundled OpenBLAS honor the same
    settings? Add it to the table only with a live test, as a new
    profile version.
  - **0e.** Log each CI runner's CPU and the hash of a generic fixture
    under each profile. This is cross-vendor evidence, not a guarantee.

### Stage 6: documentation

- **`docs/proofLadder.md`:** the Level 3 row and paragraph, and the
  "Determinism declared" row, per D1.
- **`docs/quickStart.md` section 7:** "Every file now matches" becomes
  the verdict a reader actually gets.
- **`docs/reproducibility.md`:**
  - integrity versus agreement, and the precedence;
  - the evidence contract and the image-difference check, with its
    disclosed limit;
  - provenance;
  - the two profiles with their measured cost.
- **`docs/lessons.md`:** a pinned image was read as pinned results, and
  every check agreed because no two runs ever came from different
  processors or core counts. This is the same shape as the name-vs-id
  bug.
- Run `python tools/checkAmericanSpelling.py`.

### Stage 7: tests, falsification first

`CLAUDE.md`: assert a cross-boundary guarantee with the distinguishing
keys made distinct and a real connection. Each item below is a
falsification entry for `tools/reconfirmFalsification.py`.

- **Integrity cannot be loosened.** A changed tolerance, a changed
  standard, or a **deleted** test file each yield "Published inputs
  changed".
- **Byte equality never overrides test evidence (F10).**
  - Bytes identical with a failing quantitative test yields "Failed".
  - Bytes identical with that test skipped yields "Insufficient
    evidence".
- **Each comparison is identified.** A standard whose node did not
  execute yields "insufficient evidence" for its output, even when
  other tests in the same file passed. Duplicate `sName` values yield
  "insufficient evidence".
- **Stale evidence cannot pass.** A shadow seeded with the author's
  passing markers, whose tests do not run, yields "insufficient
  evidence".
- **Fail closed:** zero comparisons, a missing output, and a standard
  edited mid-run.
- **Structural checks never stand in for agreement.** A data file whose
  bytes differ, with only passing integrity and qualitative checks,
  yields "insufficient evidence".
- **A clean rerun in every lane.** A data command that exits 0 and
  writes nothing yields "Failed (missing)" from the dashboard and from
  `reproduce.sh`.
- **Evidence is bound to its step and command.** Two steps with
  identically named test files and no root pytest configuration: one
  step's passing report never satisfies the other's absent check. A
  failure in one report is not erased by a pass in another.
- **The recorded policy is replayed.** After a test-only policy with
  different behavior becomes current, a publication recorded under
  `comparison-v1` still grades under `comparison-v1`, and its
  attestation no longer satisfies the current requirement.
- **Unsupported images are never compared.** Palette images that
  differ only in their palettes fail the check; a 16-bit or multi-frame
  image yields "insufficient evidence".
- **The image check discriminates at realistic sizes.**
  - Last-digit and 10⁻⁶ redraws pass.
  - At 1024×1024 or larger, 16 inverted pixels, a moved marker and a
    one-character label change all fail. This is the review's
    counterexample, which must stay killed.
  - A changed vector figure yields "insufficient evidence".
  - A plot-only step gets no exemption.
- **Replay is frozen (F9).** An envelope recorded under `across-v1` is
  replayed with `across-v1`'s settings after the table gains
  `across-v2`.
- **Provenance is not relabeled.** Outputs produced under context A and
  tested under context B still read context A, with the contexts
  differing in the field that matters (core count, x86-64 level). A
  plots-only run under context B relabels only the plots.
- **Historical attestations stay historical (F11).** An old passing
  attestation never satisfies the new rebuild requirement.
- **Parity.** The dashboard, CLI and `reproduce.sh` give the same
  verdict and exit code on the same tree, for each precedence case.
  Drive real `bash`, modeled on
  `tests/testReproduceScriptDeterminism.py`.
- **Settings truly requested and probed.** In a container restricted to
  two cores, a generic linear-algebra fixture under `across-v1` must
  match its hash on all cores, and must match a committed hash on every
  CI runner. It must also differ under `speed-v1` whenever the core
  counts differ.
  - A runner that cannot apply the profile fails loudly; it never skips
    green (the `VAIBIFY_REQUIRE_*` precedent).
  - If no CI runner has AVX-512, record the floor's half as a
    falsification entry and say so in the pull request.
- **Verdict honesty.** A comparison-based verdict is never rendered as
  byte-identical, an image-check pass is always labeled as vaibify's
  threshold, and the tally is always present.
- **The browser lane** for the question form and verdict rendering:
  `python -m pytest tests/browser -m browser`.

### Stage 8: the researcher's test checklist (after building)

This replaces approval of D1–D8 in advance. Expected results are
predictions to check, not promises.
1. **One computer.** Run the QuickStart flow under each answer, on the
   AVX-512 Linux host and on the Mac. Expected:
   - **Prioritize speed:** "Reproduced: published comparisons passed; 3
     of 27 files differ in their bytes" on the Linux host, and
     byte-identical on the Mac.
   - **Repeatable across computers:** byte-identical on both, if the
     Mac's emulator supports `x86-64-v3` (0a). If it does not, the Mac
     reports "floor not applied".
2. **Two computers.** Make the outputs on the Mac, push, pull on Linux,
   and Verify. Expected:
   - each output's production context names the Mac;
   - the verdict follows item 1;
   - nothing asks to regenerate the manifest.
3. **Change the answer.** The current attestation is retired, and the
   history keeps it.
4. **`reproduce.sh` on each machine:** the same verdict and exit code as
   the dashboard.
5. **Tampering.**
   - Loosen one tolerance, or delete one test file: "Published inputs
     changed".
   - Make one test skip itself: "Not enough evidence".
   - Make one test fail while the outputs stay byte-identical: "Not
     reproduced".
   - Make one data command exit 0 without writing its file: "Not
     reproduced", naming the file as missing, from the dashboard and
     from `reproduce.sh`.
6. **Figures.**
   - Confirm that the largest pixel change is reported.
   - Change one label: the figure is "not assessed".
   - Save one figure as PDF: if its bytes differ, it is "not assessed".
7. **Speed.** Time a heavy step under each answer, against fact 19's
   numbers.

### Stage 9: republish the QuickStart example (after the checklist)

- **The image needs no rebuild and no new upload.** With the settings
  applied, this same image matched all 27 files on the AVX-512 host.
- Answer the question, rerun every step on the author's machine,
  regenerate `reproduce.sh`, `.vaibify/environment.json` and
  `MANIFEST.sha256`, then commit, push and run a fresh assessment. The
  old attestation is historical under D7.
- **The data deposit changes only if output bytes change.** It holds
  declared outputs and tests
  (`dataArchiver.flistCollectArchiveFilePaths`), not the manifest or the
  envelope. At the `v3` floor on one thread, the outputs were identical
  to the current deposit on one machine. Whether that holds on the
  author's machine is item 1 of Stage 8.

---

## Known limits to state, not hide

- A verification establishes what happened in the tested environment,
  not what will happen on every future computer. Cross-machine CI runs
  are evidence, not a guarantee.
- **Settings are requested and confirmed in a probe process; they are
  not observed in every step's process** (F7).
- **The image-difference check is narrow.** It passes changes confined
  to pixels that move by 2 levels or less. Passing it is not proof that
  a figure carries the same science. It assesses only single-frame
  images with 8 or fewer bits per sample (D2).
- **`reproduce.sh` regenerates in the reader's own directory**, as it
  always has, and now deletes the declared outputs first. A run that
  fails leaves those outputs absent rather than stale; a git clone
  restores them with `git checkout`.
- **OpenBLAS's block sizes may depend on cache size** for some kernels.
  If so, two processors with the same instructions, kernel and thread
  count could still differ. This is believed possible and unverified;
  Stage 5's 0e is the first place it would show.
- The profiles cover only NumPy, OpenBLAS and glibc. Vaibify should name
  other libraries in the image that choose code by processor (Intel
  MKL, oneDNN under PyTorch, Numba, Julia, FFTW, SciPy's own OpenBLAS
  until 0c) and say they are not covered.
- GPU work is not covered by either profile.
- Native arm64 images are out of scope; an arm64 host reaches today's
  amd64 images through emulation.
- **Separate from this plan, a performance issue (fact 18):** vaibify's
  CPU limit does not limit OpenBLAS's threads. Under `speed-v1`, a
  container limited to a few CPUs still starts one OpenBLAS thread per
  host core. It belongs in its own issue.
- The measurements come from one machine, the figure measurements from
  one synthetic figure.
