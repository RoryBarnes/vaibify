---
name: add-data-loader
description: Recipe for adding a new file-format loader to vaibify/gui/dataLoaders.py. Use when the task is to support reading a new data file extension (e.g., netCDF, Zarr, a domain-specific binary format) in quantitative benchmark tests.
---

# Adding a new data-file loader to vaibify

Use this recipe when the task is to support reading a new data file
extension in vaibify's quantitative benchmark tests.

`vaibify/gui/dataLoaders.py` is the dispatch table mapping file
extensions to loader functions. It is also the source that gets
embedded verbatim into container-side introspection scripts via
`fsReadLoaderSource()`. This embedding is the primary constraint on
every loader you add.

## Prerequisites

Read these first:

- `AGENTS.md` (repo root) — global rules and traps
- `vaibify/gui/dataLoaders.py` — the existing loaders and the
  `# -- begin loader source` marker block
- `vaibify/gui/introspectionScript.py` — the f-string that embeds the
  loader source and runs inside containers
- `vaibify/gui/testGenerator.py` — the orchestrator that invokes
  `fsReadLoaderSource()`

## The critical invariant

The text between the `# -- begin loader source` and
`# -- end loader source` markers in `dataLoaders.py` is read as a
string by `fsReadLoaderSource()`, then inlined into a self-contained
Python script that runs **inside a container**. That container has a
known Python version and a limited set of packages available. This
means:

- Loaders must be **pure Python** plus the packages already used in
  the loader-source block (`json`, `pathlib`, `re`, `numpy`). Do not
  introduce new top-level imports into the loader-source block
  without confirming the dependency is always present in the
  container images vaibify supports.
- If a format requires an optional package (e.g., `netCDF4`, `zarr`,
  `pyarrow`), the loader must `import` inside the function body and
  raise a clear error if the import fails. Do not place the import at
  module top level inside the loader-source block.
- Loaders must not read configuration from the host filesystem, call
  host-only paths, or use `os.path` (container paths use `posixpath`).
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

### 2. Implement the loader function

Also inside the loader-source block, define
`_fLoad<FormatName>(sDataFile, sAccessPath)` following the shape of
the existing loaders. The function returns a single scalar value
given a data file path and an access path (which encodes which array,
column, or key to extract — the syntax is format-specific and follows
patterns set by existing loaders).

Conventions:

- Function name: `_fLoad<FormatName>` for scalar loaders.
- Optional dependencies: import inside the function body and raise a
  clear `ImportError` with a helpful message if missing.
- Input validation: raise `ValueError` with a specific message on bad
  access paths; raise `FileNotFoundError` for missing files.
- Return type: a Python scalar (float, int, str, bool) — not an
  array.

### 3. Register the loader in `DICT_LOADERS`

Still inside the loader-source block, add the mapping:

```python
DICT_LOADERS = {
    ...
    "netcdf": _fLoadNetcdf,
    ...
}
```

### 4. Confirm public symbols are unchanged

The module-level `__all__` should still be
`["DICT_FORMAT_MAP", "DICT_LOADERS", "fLoadValue", "fsReadLoaderSource"]`.
Do not add the new loader function to `__all__` — loaders are
internal to the dispatch.

### 5. Add a test

Create `tests/testDataLoader<FormatName>.py`. At minimum:

- Confirm the extension is in `_DICT_FORMAT_MAP`.
- Confirm the format string is in `DICT_LOADERS`.
- Call `fLoadValue` against a small fixture file and assert the
  returned scalar matches the expected value.
- If the format requires an optional dependency, add a test that
  skips cleanly when the dependency is absent.

Keep fixture files small and science-agnostic. Do not commit real
scientific datasets. A test fixture should be a hand-crafted
minimal file that exercises the format's structure.

### 6. Verify the loader source still parses

`fsReadLoaderSource()` returns the text between the markers as a
string. That string must be valid Python on its own — it gets
compiled and exec'd inside the container. Run:

```bash
python -c "from vaibify.gui.dataLoaders import fsReadLoaderSource; exec(fsReadLoaderSource())"
```

If this fails, something in your edit broke the embedded source.
Common causes: using a name defined outside the block; an import
that's not available in the embedded scope; a string containing
triple quotes that confuse the outer f-string in
`introspectionScript.py`.

### 7. Run the test suite

```bash
python -m pytest tests/testDataLoader<FormatName>.py -v
python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py
```

### 8. Exercise via generated tests if possible

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
- **Access path parsing is inconsistent with existing loaders** —
  study the CSV and HDF5 loaders to match conventions (slash-separated
  paths, colon-separated indexing, etc.).
- **Science-specific fixture committed** — remove it; use a
  hand-crafted minimal file.

## Do not

- Do not add a loader that reads host paths or calls host-only
  utilities. Loaders run in the container.
- Do not change `fsReadLoaderSource()`'s behavior to include code
  from outside the marker block.
- Do not add optional dependencies to vaibify's top-level
  `pyproject.toml` — they belong in the container image if they
  belong anywhere.
- Do not commit real scientific data as test fixtures. Vaibify is for
  the general problem; commit synthetic files only.
