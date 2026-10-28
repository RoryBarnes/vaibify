---
name: add-data-loader
description: Recipe for adding a new file-format loader to vaibify/gui/dataLoaders.py. Use when the task is to support reading a new data file extension (e.g., netCDF, Zarr, a domain-specific binary format) in quantitative benchmark tests.
---

# Adding a new data-file loader to vaibify

Use this recipe when the task is to support reading a new data file
extension in vaibify's quantitative benchmark tests.

`vaibify/gui/dataLoaders.py` is the dispatch table mapping file
extensions to loader functions. The text between its markers is also
embedded verbatim, by `fsReadLoaderSource()`, into the self-contained
quantitative test file vaibify generates
(`templateManager.fsBuildQuantitativeTestCode`). This embedding is the
primary constraint on every loader you add. The introspection script
that proposes benchmarks does NOT embed it: it keeps its own copy of the
format handling, which you extend separately (step 6).

## Prerequisites

Read these first:

- `AGENTS.md` (repo root) — global rules and traps
- `vaibify/gui/dataLoaders.py` — the existing loaders, the
  `# -- begin loader source` marker block, and the module docstring's
  "Where these loaders run"
- `vaibify/gui/introspectionScript.py` — the f-string executed inside
  containers, with its own `_DICT_FORMAT_MAP` and benchmark chain; read
  its module header first
- `vaibify/gui/templateManager.py` — `fsBuildQuantitativeTestCode`,
  which embeds the loader source, and `SET_PRIOR_TEMPLATE_HASHES`, which
  records the template hashes a change retires

## The critical invariant

The text between the `# -- begin loader source` and
`# -- end loader source` markers in `dataLoaders.py` is read as a
string by `fsReadLoaderSource()`, then inlined into a self-contained
Python test file that runs wherever the project's tests run: **inside
the container** for a container project, and in the hub's pytest
subprocess on the researcher's machine for a host project. The
`vaibify generate-standards` command also imports the loaders on the
host (`vaibify/testing/standards.py`); the hub process itself only
embeds the text (`tests/testDataLoadersStayOutOfTheHub.py`). That
environment has a known Python version and a limited set of packages
available. This means:

- Loaders must be **pure Python** plus the packages already used in
  the loader-source block (`json`, `pathlib`, `re`, `numpy`). Do not
  introduce new top-level imports into the loader-source block
  without confirming the dependency is always present in the
  container images vaibify supports.
- If a format requires an optional package (e.g., `netCDF4`, `zarr`,
  `pyarrow`), the loader must `import` inside the function body and
  raise a clear error if the import fails. Do not place the import at
  module top level inside the loader-source block.
- Loaders read only the path they are handed: no configuration, no
  host-only utilities. Build paths with the `pathlib` the block already
  imports, as `_ffLoadValue` does.
- Loaders must be deterministic and side-effect-free beyond the file
  read.

## Steps

### 1. Add the extension to `_DICT_FORMAT_MAP`

In `dataLoaders.py`, inside the loader-source block (between the
`# -- begin loader source` and `# -- end loader source` markers), add
the extension(s) to `_DICT_FORMAT_MAP`, mapping to a canonical format
string you'll use in step 2:

```python
_DICT_FORMAT_MAP = {
    ...
    ".nc": "netcdf",
    ".nc4": "netcdf",
    ...
}
```

Keys are lowercase: the lookup lower-cases the file's suffix, so a
mixed-case key can never match
(`test_every_advertised_extension_actually_resolves`). List the new
format in the format table of `docs/testing.md`.

### 2. Implement the loader function

Also inside the loader-source block, define
`_ffLoad<Name>Value(sFullPath, dictAccess)` following the shape of
the existing loaders. `sFullPath` is the path `_ffLoadValue` has
already joined onto the step directory; `dictAccess` is the dict
`_fdictParseAccessPath` parsed from the access path (`key`, `column`,
`dataset`, `section`, `hdu`, `listIndices`, `sAggregate`). Finish
through the shared extractors (`_ffExtractArrayValue`,
`_ffExtractTabularValue`, `_ffExtractDataframeValue`) so index and
aggregate syntax behave as they do for every other format.

Conventions:

- Function name: `_ffLoad<Name>Value` — `ff` because it returns a
  float.
- Optional dependencies: import inside the function body and raise a
  clear `ImportError` with a helpful message if missing.
- Errors: when the file cannot be read or the access path does not
  resolve, raise `ValueError` naming the path and the format, chained
  from the library's error, as `_ffLoadVtkValue` does
  (`Failed to load <path> as vtk: ...`).
- Return type: a Python `float`, not an array; the generated test
  compares it with `np.allclose`.
- Every binding carries a cast prefix
  (`testVariableBindingsCarryCastPrefixes`). Unprefixed names in older
  loaders (`mesh`, `table`) are seeded debt, not a pattern to copy.

### 3. Register the loader in `_DICT_LOADERS`

Still inside the loader-source block, add the mapping:

```python
_DICT_LOADERS = {
    ...
    "netcdf": _ffLoadNetcdfValue,
    ...
}
```

`DICT_LOADERS`, below the end marker, is the public alias of this dict;
do not edit it.

### 4. Confirm public symbols are unchanged

The module-level `__all__` should still be
`["DICT_FORMAT_MAP", "DICT_LOADERS", "ffLoadValue", "fsReadLoaderSource"]`.
Do not add the new loader function to `__all__` — loaders are
internal to the dispatch.

### 5. Re-pin the quantitative template hash

