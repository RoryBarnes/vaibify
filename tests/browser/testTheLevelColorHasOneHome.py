"""Every PROOF-level accent follows the level, and nothing else does.

The level color used to have several homes. Each ``body.proof-level-N``
set ``--highlight-color``, but a ``.toolbar-label`` override, the
checkmark filter, the terminal cursor, the panel glow and the birth
outline all hard-coded the Level 1 purple, and a long tail of pale-blue
accents never followed any level. At Level 2 and Level 3 the dashboard
therefore wore two or three themes at once (2026-10-28). The colors now
have one home -- ``--proof-level-N-color`` on ``:root`` -- and this
census checks the page by VISUAL ROLE at L0 through L3:

  - theme accents compute to the current level's color;
  - the Level 3 card title and verify button keep the Level 3 color at
    every level, because they name that level;
  - status colors keep their meaning, and each is on an allow-list
    with its reason;
  - a sweep of every visible element finds no other level's color, and
    above Level 0 no pale-blue accent.

Computed CSS cannot see what a filter does to an image, nor what
xterm paints, so ``testTheLevelMarksRenderTheLevelsHue.py`` checks the
filtered marks and the terminal cursor by their pixels.

The level is set through the app's own path,
``VaibifyApp.fnUpdateHighlightState``, with the page clock paused so no
poll can re-render between the setting and the measurement.

Kills (confirmed): restoring the ``.toolbar-label`` override -> the
accent census fails at Level 2 with the label still purple.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.testResizeAndCopyHoldTogether import (
    F_SHELL_DEADLINE_SECONDS,
    _fnWaitForPaneText,
)


pytestmark = pytest.mark.browser

DICT_LEVEL_COLORS = {
    0: (19, 174, 213),
    1: (179, 157, 219),
    2: (139, 200, 163),
    3: (244, 143, 177),
}
T_PALE_BLUE = DICT_LEVEL_COLORS[0]

# Probes are real dashboard markup for accents that the seeded page
# does not happen to render. Each is matched by the stylesheet exactly
# as the real element is; they live in a hidden host, because computed
# style is defined for undisplayed elements.
S_PROBE_MARKUP = """
<div class="project-block"><a id="probeProjectLink" href="#">x</a></div>
<a id="probeDiagnosisLink" class="diagnosis-link">x</a>
<div class="requirement-row expanded">
  <div id="probeExpandedRow" class="requirement-row-header">x</div></div>
<div id="probeSelectedStep" class="step-item selected">x</div>
<details class="confirm-details"><summary id="probeConfirmSummary">x</summary></details>
<span id="probeSyncTarget" class="sync-target-value">x</span>
<button id="probeFreshness" class="freshness-refresh-button">x</button>
<div class="proof-req-how"><span id="probeGapFix" class="proof-gap-fix">x</span></div>
<table class="env-info-table"><tr class="env-info-section">
  <th id="probeEnvSection">x</th></tr></table>
<div id="probeLocationCard" class="wizard-location-card-selected">x</div>
<div id="probeAgentBanner" class="wizard-agent-banner">x</div>
<div class="proof-card proof-level3-card">
  <div id="probeLevelThreeTitle" class="proof-card-title">x</div></div>
