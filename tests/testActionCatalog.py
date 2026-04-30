"""Behavioral tests for vaibify.gui.actionCatalog.

The architectural invariants in ``testArchitecturalInvariants.py``
cover catalog-vs-router registration. This file exercises the three
exported helpers (``fnAgentAction``, ``fdictLookupAction``,
``fdictBuildCatalogJson``) plus the sPath placeholder hygiene that
``vaibify-do`` depends on.
"""

import re

from vaibify.gui import actionCatalog


# -----------------------------------------------------------------------
# fnAgentAction decorator
# -----------------------------------------------------------------------


def test_fnAgentAction_attaches_name_attribute():
    @actionCatalog.fnAgentAction("my-action")
    def fnHandler():
        return 42

    assert getattr(fnHandler, "_sAgentActionName") == "my-action"


def test_fnAgentAction_returns_original_function():
    """Decorator must not wrap — vaibify-do relies on introspection."""
    def fnOriginal():
        return "sentinel"

    fnDecorated = actionCatalog.fnAgentAction("x")(fnOriginal)
    assert fnDecorated is fnOriginal
    assert fnDecorated() == "sentinel"


def test_fnAgentAction_preserves_call_behavior():
    @actionCatalog.fnAgentAction("add")
    def fnAdd(a, b):
        return a + b

    assert fnAdd(2, 3) == 5


# -----------------------------------------------------------------------
# fdictLookupAction
# -----------------------------------------------------------------------


def test_fdictLookupAction_finds_known_action():
    dictEntry = actionCatalog.fdictLookupAction("run-all")
    assert dictEntry is not None
    assert dictEntry["sName"] == "run-all"


def test_fdictLookupAction_unknown_returns_none():
    assert actionCatalog.fdictLookupAction("not-a-real-action") is None


def test_fdictLookupAction_empty_string_returns_none():
    assert actionCatalog.fdictLookupAction("") is None


# -----------------------------------------------------------------------
# fdictBuildCatalogJson
# -----------------------------------------------------------------------


def test_fdictBuildCatalogJson_has_schema_version_and_actions():
    dictResult = actionCatalog.fdictBuildCatalogJson()
    assert dictResult["sSchemaVersion"] == (
        actionCatalog.S_CATALOG_SCHEMA_VERSION
    )
    assert dictResult["listActions"] == list(
        actionCatalog.LIST_AGENT_ACTIONS
    )


def test_fdictBuildCatalogJson_actions_is_a_copy():
    """Mutating the result's list must not alter the module constant."""
    dictResult = actionCatalog.fdictBuildCatalogJson()
    iBefore = len(actionCatalog.LIST_AGENT_ACTIONS)
    dictResult["listActions"].append({"sName": "injected"})
    assert len(actionCatalog.LIST_AGENT_ACTIONS) == iBefore


def test_fdictBuildCatalogJson_entries_are_copies():
    """Mutating an entry in the result must not alter the module constant."""
    dictResult = actionCatalog.fdictBuildCatalogJson()
    sOriginalName = actionCatalog.LIST_AGENT_ACTIONS[0]["sName"]
    dictResult["listActions"][0]["sName"] = "tampered"
    assert actionCatalog.LIST_AGENT_ACTIONS[0]["sName"] == sOriginalName


# -----------------------------------------------------------------------
# Path-placeholder hygiene
# -----------------------------------------------------------------------


_PATTERN_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def test_catalog_placeholders_have_balanced_braces():
    """Every sPath's braces come in matched pairs with no stray braces."""
    for dictEntry in actionCatalog.LIST_AGENT_ACTIONS:
        sPath = dictEntry["sPath"]
        # Count open/close; must match.
        assert sPath.count("{") == sPath.count("}"), (
            f"unbalanced braces in {dictEntry['sName']}: {sPath}"
        )


def test_catalog_placeholders_are_well_formed_names():
    """Placeholder bodies are [name] or [name:converter]."""
    sValidNameRe = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    for dictEntry in actionCatalog.LIST_AGENT_ACTIONS:
        sPath = dictEntry["sPath"]
        for sBody in _PATTERN_PLACEHOLDER.findall(sPath):
            listParts = sBody.split(":")
            assert len(listParts) <= 2, (
                f"{dictEntry['sName']}: too many colons in {sBody}"
            )
            sName = listParts[0]
            assert sValidNameRe.match(sName), (
                f"{dictEntry['sName']}: bad placeholder name {sName!r}"
            )


def test_catalog_ws_paths_have_no_placeholders():
    """WebSocket sPath values are action tokens, not URL templates."""
    for dictEntry in actionCatalog.LIST_AGENT_ACTIONS:
        if dictEntry["sMethod"] == "WS":
            assert "{" not in dictEntry["sPath"], (
                f"WS action {dictEntry['sName']} should have no "
                f"placeholder: {dictEntry['sPath']}"
            )


def test_catalog_http_paths_start_with_api():
    for dictEntry in actionCatalog.LIST_AGENT_ACTIONS:
        if dictEntry["sMethod"] != "WS":
            assert dictEntry["sPath"].startswith("/api/"), (
                f"{dictEntry['sName']}: HTTP sPath must start with /api/"
            )


# -----------------------------------------------------------------------
# Shared constants
# -----------------------------------------------------------------------


def test_shared_constants_are_stable_strings():
    assert actionCatalog.S_SESSION_ENV_PATH == "/tmp/vaibify-session.env"
    assert actionCatalog.S_CATALOG_JSON_PATH == (
        "/tmp/vaibify-action-catalog.json"
    )
    assert actionCatalog.S_SESSION_HEADER_NAME == "X-Vaibify-Session"
    assert actionCatalog.S_CATALOG_SCHEMA_VERSION == "1.0"
