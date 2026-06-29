"""Coverage-closing tests for conftestManager surfaced by mutation testing.

Each test targets a specific surviving mutant in
``vaibify/gui/conftestManager.py`` (or in the conftest.py marker
template it ships into containers). The template-internal helpers are
exercised by exec'ing the generated source into an isolated namespace,
the same technique used in ``testConftestManagerMarker.py``.
"""

import types

from vaibify.gui import conftestManager


def _fnExecTemplateWithRoot(tmp_path):
    """Exec the prologue+template with _PROJECT_REPO = tmp_path.

    Returns a module-like namespace containing the template's helper
    functions and hooks so tests can drive them without a live pytest
    session.
    """
    sSource = conftestManager.fsBuildConftestSource(str(tmp_path))
    moduleNs = types.ModuleType("vaibify_conftest_cov_ns")
    moduleNs.__dict__["__name__"] = "vaibify_conftest_cov_ns"
    exec(compile(sSource, "<template>", "exec"), moduleNs.__dict__)
    return moduleNs


class _FakeRep:
    """Stand-in for a pytest call report carrying pass/fail booleans."""

    def __init__(self, bPassed, bFailed):
        self.passed = bPassed
        self.failed = bFailed


class _FakeItem:
    """Stand-in for a collected test item with a nodeid and rep_call."""

    def __init__(self, sNodeId, repCall):
        self.nodeid = sNodeId
        self.rep_call = repCall


class _FakeSession:
    """Minimal pytest session exposing .items and .testscollected."""

    def __init__(self, listItems):
        self.items = listItems
        self.testscollected = len(listItems)


# ----------------------------------------------------------------------
# Hole 1: _fdictBuildCategoryResults must not swap passed/failed tallies.
# ----------------------------------------------------------------------


def test_buildCategoryResults_tallies_pass_and_fail_to_correct_keys(tmp_path):
    """Passed items increment iPassed, failed items increment iFailed.

    Uses asymmetric counts (2 passing, 1 failing) so a swap of the
    tally keys produces a different dict and is detected. A None
    rep_call contributes to neither counter.
    """
    ns = _fnExecTemplateWithRoot(tmp_path)
    listItems = [
        _FakeItem("test_integrity_a.py::test_x", _FakeRep(True, False)),
        _FakeItem("test_integrity_b.py::test_y", _FakeRep(True, False)),
        _FakeItem("test_integrity_c.py::test_z", _FakeRep(False, True)),
        _FakeItem("test_integrity_d.py::test_skipped", None),
    ]
    dictCategories = ns._fdictBuildCategoryResults(_FakeSession(listItems))
    assert dictCategories["integrity"] == {"iPassed": 2, "iFailed": 1}


# ----------------------------------------------------------------------
# Hole 2: sessionfinish marker filename must use '/'->'_' (not '-').
# ----------------------------------------------------------------------


def test_sessionfinish_marker_filename_uses_underscore_for_nested_dir(
    tmp_path, monkeypatch,
):
    """A nested step dir 'a/b' yields marker 'a_b.json' under the slug.

    The host reader looks for the underscore-flattened name; a '-'
    substitution writes a file the reader never finds (dashboard
    desync). Asserting the exact underscore filename and the absence
    of the hyphen variant kills that mutant.
    """
    monkeypatch.setenv("VAIBIFY_ACTIVE_WORKFLOW_SLUG", "demoSlug")
    ns = _fnExecTemplateWithRoot(tmp_path)
    sConftestPath = str(tmp_path / "a" / "b" / "tests" / "conftest.py")
    ns.__dict__["__file__"] = sConftestPath
    ns.pytest_sessionfinish(_FakeSession([]), 0)
    sMarkerDir = tmp_path / ".vaibify" / "test_markers" / "demoSlug"
    assert (sMarkerDir / "a_b.json").is_file()
    assert not (sMarkerDir / "a-b.json").exists()


# ----------------------------------------------------------------------
# Hole 3: _fsActiveWorkflowSlug must fall back to 'default', never ''.
# ----------------------------------------------------------------------


def test_activeWorkflowSlug_falls_back_to_default_when_nothing_present(
    tmp_path, monkeypatch,
):
    """With no env slug and no workflow JSONs, slug is the literal 'default'.

    An empty fallback would write markers to the bare test_markers dir
    the host reader no longer scans, so the result vanishes.
    """
    monkeypatch.delenv("VAIBIFY_ACTIVE_WORKFLOW_SLUG", raising=False)
    ns = _fnExecTemplateWithRoot(tmp_path)
    sSlug = ns._fsActiveWorkflowSlug()
    assert sSlug == "default"
    assert sSlug != ""


# ----------------------------------------------------------------------
# Hole 4: _flistPathsWithinRoot must reject sibling repos sharing a prefix.
# ----------------------------------------------------------------------


def test_pathsWithinRoot_rejects_sibling_with_shared_name_prefix():
    """'/workspace/myrepo-evil/...' is not inside '/workspace/myrepo'.

    A bare startswith(root) check would admit the sibling; the proper
    boundary test (==root or startswith(root + '/')) rejects it.
    """
    listPaths = ["/workspace/myrepo-evil/step/tests/conftest.py"]
    assert conftestManager._flistPathsWithinRoot(
        listPaths, "/workspace/myrepo",
    ) == []


def test_pathsWithinRoot_keeps_in_root_path():
    """A genuinely in-root path survives the containment filter."""
    sInRoot = "/workspace/myrepo/step/tests/conftest.py"
    assert conftestManager._flistPathsWithinRoot(
        [sInRoot], "/workspace/myrepo",
    ) == [sInRoot]