<button id="probeLevelThreeVerify" class="btn-proof-verify-l3">x</button>
<div class="step-item"><span id="probeRunLight" class="step-status pass"></span></div>
<span id="probeSyncedBadge" class="remote-badge badge-synced">x</span>
"""

# (description, selector, CSS property) -- each must equal the level.
LIST_THEME_ACCENTS = [
    ("toolbar label", ".toolbar-label", "color"),
    ("step name", ".step-item .step-name", "color"),
    ("modal heading", ".modal h2", "color"),
    ("selected step row", "#probeSelectedStep", "border-left-color"),
    ("selected step fill", "#probeSelectedStep", "background-color"),
    ("expanded requirement row", "#probeExpandedRow", "background-color"),
    ("Project-block link", "#probeProjectLink", "color"),
    ("diagnosis link", "#probeDiagnosisLink", "color"),
    ("confirm details", "#probeConfirmSummary", "color"),
    ("sync target", "#probeSyncTarget", "color"),
    ("freshness refresh", "#probeFreshness", "color"),
    ("PROOF gap fix", "#probeGapFix", "color"),
    ("environment info section", "#probeEnvSection", "color"),
    ("selected location card", "#probeLocationCard", "border-top-color"),
    ("wizard agent banner", "#probeAgentBanner", "border-left-color"),
]

LIST_FIXED_LEVEL_THREE = [
    ("Level 3 card title", "#probeLevelThreeTitle", "color"),
    ("Level 3 verify button", "#probeLevelThreeVerify", "background-color"),
]

# Status colors keep their meaning whatever the level. Each entry is a
# selector the sweep exempts, with the reason it is not an accent.
DICT_STATUS_ALLOW_LIST = {
    ".step-status.pass": "the run light's quiet pale-blue success dot "
                         "(2026-07-17 ruling); an execution fact, not a level",
    ".remote-badge.badge-synced": "a remote's sync status",
    ".push-file-status-badge.badge-new": "a file's push status: new",
    ".diff-group-new": "the same 'new file' status as the push badge",
    ".containment-chip--containerizing": "a containment status chip",
    ".council-chip-verifiedStopped": "an Agent Council lifecycle status",
    ".case-collision-banner": "an informational sync notice",
    ".sync-consistency-banner": "an informational sync notice",
    ".btn-ai-declaration-commit": "the routine commit action, pinned "
                                  "pale blue by its own contract test",
    ".xterm": "terminal content is the program's ANSI palette",
}
LIST_FIXED_SELECTORS = [".proof-level3-card", ".btn-proof-verify-l3"]

LIST_STATUS_PROBES = [
    ("run light pass dot", "#probeRunLight", "background-color"),
    ("synced remote badge", "#probeSyncedBadge", "color"),
]

S_INSTALL_PROBES = """(sMarkup) => {
    const elStyle = document.createElement('style');
    elStyle.textContent = '*, *::before, *::after {' +
        'transition: none !important; animation: none !important; }';
    document.head.appendChild(elStyle);
    const elHost = document.createElement('div');
    elHost.id = 'levelColorProbes';
    elHost.style.display = 'none';
    elHost.innerHTML = sMarkup;
    document.body.appendChild(elHost);
    const elMarks = document.createElement('div');
    elMarks.id = 'levelColorMarks';
    elMarks.style.cssText = 'position: fixed; right: 8px; bottom: 8px;' +
        'padding: 6px; z-index: 99999; background: var(--bg-panel);';
    elMarks.innerHTML =
        '<img id="probeFavicon" class="level-cell-favicon" ' +
        'src="/static/favicon.png" style="width:40px;height:40px">' +
        '<img id="probeBadge" class="vaib-verified-badge" ' +
        'src="/static/favicon.png" style="width:40px;height:40px">';
    document.body.appendChild(elMarks);
}"""

S_SET_LEVEL_THROUGH_THE_APP = """(iLevel) => {
    const dictWorkflow = VaibifyApp.fdictGetWorkflow();
    const listSavedSteps = dictWorkflow.listSteps;
    const iSavedLevel = dictWorkflow.iProofLevel;
    /* A declaration step passes the client gate by the 2026-07-02
       ruling, so this one-step list lets the server's level stand. */
    dictWorkflow.listSteps = [{sStepKind: 'ai-declaration'}];
    dictWorkflow.iProofLevel = iLevel;
    VaibifyApp.fnUpdateHighlightState();
    dictWorkflow.listSteps = listSavedSteps;
    dictWorkflow.iProofLevel = iSavedLevel;
    document.querySelectorAll(
        '.vaibify-bloom-overlay, .vaibify-project-birth-outline'
    ).forEach((el) => el.remove());
    return Array.from(document.body.classList)
        .filter((s) => s.startsWith('proof-level-'));
}"""

S_MEASURE = """([listAccents, listFixed, listStatus, listExempt]) => {
    function fdictRead(listEntries) {
        return listEntries.map(([sWhat, sSelector, sProperty]) => {
            const el = document.querySelector(sSelector);
            return [sWhat, el ? getComputedStyle(el)[sProperty] : null];
        });
    }
    function flistRgb(sValue) {
        const match = /rgba?\\(([^)]+)\\)/.exec(sValue || '');
        if (!match) return null;
        const listParts = match[1].split(/[ ,\\/]+/).filter(Boolean);
        const fAlpha = listParts.length > 3 ? parseFloat(listParts[3]) : 1;
        if (fAlpha === 0) return null;
        return listParts.slice(0, 3).map((s) => Math.round(parseFloat(s)));
    }
    function fbVisible(el) {
        if (el.closest('#levelColorProbes, #levelColorMarks')) return false;
        if (el.getClientRects().length === 0) return false;
        const style = getComputedStyle(el);
        return style.visibility !== 'hidden' && style.opacity !== '0';
    }
    function fbHasOwnText(el) {
        return Array.from(el.childNodes).some((node) =>
            node.nodeType === 3 && node.textContent.trim() !== '');
    }
    const listSweep = [];
    document.querySelectorAll('body *').forEach((el) => {
        if (!fbVisible(el)) return;
        if (listExempt.some((s) => el.closest(s))) return;
        const style = getComputedStyle(el);
        const listCandidates = [];
        if (fbHasOwnText(el)) listCandidates.push(['color', style.color]);
        listCandidates.push(['background-color', style.backgroundColor]);
        for (const sSide of ['top', 'right', 'bottom', 'left']) {
            if (parseFloat(style['border-' + sSide + '-width']) > 0 &&
                style['border-' + sSide + '-style'] !== 'none') {
                listCandidates.push(['border-' + sSide + '-color',
                    style['border-' + sSide + '-color']]);
            }
        }
        if (style.outlineStyle !== 'none' &&
            parseFloat(style.outlineWidth) > 0) {
            listCandidates.push(['outline-color', style.outlineColor]);
        }
        if (el instanceof SVGElement) {
            listCandidates.push(['fill', style.fill]);
            listCandidates.push(['stroke', style.stroke]);
        }
        for (const [sProperty, sValue] of listCandidates) {
            const listRgb = flistRgb(sValue);
            if (!listRgb) continue;
            const sClass = typeof el.className === 'string' ?
                el.className : '';
            listSweep.push([el.tagName.toLowerCase() +
                (el.id ? '#' + el.id : '') +
                (sClass ? '.' + sClass.trim().split(/\\s+/).join('.') : ''),
                sProperty, listRgb]);
        }
    });
    return {listAccents: fdictRead(listAccents),
            listFixed: fdictRead(listFixed),
            listStatus: fdictRead(listStatus), listSweep: listSweep};
}"""


def ftParseRgb(sValue):
    """Return the (r, g, b) of a computed color, ignoring its alpha."""
    assert sValue and "(" in sValue, f"no color computed: {sValue!r}"
    listParts = sValue[sValue.index("(") + 1:sValue.index(")")]
    listParts = listParts.replace("/", " ").replace(",", " ").split()
    return tuple(round(float(s)) for s in listParts[:3])


def _fnAssertTheCensus(dictMeasured, iLevel):
    tLevel = DICT_LEVEL_COLORS[iLevel]
    for sWhat, sValue in dictMeasured["listAccents"]:
        assert ftParseRgb(sValue) == tLevel, (
            f"at Level {iLevel} the {sWhat} computes to {sValue}, not "
            f"the level color rgb{tLevel}"
        )
    for sWhat, sValue in dictMeasured["listFixed"]:
        assert ftParseRgb(sValue) == DICT_LEVEL_COLORS[3], (
            f"the {sWhat} names Level 3 and must keep its color at "
            f"Level {iLevel}; it computes to {sValue}"
        )
    for sWhat, sValue in dictMeasured["listStatus"]:
        assert ftParseRgb(sValue) == T_PALE_BLUE, (
            f"the {sWhat} is a status color and must keep its meaning "
            f"at Level {iLevel}; it computes to {sValue}"
        )
    setForbidden = {t for i, t in DICT_LEVEL_COLORS.items() if i != iLevel}
    listOffenders = [
        (sElement, sProperty, tuple(listRgb))
        for sElement, sProperty, listRgb in dictMeasured["listSweep"]
        if tuple(listRgb) in setForbidden
    ]
    assert listOffenders == [], (
        f"at Level {iLevel} these visible elements wear another level's "
        f"color or an unclassified pale-blue accent: {listOffenders}"
    )


S_RECORD_TERMINALS = """
(() => {
    window.__listTerminalsForTest = [];
    Object.defineProperty(window, 'Terminal', {
        configurable: true,
        set(classTerminal) {
            class RecordedTerminal extends classTerminal {
                constructor(...listArguments) {
                    super(...listArguments);
                    window.__listTerminalsForTest.push(this);
                }
            }
            Object.defineProperty(window, 'Terminal', {
                value: RecordedTerminal, writable: true, configurable: true,
            });
        },
        get() { return undefined; },
    });
})();
"""


def fnOpenAndFreezeTheDashboard(pageDashboard, serverHub, bWithShell=False):
    """Open the seeded project, optionally dial its shell, then pause."""
    pageDashboard.add_init_script(S_RECORD_TERMINALS)
    pageDashboard.clock.install()
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    if bWithShell:
        pageDashboard.wait_for_selector(".xterm", timeout=20000)
        pageDashboard.click(".xterm")
        _fnWaitForPaneText(
            pageDashboard, "YOUR OWN machine", "the host shell banner",
            F_SHELL_DEADLINE_SECONDS,
        )
    pageDashboard.evaluate(S_INSTALL_PROBES, S_PROBE_MARKUP)
    pageDashboard.clock.pause_at(pageDashboard.evaluate("Date.now()") + 1000)


def fnSetTheLevel(pageDashboard, iLevel):
    listClasses = pageDashboard.evaluate(S_SET_LEVEL_THROUGH_THE_APP, iLevel)
    assert listClasses == ([f"proof-level-{iLevel}"] if iLevel else [])


@pytest.mark.falsification
def testEveryLevelAccentFollowsTheLevelAndNothingElseDoes(
    pageDashboard, serverHub,
):
    """Kills: restoring the .toolbar-label override to the L1 purple."""
    fnOpenAndFreezeTheDashboard(pageDashboard, serverHub)
    listExempt = list(DICT_STATUS_ALLOW_LIST) + LIST_FIXED_SELECTORS
    for iLevel in (0, 1, 2, 3):
        fnSetTheLevel(pageDashboard, iLevel)
        dictMeasured = pageDashboard.evaluate(S_MEASURE, [
            LIST_THEME_ACCENTS, LIST_FIXED_LEVEL_THREE,
            LIST_STATUS_PROBES, listExempt,
        ])
        _fnAssertTheCensus(dictMeasured, iLevel)
    pageDashboard.clock.resume()
    assert pageDashboard.listPageErrors == []
