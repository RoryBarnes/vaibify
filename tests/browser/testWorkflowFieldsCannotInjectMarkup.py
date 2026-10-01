"""Container-writable project data reaches the page as data, never markup.

``project.json`` and ``state.json`` are writable from inside the
container. Three kinds of value from them used to be interpolated into
HTML: a verification state (into a CSS class), numeric settings (into
``value=`` attributes), and the dependency diagram that `dot` produced
inside the container (inlined with ``innerHTML``). The server now types
the fields at load (``tests/testWorkflowFieldTypesAreValidatedAtLoad.py``);
these tests drive the renderers directly with hostile values, so a value
that gets past the server is still inert in the page.
"""

import pytest

from .conftest import fnOpenTheSeededHostWorkflow

pytestmark = pytest.mark.browser

S_HOSTILE = 'passed" data-injected="1" onfocus="window.pwned=1" class="x'

S_HOSTILE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" '
    'width="120pt" height="60pt" viewBox="0 0 120 60">'
    '<script>window.pwned = 1</script>'
    '<g class="edge"><path d="M0 0L50 50" stroke="black" fill="none"/></g>'
    '<foreignObject width="50" height="50">'
    '<div xmlns="http://www.w3.org/1999/xhtml" '
    'onclick="window.pwned = 2">spoof</div></foreignObject>'
    '<a xlink:href="javascript:window.pwned = 3">'
    '<text x="5" y="55">node</text></a></svg>'
)


def _fnLoadTheDashboard(pageDashboard, serverHub):
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=15000)


@pytest.mark.falsification
def testAHostileVerificationStateNeverBecomesAnAttribute(
        pageDashboard, serverHub):
    """Kills: interpolating the raw verification state into the class."""
    _fnLoadTheDashboard(pageDashboard, serverHub)
    dictResult = pageDashboard.evaluate(
        """(sHostile) => {
            const dictBase = {
                fdictGetVerification: () => ({sUser: sHostile,
                    sUnitTest: sHostile, sLastUserUpdate: ""}),
                fsEffectiveTestState: () => sHostile,
                fsComputeDepsState: () => sHostile,
                flistGetStepDependencies: () => [1],
                fsVerificationStateLabel: () => "Untested",
                fsVerificationStateIcon: () => "-",
                setGeneratingInFlight: new Set(),
                setExpandedUnitTests: new Set(),
                setExpandedDeps: new Set(),
                dictMarkerMtimeByStep: {},
                sUserName: "researcher",
            };
            const dictContext = new Proxy(dictBase, {
                get: (t, k) => (k in t) ? t[k]
                    : (typeof k === "string" && k.startsWith("set"))
                        ? new Set() : () => "",
            });
            const step = {sName: "s", saDataCommands: ["x"],
                saPlotCommands: [], bInteractive: false};
            const sHtml = VaibifyStepRenderer.fsRenderVerificationBlock(
                step, 0, dictContext);
            const elHost = document.createElement("div");
            elHost.innerHTML = sHtml;
            return {
                iInjected: elHost.querySelectorAll(
                    "[data-injected],[onfocus]").length,
                listClasses: Array.from(elHost.querySelectorAll(
                    ".verification-badge")).map(el => el.className),
                iBadges: elHost.querySelectorAll(
                    ".verification-badge").length,
            };
        }""",
        S_HOSTILE)
    assert dictResult["iBadges"] >= 2
    assert dictResult["iInjected"] == 0
    assert set(dictResult["listClasses"]) == {
        "verification-badge state-untested"}


@pytest.mark.falsification
def testAHostileRuntimeLimitCannotLeaveTheValueAttribute(
        pageDashboard, serverHub):
    """Kills: interpolating the raw budget into the input's value."""
    _fnLoadTheDashboard(pageDashboard, serverHub)
    dictResult = pageDashboard.evaluate(
        """(sHostile) => {
            VaibifyModals.fnShowRuntimeLimitModal({
                sStepTitle: "t", fCurrentBudget: sHostile,
                fProjectDefault: 0, fnOnSave: () => {}});
            const elInput = document.getElementById("runtimeLimitInput");
            return {
                listAttributes: Array.from(elInput.attributes)
                    .map(a => a.name).sort(),
                sValue: elInput.getAttribute("value"),
            };
        }""",
        S_HOSTILE)
    assert "onfocus" not in dictResult["listAttributes"]
    assert "data-injected" not in dictResult["listAttributes"]
    assert dictResult["sValue"] == ""


def testFiniteNumberTextAdmitsOnlyNumbers(pageDashboard, serverHub):
    _fnLoadTheDashboard(pageDashboard, serverHub)
    listAnswers = pageDashboard.evaluate(
        """(sHostile) => [
            VaibifyUtilities.fsFiniteNumberText(8, "-1"),
            VaibifyUtilities.fsFiniteNumberText("2.5", "-1"),
            VaibifyUtilities.fsFiniteNumberText(sHostile, "-1"),
            VaibifyUtilities.fsFiniteNumberText(null, "-1"),
            VaibifyUtilities.fsFiniteNumberText(true, "-1"),
            VaibifyUtilities.fsFiniteNumberText(Infinity, "-1"),
        ]""",
        S_HOSTILE)
    assert listAnswers == ["8", "2.5", "-1", "-1", "-1", "-1"]


@pytest.mark.falsification
def testTheContainerProducedDiagramIsAnImageNotInlineMarkup(
        pageDashboard, serverHub):
    """Kills: inlining the diagram with innerHTML again."""
    pageDashboard.route(
        "**/api/workflow/*/dag",
        lambda route: route.fulfill(
            status=200, content_type="image/svg+xml", body=S_HOSTILE_SVG))
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate("() => VaibifyApp.fnShowDag()")
    pageDashboard.wait_for_selector(".dag-container img.dag-image",
                                    timeout=15000)
    dictResult = pageDashboard.evaluate(
        """() => ({
            iInline: document.querySelectorAll(
                '.dag-container svg, .dag-container script, '
                + '.dag-container foreignObject, .dag-container a').length,
            sSource: document.querySelector('.dag-container img')
                .getAttribute('src'),
            vPwned: window.pwned === undefined ? null : window.pwned,
        })""")
    assert dictResult["iInline"] == 0
    assert dictResult["sSource"].startswith("blob:")
    assert dictResult["vPwned"] is None
    assert pageDashboard.listPageErrors == []


def testAnUnreadableDiagramIsReportedNotRenderedBlank(
        pageDashboard, serverHub):
    pageDashboard.route(
        "**/api/workflow/*/dag",
        lambda route: route.fulfill(
            status=200, content_type="image/svg+xml",
            body="<svg><unclosed></svg"))
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate("() => VaibifyApp.fnShowDag()")
    pageDashboard.wait_for_selector(".toast.error", timeout=15000)
    assert "could not be read" in pageDashboard.inner_text(
        ".toast.error")
    assert pageDashboard.locator(".dag-container img").count() == 0