Any edit inside the marker block changes the generated quantitative
test, so `tests/testGuiTemplateManager.py::testTemplateHashesArePinned`
fails until you add the outgoing hash
(`DICT_PINNED_TEMPLATE_HASHES["quantitative"]` in that test) to
`SET_PRIOR_TEMPLATE_HASHES` in `vaibify/gui/templateManager.py`, then
pin the new hash the failure message prints. Skip the first half and
every project's untouched quantitative test files read as custom
scripts.

### 6. Mirror the format in the introspection script

`introspectionScript.py` is an f-string executed inside containers (and
on the researcher's machine for a host project), with its own copy of
the format handling, because a container script cannot import from the
host. No test binds the two copies, so a format added only to
`dataLoaders.py` loads but is never introspected: no benchmarks are
proposed for it, and nothing says so. Inside the template that
`_fsBuildIntrospectionScript` returns:

- add the extension(s) to its `_DICT_FORMAT_MAP`;
- add an `elif sFormat == "<name>":` branch to `_fnLoadAndBenchmark`;
- add `_fnBenchmark<Name>(sFullPath, sFileName, dictReport)`, modeled
  on `_fnBenchmarkTfrecord`: an optional import that fails sets
  `dictReport["sError"]` and returns rather than raising, and every
  access path it emits must be one your loader reads back.

Every literal brace in that text is doubled (`{{`, `}}`), because the
whole script is one f-string: a single brace is interpolated on the
host, which either breaks the build of the script or changes it
silently.

### 7. Raise the two module-size caps

`dataLoaders.py` and `introspectionScript.py` are held to their entries
in `tests/testArchitecturalInvariants.py::DICT_GRANDFATHERED_MODULE_LINES`,
each the module's size when it was last raised, so a new format crosses
both. Raise each by exactly the lines the edit adds, with a one-line
dated reason above it, as the TFRecord fix did:

```python
    # 1222 -> 1229 (2026-10-01): a TFRecord feature is read from the decoded example.
```

### 8. Add a test

Create `tests/testDataLoader<FormatName>.py`. At minimum:

- Confirm the extension is in `_DICT_FORMAT_MAP`.
- Confirm the format string is in `DICT_LOADERS`.
- Call `ffLoadValue` against a small fixture file and assert the
  returned scalar matches the expected value.
- Run the generated introspection script over the fixture, as
  `tests/testIntrospectionTfrecord.py` does
  (`_fsBuildIntrospectionScript`, then `subprocess.run` with
  `sys.executable`), and assert that a benchmark names an access path
  `ffLoadValue` reads back.
- If the format requires an optional dependency, add a test that
  skips cleanly when the dependency is absent (`pytest.importorskip`).
  CI installs `.[dev]`, not the `formats` extra.

Keep fixture files small and science-agnostic. Do not commit real
scientific datasets. A test fixture should be a hand-crafted
minimal file that exercises the format's structure.

### 9. Verify both embedded sources still parse

`fsReadLoaderSource()` returns the text between the markers as a
string. That string must be valid Python on its own — it becomes the
body of the generated test file. Run:

```bash
python -c "from vaibify.gui.dataLoaders import fsReadLoaderSource; exec(fsReadLoaderSource())"
python -c "import ast; from vaibify.gui.introspectionScript import _fsBuildIntrospectionScript; ast.parse(_fsBuildIntrospectionScript([], 'data'))"
```

If the first fails, something in your edit broke the embedded loader
source. Common causes: a top-level statement that uses a name defined
outside the block; a top-level import of a package that is not
installed. If the second fails, a brace in step 6 was not doubled.

### 10. Run the test suite

```bash
python -m pytest tests/testDataLoader<FormatName>.py tests/testDataLoaders.py tests/testGuiTemplateManager.py tests/testStyleInvariants.py -v
python -m pytest tests/testArchitecturalInvariants.py::testModuleSizeIsBounded -v
python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py
```

### 11. Exercise via generated tests if possible

If you have a running container and a workflow that outputs the new
format, trigger a quantitative test generation and confirm the
inlined script runs inside the container without errors.

## Common failure modes

- **`exec(fsReadLoaderSource())` fails** — you added a symbol outside
  the marker block that the inlined code references, or a top-level
  import not available in the container image.
- **Container test script fails with ImportError** — your loader
  top-level-imports a package that isn't in the container. Move the
  import inside the function and raise a clean error on failure.
- **The format loads but generation proposes no benchmarks for it** —
  step 6 was skipped; the introspection script does not know the
  extension.
- **Access path parsing is inconsistent with existing loaders** —
  study the CSV and HDF5 loaders to match conventions (slash-separated
  paths, colon-separated indexing, etc.).
- **Science-specific fixture committed** — remove it; use a
  hand-crafted minimal file.

## Do not

- Do not add a loader that reads configuration or calls host-only
  utilities: the same text runs in a container and on a host
  project's machine.
- Do not change `fsReadLoaderSource()`'s behavior to include code
  from outside the marker block.
- Do not add an optional reader's package to vaibify's core
  `dependencies` in `pyproject.toml`. It belongs in the `formats` extra,
  beside the readers already there, and in that extra's row of
  `docs/install.md`. The container image installs the project's
  declared Python packages and the analysis stack
  (`T_ANALYSIS_STACK_PACKAGES`), never this extra, so inside a container
  the reader is present only when the project declares it, and the
  loader's `ImportError` says so when it is not.
- Do not commit real scientific data as test fixtures. Vaibify is for
  the general problem; commit synthetic files only.
