/* Vaibify — Multi-pane terminal management with xterm.js */

const VaibifyTerminal = (function () {
    "use strict";

    var I_MAX_PANES = 5;
    var iTabCounter = 0;

    /* Scrollback (retained lines) is a client display preference, not
       workflow data: the default is generous, the floor protects
       against a useless terminal, and "unlimited" is capped at a value
       that is effectively unbounded for any real session while keeping
       xterm.js's pre-allocated line buffer from exhausting browser
       memory. Persisted in localStorage; applied live to open tabs. */
    var I_DEFAULT_SCROLLBACK = 10000;
    var I_MINIMUM_SCROLLBACK = 100;
    var I_UNLIMITED_SCROLLBACK = 1000000;
    var S_SCROLLBACK_STORAGE_KEY = "vaibifyTerminalScrollback";
    var iCurrentScrollback = I_DEFAULT_SCROLLBACK;

    /*
     * listPanes: array of pane objects, each with:
     *   iId, listTabs, iActiveTabIndex, elPane
     */
    var listPanes = [];

    /* --- Terminal Theme --- */

    var sCurrentCursorColor = "#13aed5";

    var DICT_TERMINAL_THEME = {
        background: "#0d0d1a",
        foreground: "#e0e0e8",
        cursor: "#13aed5",
        selectionBackground: "rgba(19, 174, 213, 0.3)",
        black: "#1e1e2e",
        /* Text-grade on the terminal background (4.9:1) — programs
           print error prose in ANSI red, so it must read as text,
           not just signal. Matches the app palette --color-red. */
        red: "#e5484d",
        green: "#2ecc71",
        yellow: "#e09401",
        blue: "#1321d8",
        magenta: "#9b59b6",
        cyan: "#13aed5",
        white: "#e0e0e8",
        brightBlack: "#6a6a88",
        brightRed: "#e04040",
        brightGreen: "#4edc91",
        brightYellow: "#f0b030",
        brightBlue: "#4050f0",
        brightMagenta: "#8040c0",
        brightCyan: "#40c8e8",
        brightWhite: "#ffffff",
    };

    /* --- Pane Management --- */

    function fnCreatePane() {
        if (listPanes.length >= I_MAX_PANES) return;

        var elStrip = document.getElementById("terminalStrip");
        var iPaneId = listPanes.length;

        /* Insert resize handle if not first pane */
        if (listPanes.length > 0) {
            var elHandle = document.createElement("div");
            elHandle.className = "resize-handle-terminal";
            elHandle.dataset.pane = String(iPaneId);
            elStrip.appendChild(elHandle);
            fnBindTerminalResizeHandle(elHandle, iPaneId);
        }

        var elPane = document.createElement("div");
        elPane.className = "terminal-pane";
        elPane.id = "terminalPane" + iPaneId;
        elPane.innerHTML =
            '<div class="terminal-pane-tabs">' +
            '<button class="terminal-pane-add" data-pane="' +
            iPaneId + '" title="New tab">+</button>' +
            '<button class="terminal-pane-copy" data-pane="' +
            iPaneId + '" title="Copy this terminal\'s full scrollback ' +
            'to the clipboard">Copy all</button>' +
            '</div>' +
            '<div class="terminal-pane-container"></div>';
        elStrip.appendChild(elPane);

        var dictPane = {
            iId: iPaneId,
            listTabs: [],
            iActiveTabIndex: -1,
            elPane: elPane,
        };
        listPanes.push(dictPane);

        fnBindPaneToolbarButtons(dictPane, iPaneId);

        fnCreateTab(iPaneId);
        fnUpdateAddPaneButton();
    }

    function fnRemovePane(iPaneId) {
        var dictPane = listPanes[iPaneId];
        if (!dictPane) return;

        /* Close all tabs in this pane without confirmation */
        while (dictPane.listTabs.length > 0) {
            fnForceCloseTabInPane(iPaneId, 0);
        }

        /* Remove DOM elements */
        var elStrip = document.getElementById("terminalStrip");
        dictPane.elPane.remove();

        /* Remove preceding resize handle */
        var elHandle = elStrip.querySelector(
            '.resize-handle-terminal[data-pane="' + iPaneId + '"]'
        );
        if (elHandle) elHandle.remove();

        listPanes.splice(iPaneId, 1);

        /* Reindex remaining panes */
        fnReindexPanes();
        fnUpdateAddPaneButton();
    }

    function fnReindexPanes() {
        var elStrip = document.getElementById("terminalStrip");
        elStrip.innerHTML = "";

        listPanes.forEach(function (dictPane, iNewId) {
            dictPane.iId = iNewId;
            dictPane.elPane.id = "terminalPane" + iNewId;

            if (iNewId > 0) {
                var elHandle = document.createElement("div");
                elHandle.className = "resize-handle-terminal";
                elHandle.dataset.pane = String(iNewId);
                elStrip.appendChild(elHandle);
                fnBindTerminalResizeHandle(elHandle, iNewId);
            }
            elStrip.appendChild(dictPane.elPane);
            fnBindPaneToolbarButtons(dictPane, iNewId);
        });
    }

    /* The pane's own buttons are rebound on every reindex, because the
       pane id they act on is its position in listPanes and that moves
       when a pane is removed. */
    function fnBindPaneToolbarButtons(dictPane, iPaneId) {
        var elAdd = dictPane.elPane.querySelector(".terminal-pane-add");
        if (elAdd) {
            elAdd.dataset.pane = String(iPaneId);
            elAdd.onclick = function () {
                fnCreateTab(iPaneId);
            };
        }
        var elCopy = dictPane.elPane.querySelector(".terminal-pane-copy");
        if (elCopy) {
            elCopy.dataset.pane = String(iPaneId);
            elCopy.onclick = function () {
                fnCopyPaneScrollback(iPaneId);
            };
        }
    }

    function fnUpdateAddPaneButton() {
        var elBtn = document.getElementById("btnAddTerminalPane");
        if (elBtn) {
            elBtn.disabled = listPanes.length >= I_MAX_PANES;
        }
    }

    /* --- Tab Management --- */

    function fnCreateTab(iPaneId) {
        if (iPaneId === undefined) iPaneId = 0;
        var dictPane = listPanes[iPaneId];
        if (!dictPane) return;

        var sContainerId = VaibifyApp.fsGetContainerId();
        if (!sContainerId) return;

        iTabCounter++;
        var dictTab = {
            iId: iTabCounter,
            sLabel: "Term " + iTabCounter,
            terminal: null,
            fitAddon: null,
            websocket: null,
            resizeObserver: null,
            bFitDeferred: false,
            iRefitTimer: null,
            iCopyOnSelectTimer: null,
        };
        dictPane.listTabs.push(dictTab);
        fnRenderPaneTabs(iPaneId);
        fnActivateTabInPane(iPaneId, dictPane.listTabs.length - 1);
    }

    function fnRenderPaneTabs(iPaneId) {
        var dictPane = listPanes[iPaneId];
        if (!dictPane) return;

        var elTabBar = dictPane.elPane.querySelector(".terminal-pane-tabs");
        var elAdd = elTabBar.querySelector(".terminal-pane-add");

        /* Remove old tab elements (keep add button) */
        var listExisting = elTabBar.querySelectorAll(".terminal-tab");
        listExisting.forEach(function (el) { el.remove(); });

        dictPane.listTabs.forEach(function (dictTab, iTabIndex) {
            var elTab = document.createElement("div");
            elTab.className = "terminal-tab" +
                (iTabIndex === dictPane.iActiveTabIndex ? " active" : "");
            elTab.innerHTML =
                "<span>" + dictTab.sLabel + "</span>" +
                '<span class="close-tab">&times;</span>';
            var iCapturedPane = iPaneId;
            var iCapturedTab = iTabIndex;
            elTab.querySelector(".close-tab").addEventListener(
                "click", function (event) {
                    event.preventDefault();
                    event.stopPropagation();
                    fnCloseTabInPane(iCapturedPane, iCapturedTab);
                }
            );
            elTab.addEventListener("click", function (event) {
                if (!event.target.closest(".close-tab")) {
                    fnActivateTabInPane(iCapturedPane, iCapturedTab);
                }
            });
            elTabBar.insertBefore(elTab, elAdd);
        });
    }

    function fnActivateTabInPane(iPaneId, iTabIndex) {
        var dictPane = listPanes[iPaneId];
        if (!dictPane) return;
        if (iTabIndex < 0 || iTabIndex >= dictPane.listTabs.length) return;

        /* Hide current terminal */
        if (dictPane.iActiveTabIndex >= 0 &&
            dictPane.iActiveTabIndex < dictPane.listTabs.length) {
            var dictOldTab = dictPane.listTabs[dictPane.iActiveTabIndex];
            if (dictOldTab.terminal && dictOldTab.terminal.element) {
                dictOldTab.terminal.element.style.display = "none";
            }
        }

        dictPane.iActiveTabIndex = iTabIndex;
        var dictTab = dictPane.listTabs[iTabIndex];

        if (!dictTab.terminal) {
            fnInitializeTerminal(dictPane, dictTab);
        } else {
            dictTab.terminal.element.style.display = "";
            fnRefitTabPreservingSelection(dictTab, dictTab.fitAddon);
            dictTab.terminal.focus();
        }
        fnRenderPaneTabs(iPaneId);
    }

    /* xterm's built-in width tables render some emoji (✅ ❌) as a
       single cell, so the font paints the glyph wider than its cell
       and the right edge is clipped. The Unicode 11 addon supplies the
       width data that marks these as double-width, matching how the
       producing program laid out the table. Guarded so a blocked CDN
       degrades to the built-in widths rather than breaking the tab. */
    function fnActivateWideCharWidths(terminal) {
        if (typeof Unicode11Addon === "undefined") return;
        try {
            terminal.loadAddon(new Unicode11Addon.Unicode11Addon());
            terminal.unicode.activeVersion = "11";
        } catch (error) {
            console.warn(
                "[terminal] wide-char width activation failed; "
                + "falling back to built-in widths:", error);
        }
    }

    function fnInitializeTerminal(dictPane, dictTab) {
        if (typeof Terminal === "undefined") {
            // The vendored xterm library failed to load. Degrade to a
            // clear message instead of throwing — an exception here
            // used to propagate out of workflow activation and abort
            // the PROOF/repos/badge initialization that follows it.
            VaibifyApp.fnShowToast(
                "The terminal library failed to load — reload the " +
                "page to restore terminal access.", "error");
            return;
        }
        var elContainer = dictPane.elPane.querySelector(
            ".terminal-pane-container"
        );

        var dictTheme = Object.assign({}, DICT_TERMINAL_THEME,
            { cursor: sCurrentCursorColor });
        var terminal = new Terminal({
            cursorBlink: true,
            fontSize: 14,
            fontFamily:
                '"SF Mono", "Fira Code", "Cascadia Code", monospace',
            theme: dictTheme,
            scrollback: iCurrentScrollback,
            allowProposedApi: true,
            /* When a full-screen program (agent, vim, htop) turns on
               mouse reporting, xterm forwards drags to the program and
               plain drag-select stops working. Shift+drag overrides
               this on Linux unconditionally, but the macOS override
               (Option+drag) is opt-in — without it there is no way to
               select text on a Mac while such a program is running. */
            macOptionClickForcesSelection: true,
        });

        var fitAddon = new FitAddon.FitAddon();
        terminal.loadAddon(fitAddon);
        fnActivateWideCharWidths(terminal);
        terminal.open(elContainer);

        /* The kill overlay exists for the same reason the terminal
           does: a foreground process a researcher started needs a way
           to be stopped from the pane it is running in. It is created
           only where a shell can exist, so a host pane showing the
           notice does not offer a button that would signal nothing. */
        if (fbTerminalIsAvailableHere()) {
            var elKillButton = document.createElement("button");
            elKillButton.className = "terminal-kill-overlay";
            elKillButton.title = "Kill foreground process";
            elKillButton.textContent = "Kill";
            elContainer.style.position = "relative";
            elContainer.appendChild(elKillButton);
            elKillButton.addEventListener("click", function () {
                fnKillTabDirect(dictTab);
            });
            dictTab.elKillButton = elKillButton;
        }

        dictTab.terminal = terminal;
        dictTab.fitAddon = fitAddon;

        fnBindCopyAndSelectionHandlers(dictTab, terminal);
        fnBindScrollbackWheelHandler(terminal);
        if (fbTerminalIsAvailableHere()) {
            fnArmLazyShellDial(dictPane, dictTab, terminal, elContainer);
        } else {
            fnRenderTerminalUnavailableNotice(terminal);
        }
        fnBindTerminalResize(dictPane, dictTab, elContainer, fitAddon);

        /* Fit once the container is laid out, not before: an early fit
           lands at the wrong width and the corrective fit then reflows
           the buffer, duplicating wrapped scrollback lines. */
        fnRefitTabPreservingSelection(dictTab, fitAddon);
        fnScheduleRefit(dictTab, fitAddon);

        terminal.focus();
    }

    function fbHandleCopyKeyEvent(event, terminal) {
        if (event.type !== "keydown") return true;
        if (String(event.key).toLowerCase() !== "c") return true;
        var bMacintoshCopy = event.metaKey && !event.ctrlKey;
        var bLinuxCopy = event.ctrlKey && event.shiftKey;
        if (!bMacintoshCopy && !bLinuxCopy) return true;
        if (!terminal.hasSelection()) return true;
        event.preventDefault();
        VaibifyFileOps.fnCopyToClipboard(terminal.getSelection());
        return false;
    }

    function fnFlushDeferredFit(dictTab) {
        if (!dictTab.bFitDeferred) return;
        if (dictTab.terminal && dictTab.terminal.hasSelection()) return;
        dictTab.bFitDeferred = false;
        if (dictTab.fitAddon) dictTab.fitAddon.fit();
    }

    /* Copy-on-select: onSelectionChange fires repeatedly during a
       drag, so the copy is debounced until the selection has been
       stable briefly — intermediate copies are harmless (the final
       selection overwrites them) but pointless. The quiet clipboard
       path never shows toasts and never uses the focus-stealing
       textarea fallback: a missed background copy is recoverable via
       Cmd+C or right-click, a focus theft on every selection is not. */
    var I_COPY_ON_SELECT_DELAY_MS = 200;

    function fnScheduleCopyOnSelect(dictTab, terminal) {
        if (dictTab.iCopyOnSelectTimer) {
            window.clearTimeout(dictTab.iCopyOnSelectTimer);
            dictTab.iCopyOnSelectTimer = null;
        }
        if (!terminal.hasSelection()) return;
        dictTab.iCopyOnSelectTimer = window.setTimeout(function () {
            dictTab.iCopyOnSelectTimer = null;
            if (!terminal.hasSelection()) return;
            var sSelection = terminal.getSelection();
            if (sSelection) {
                VaibifyFileOps.fnCopyToClipboardQuietly(sSelection);
            }
        }, I_COPY_ON_SELECT_DELAY_MS);
    }

    function fnCopySelectionOnRightClick(event, terminal) {
        if (!terminal.hasSelection()) return;
        event.preventDefault();
        event.stopPropagation();
        VaibifyFileOps.fnCopyToClipboard(terminal.getSelection());
    }

    function fnBindCopyAndSelectionHandlers(dictTab, terminal) {
        terminal.attachCustomKeyEventHandler(function (event) {
            return fbHandleCopyKeyEvent(event, terminal);
        });
        dictTab.disposableOnSelectionChange =
            terminal.onSelectionChange(function () {
                fnFlushDeferredFit(dictTab);
                fnScheduleCopyOnSelect(dictTab, terminal);
            });
        terminal.element.addEventListener("contextmenu",
            function (event) {
                fnCopySelectionOnRightClick(event, terminal);
            });
    }

    /* --- Reaching the scrollback ---

       A full-screen program (an agent, vim, htop) turns on mouse
       reporting, and xterm then forwards the WHEEL to it — so the
       researcher's own scrollback becomes unreachable for as long as
       that program runs. Option+drag overrides the same capture for
       selection, but there is no built-in override for the wheel, and
       xterm's drag-scroll only engages once the pointer leaves the
       pane. The measured consequence was a hard ceiling of one
       screenful on anything that could be selected or copied.

       Shift+wheel therefore scrolls the viewport UNCONDITIONALLY,
       rather than detecting capture and reacting to it. The state it
       would detect is not stable: the container's agent self-updates,
       so whether the wheel is captured can change under a researcher
       who changed nothing. A single behaviour in both states is the
       only one that stays true across that. */

    var _I_FALLBACK_CELL_HEIGHT_PIXELS = 17;

    /* The browser remaps a shifted wheel onto the HORIZONTAL axis, so
       the very gesture this handler exists for arrives with deltaY 0
       and its magnitude in deltaX. Reading deltaY alone measured zero
       lines on every real Shift+scroll — and since the handler still
       claimed the event, it SUPPRESSED the scroll rather than
       performing it, which is worse than not having been written.
       (The browser-lane test did not catch it: Playwright's
       mouse.wheel writes deltaY directly and does not reproduce the
       remap, so the suite drove a shape no browser sends.) */
    function ffMeasureWheelDelta(event) {
        return event.deltaY || event.deltaX;
    }

    function fiMeasureWheelScrollLines(event, terminal) {
        var fDelta = ffMeasureWheelDelta(event);
        if (event.deltaMode === 1) return Math.round(fDelta);
        if (event.deltaMode === 2) {
            return Math.round(fDelta) * terminal.rows;
        }
        var fCellHeight = _I_FALLBACK_CELL_HEIGHT_PIXELS;
        if (terminal.element && terminal.rows > 0) {
            fCellHeight = terminal.element.clientHeight / terminal.rows;
        }
        var iLines = Math.round(fDelta / Math.max(fCellHeight, 1));
        if (iLines !== 0 || fDelta === 0) return iLines;
        /* A trackpad emits many sub-cell deltas; rounding each to zero
           would make Shift+wheel do nothing at all on a trackpad. */
        return fDelta > 0 ? 1 : -1;
    }

    function fbHandleScrollbackWheelEvent(event, terminal) {
        if (!event.shiftKey) return true;
        var iLines = fiMeasureWheelScrollLines(event, terminal);
        /* Never swallow a wheel this handler did not act on: claiming
           the event while scrolling nothing is how the remap above
           turned a fix into a regression. */
        if (iLines === 0) return true;
        event.preventDefault();
        terminal.scrollLines(iLines);
        return false;
    }

    function fnBindScrollbackWheelHandler(terminal) {
        terminal.attachCustomWheelEventHandler(function (event) {
            return fbHandleScrollbackWheelEvent(event, terminal);
        });
    }

    /* The whole buffer, with wrapped rows rejoined into the line the
       program actually wrote. This is the path that needs no gesture
       at all: it cannot be defeated by a program holding the mouse,
       which is what makes it the reliable way to get an agent's
       answer out of the pane. */
    function flistReadBufferLines(buffer) {
        var listLines = [];
        for (var iRow = 0; iRow < buffer.length; iRow++) {
            var bufferLine = buffer.getLine(iRow);
            if (!bufferLine) continue;
            var sText = bufferLine.translateToString(true);
            if (bufferLine.isWrapped && listLines.length > 0) {
                listLines[listLines.length - 1] += sText;
            } else {
                listLines.push(sText);
            }
        }
        return listLines;
    }

    function fsReadTerminalScrollback(terminal) {
        var listLines = flistReadBufferLines(terminal.buffer.active);
        while (listLines.length > 0 &&
               listLines[listLines.length - 1] === "") {
            listLines.pop();
        }
        return listLines.join("\n");
    }

    function fnCopyPaneScrollback(iPaneId) {
        var dictPane = listPanes[iPaneId];
        if (!dictPane) return;
        var dictTab = dictPane.listTabs[dictPane.iActiveTabIndex];
        if (!dictTab || !dictTab.terminal) return;
        var sScrollback = fsReadTerminalScrollback(dictTab.terminal);
        if (!sScrollback) {
            VaibifyApp.fnShowToast(
                "This terminal has no output to copy yet.", "error");
            return;
        }
        VaibifyFileOps.fnCopyToClipboard(sScrollback);
    }

    function fbTerminalIsAvailableHere() {
        /* Both modes now (2026-08-15 ruling): a host project's shell
           is a real PTY on the researcher's own machine, journaled
           through the gated launch, and the server's per-session
           banner reminds them that processes can outlive the tab.
           The helper survives as the single gate a future
           unavailable mode would flip. */
        return true;
    }

    function flistTerminalUnavailableNotice() {
        /* Kept for the gate above: whatever mode flips it off next
           gets a pane that says something true instead of dialing a
           socket it knows will be refused. */
        return [
            "",
            "  A terminal is not available for this project.",
            "",
        ];
    }

    function fnRenderTerminalUnavailableNotice(terminal) {
        flistTerminalUnavailableNotice().forEach(function (sLine) {
            terminal.write(sLine + "\r\n");
        });
        terminal.options.cursorBlink = false;
    }

    /* --- Lazy shell dial ---
       A shell is a quarantine-bearing operation: once one has run,
       the container's quiescence can only be PROVEN settled, never
       assumed, and an unclean exit leaves a journal record that
       blocks the container until `vaibify reconcile`. Spawning the
       shell on workflow entry meant every visit staked that claim
       whether or not the researcher ever touched the pane — so the
       pane now renders armed but silent, and the socket dials (and
       the shell spawns) only on the researcher's first gesture into
       it, or when a caller explicitly sends a command. */

    function fnArmLazyShellDial(dictPane, dictTab, terminal, elContainer) {
        terminal.write(
            "\x1b[2m  A shell starts here on your first click or " +
            "keystroke.\x1b[0m\r\n");
        dictTab.disposableOnLazyData = terminal.onData(function () {
            fnDialTabShell(dictTab);
        });
        dictTab.fnLazyMousedown = function () {
            /* The container element is shared by every tab in the
               pane; only the visible tab's gesture may dial. */
            if (dictPane.listTabs[dictPane.iActiveTabIndex] === dictTab) {
                fnDialTabShell(dictTab);
            }
        };
        elContainer.addEventListener("mousedown", dictTab.fnLazyMousedown);
        dictTab.elLazyContainer = elContainer;
    }

    function fnDisarmLazyShellDial(dictTab) {
        if (dictTab.disposableOnLazyData) {
            dictTab.disposableOnLazyData.dispose();
        }
        dictTab.disposableOnLazyData = null;
        if (dictTab.elLazyContainer && dictTab.fnLazyMousedown) {
            dictTab.elLazyContainer.removeEventListener(
                "mousedown", dictTab.fnLazyMousedown);
        }
        dictTab.elLazyContainer = null;
        dictTab.fnLazyMousedown = null;
    }

    function fnDialTabShell(dictTab) {
        if (dictTab.websocket || !dictTab.terminal) return;
        if (!fbTerminalIsAvailableHere()) return;
        fnDisarmLazyShellDial(dictTab);
        _fnCancelShellRedial(dictTab);
        dictTab.iRedialAttempt = 0;
        dictTab.fRedialElapsedSeconds = 0;
        dictTab.terminal.reset();
        fnConnectTerminalWebSocket(dictTab, dictTab.terminal);
    }

    /* A dropped terminal socket used to write "[Connection closed]"
       and stop forever: the pane was dead until the researcher
       recreated the tab, which is not something a dashboard should
       ask for after a Wi-Fi blip.

       What comes back is a NEW shell, deliberately. The old one is
       gone -- closing the socket terminates the recorded session and
       proves it dead, which is what lets vaibify say anything honest
       about the project being quiet -- so the pane says the shell
       ended rather than pretending the session resumed. Anything a
       researcher needs to survive a disconnection belongs in a step,
       which is durable; a terminal is not. */

    function _fnReleaseSocketDisposables(dictTab) {
        if (dictTab.disposableOnData) dictTab.disposableOnData.dispose();
        dictTab.disposableOnData = null;
        if (dictTab.disposableOnResize) {
            dictTab.disposableOnResize.dispose();
        }
        dictTab.disposableOnResize = null;
    }

    function _fnCancelShellRedial(dictTab) {
        if (dictTab.iRedialTimer) {
            clearTimeout(dictTab.iRedialTimer);
        }
        dictTab.iRedialTimer = null;
    }

    function _ffNextRedialDelaySeconds(dictTab) {
        /* Same shape and the same budget as the pipeline socket: the
           window belongs to the SESSION, so a terminal that outlived
           it would be retrying against a revoked credential. */
        var fWindow = _F_REDIAL_WINDOW_DEFAULT_SECONDS;
        if (typeof VaibifyWebSocket !== "undefined" &&
                VaibifyWebSocket.ffGetReconnectWindowSeconds) {
            fWindow = VaibifyWebSocket.ffGetReconnectWindowSeconds();
        }
        var fDelay = Math.min(
            Math.pow(2, dictTab.iRedialAttempt || 0),
            _F_REDIAL_MAX_DELAY_SECONDS,
        );
        var fElapsed = dictTab.fRedialElapsedSeconds || 0;
        if (fElapsed + fDelay > fWindow - _F_REDIAL_MARGIN_SECONDS) {
            return -1;
        }
        return fDelay;
    }

    function _fnScheduleShellRedial(dictTab, terminal, event) {
        var iCode = event ? event.code : 0;
        /* A deliberate refusal answers the same way every time, and a
           normal close is the researcher leaving. Neither is retried. */
        var bNormal = iCode === 1000 || iCode === 1001;
        var bRefusal = iCode >= 4000 && iCode < 5000;
        if (bNormal || bRefusal || !fbTerminalIsAvailableHere()) return;
        var fDelay = _ffNextRedialDelaySeconds(dictTab);
        if (fDelay < 0) {
            terminal.write(
                "\x1b[33m[vaibify]\x1b[0m Reconnecting stopped — this " +
                "session has expired. Reload the dashboard to start a " +
                "new shell.\r\n");
            return;
        }
        dictTab.iRedialAttempt = (dictTab.iRedialAttempt || 0) + 1;
        dictTab.fRedialElapsedSeconds =
            (dictTab.fRedialElapsedSeconds || 0) + fDelay;
        terminal.write(
            "\x1b[2m  Reconnecting in " + fDelay + "s — a NEW shell " +
            "will start; the previous one has ended.\x1b[0m\r\n");
        dictTab.iRedialTimer = setTimeout(function () {
            dictTab.iRedialTimer = null;
            if (dictTab.websocket || !dictTab.terminal) return;
            fnConnectTerminalWebSocket(dictTab, dictTab.terminal);
        }, fDelay * 1000);
    }

    function fnConnectTerminalWebSocket(dictTab, terminal) {
        var sProtocol =
            window.location.protocol === "https:" ? "wss:" : "ws:";
        var sContainerId = VaibifyApp.fsGetContainerId();
        var sToken = VaibifyApp.fsGetSessionToken();
        var sLeaseId = VaibifyApp.fsGetLeaseId();
        var sUrl = sProtocol + "//" + window.location.host +
            "/ws/terminal/" + sContainerId +
            "?sToken=" + encodeURIComponent(sToken) +
            "&sLeaseId=" + encodeURIComponent(sLeaseId);
        var ws = new WebSocket(sUrl);
        dictTab.websocket = ws;
        ws.binaryType = "arraybuffer";

        ws.onopen = function () {
            dictTab.iRedialAttempt = 0;
            dictTab.fRedialElapsedSeconds = 0;
            ws.send(JSON.stringify({
                sType: "resize",
                iRows: terminal.rows,
                iColumns: terminal.cols,
            }));
        };

        ws.onmessage = function (event) {
            if (event.data instanceof ArrayBuffer) {
                terminal.write(new Uint8Array(event.data));
            } else if (typeof event.data === "string") {
                try {
                    var dictData = JSON.parse(event.data);
                    if (dictData.sType === "error") {
                        terminal.write(
                            "\r\nError: " + dictData.sMessage + "\r\n"
                        );
                    }
                } catch (_) {
                    terminal.write(event.data);
                }
            }
        };

        ws.onclose = function (event) {
            if (dictTab.websocket !== ws) return;
            dictTab.websocket = null;
            _fnReleaseSocketDisposables(dictTab);
            terminal.write(
                "\r\n" + fsDescribeTerminalClose(event) + "\r\n");
            _fnScheduleShellRedial(dictTab, terminal, event);
        };

        dictTab.disposableOnData = terminal.onData(function (sData) {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(new TextEncoder().encode(sData));
            }
        });

        dictTab.disposableOnResize = terminal.onResize(function (size) {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    sType: "resize",
                    iRows: size.rows,
                    iColumns: size.cols,
                }));
            }
        });
    }

    /* The deliberate close codes, named. A researcher told only
       "[Connection closed]" for a refusal reads it as a network fault
       and retries forever; each of these sends them somewhere
       different, which is the whole reason the server keeps them
       distinct. */
    /* The redial budget. The window itself belongs to the session and
       is answered by VaibifyWebSocket, which was told it by the
       server; this default only covers the moment before a connect
       handshake has landed. */
    var _F_REDIAL_WINDOW_DEFAULT_SECONDS = 15;
    var _F_REDIAL_MAX_DELAY_SECONDS = 30;
    var _F_REDIAL_MARGIN_SECONDS = 2;

    var _I_REJECT_TERMINAL_DISABLED = 4503;
    var _I_REJECT_TERMINAL_NOT_ON_HOST = 4504;
    var _I_REJECT_POISONED = 4423;

    function fsDescribeTerminalClose(event) {
        var iCode = event ? event.code : 0;
        if (iCode === _I_REJECT_TERMINAL_DISABLED) {
            return "[Terminals are disabled in this build]";
        }
        if (iCode === _I_REJECT_TERMINAL_NOT_ON_HOST) {
            return "[This project runs on your machine; use your own "
                + "shell]";
        }
        if (iCode === _I_REJECT_POISONED) {
            return "[This container needs 'vaibify reconcile' before "
                + "it can be used]";
        }
        return "[Connection closed]";
    }

    function fdictCaptureTerminalMetrics(term) {
        return {
            iCols: term ? term.cols : -1,
            iRows: term ? term.rows : -1,
            iYdisp: (term && term.buffer && term.buffer.active)
                ? term.buffer.active.viewportY : -1,
            iBase: (term && term.buffer && term.buffer.active)
                ? term.buffer.active.baseY : -1,
        };
    }

    function fnLogResizeChange(dictBefore, dictAfter, dictProposed) {
        var bDimsChanged = (dictBefore.iCols !== dictAfter.iCols ||
            dictBefore.iRows !== dictAfter.iRows);
        var bViewportJumped = (dictBefore.iYdisp !== dictAfter.iYdisp);
        if (bDimsChanged || bViewportJumped) {
            console.log("[TERM-RESIZE]",
                "cols:", dictBefore.iCols, "->", dictAfter.iCols,
                "rows:", dictBefore.iRows, "->", dictAfter.iRows,
                "ydisp:", dictBefore.iYdisp, "->", dictAfter.iYdisp,
                "baseY:", dictBefore.iBase, "->", dictAfter.iBase,
                "proposed:", dictProposed);
        }
    }

    function fdictProposeDimensions(fitAddon) {
        try {
            return fitAddon.proposeDimensions();
        } catch (_) {
            return null;
        }
    }

    function fbDimensionsRefitNeeded(terminal, dictProposed) {
        if (!dictProposed) return false;
        var bSized = dictProposed.cols > 0 && dictProposed.rows > 0 &&
            isFinite(dictProposed.cols) && isFinite(dictProposed.rows);
        if (!bSized) return false;
        return dictProposed.cols !== terminal.cols ||
            dictProposed.rows !== terminal.rows;
    }

    function fnRefitTabPreservingSelection(dictTab, fitAddon) {
        if (dictTab.terminal && dictTab.terminal.hasSelection()) {
            dictTab.bFitDeferred = true;
            return;
        }
        dictTab.bFitDeferred = false;
        var dictProposed = fdictProposeDimensions(fitAddon);
        if (!fbDimensionsRefitNeeded(dictTab.terminal, dictProposed)) {
            return;
        }
        var dictBefore = fdictCaptureTerminalMetrics(dictTab.terminal);
        fitAddon.fit();
        var dictAfter = fdictCaptureTerminalMetrics(dictTab.terminal);
        fnLogResizeChange(dictBefore, dictAfter, dictProposed);
    }

    function fnScheduleRefit(dictTab, fitAddon) {
        if (dictTab.iRefitTimer) {
            window.clearTimeout(dictTab.iRefitTimer);
        }
        dictTab.iRefitTimer = window.setTimeout(function () {
            dictTab.iRefitTimer = null;
            fnRefitTabPreservingSelection(dictTab, fitAddon);
        }, 60);
    }

    function fnBindTerminalResize(dictPane, dictTab, elContainer, fitAddon) {
        var resizeObserver = new ResizeObserver(function () {
            if (dictPane.listTabs[dictPane.iActiveTabIndex] !== dictTab) {
                return;
            }
            fnScheduleRefit(dictTab, fitAddon);
        });
        resizeObserver.observe(elContainer);
        dictTab.resizeObserver = resizeObserver;
    }

    function fnCloseTabInPane(iPaneId, iTabIndex) {
        var dictPane = listPanes[iPaneId];
        if (!dictPane) return;
        if (iTabIndex < 0 || iTabIndex >= dictPane.listTabs.length) return;

        VaibifyApp.fnShowConfirmModal(
            "Close Terminal",
            "Are you sure you want to close this terminal tab?",
            function () {
                fnForceCloseTabInPane(iPaneId, iTabIndex);
            }
        );
    }

    function fnDisposeTab(dictTab) {
        fnDisarmLazyShellDial(dictTab);
        _fnCancelShellRedial(dictTab);
        if (dictTab.websocket) dictTab.websocket.close();
        dictTab.websocket = null;
        if (dictTab.disposableOnData) dictTab.disposableOnData.dispose();
        dictTab.disposableOnData = null;
        if (dictTab.disposableOnResize) dictTab.disposableOnResize.dispose();
        dictTab.disposableOnResize = null;
        if (dictTab.disposableOnSelectionChange) {
            dictTab.disposableOnSelectionChange.dispose();
        }
        dictTab.disposableOnSelectionChange = null;
        if (dictTab.iRefitTimer) {
            window.clearTimeout(dictTab.iRefitTimer);
            dictTab.iRefitTimer = null;
        }
        if (dictTab.iCopyOnSelectTimer) {
            window.clearTimeout(dictTab.iCopyOnSelectTimer);
            dictTab.iCopyOnSelectTimer = null;
        }
        dictTab.bFitDeferred = false;
        if (dictTab.elKillButton && dictTab.elKillButton.parentNode) {
            dictTab.elKillButton.parentNode.removeChild(
                dictTab.elKillButton
            );
        }
        dictTab.elKillButton = null;
        if (dictTab.terminal) {
            dictTab.terminal.clear();
            dictTab.terminal.dispose();
        }
        dictTab.terminal = null;
        dictTab.fitAddon = null;
        if (dictTab.resizeObserver) dictTab.resizeObserver.disconnect();
        dictTab.resizeObserver = null;
    }

    function fnReconcileActiveTab(iPaneId, iClosedIndex) {
        var dictPane = listPanes[iPaneId];
        if (dictPane.listTabs.length === 0) {
            if (listPanes.length > 1) {
                fnRemovePane(iPaneId);
                return;
            }
            dictPane.iActiveTabIndex = -1;
            var elContainer = dictPane.elPane.querySelector(
                ".terminal-pane-container"
            );
            elContainer.innerHTML = "";
        } else if (dictPane.iActiveTabIndex >= dictPane.listTabs.length) {
            dictPane.iActiveTabIndex = dictPane.listTabs.length - 1;
            fnActivateTabInPane(iPaneId, dictPane.iActiveTabIndex);
        } else if (dictPane.iActiveTabIndex === iClosedIndex) {
            var iNewIndex = Math.min(
                iClosedIndex, dictPane.listTabs.length - 1);
            dictPane.iActiveTabIndex = -1;
            fnActivateTabInPane(iPaneId, iNewIndex);
        }
    }

    function fnForceCloseTabInPane(iPaneId, iTabIndex) {
        var dictPane = listPanes[iPaneId];
        if (!dictPane) return;
        if (iTabIndex < 0 || iTabIndex >= dictPane.listTabs.length) return;
        fnDisposeTab(dictPane.listTabs[iTabIndex]);
        dictPane.listTabs.splice(iTabIndex, 1);
        fnReconcileActiveTab(iPaneId, iTabIndex);
        fnRenderPaneTabs(iPaneId);
    }

    /* --- Resize Handle Between Panes --- */

    function fnBindTerminalResizeHandle(elHandle, iPaneId) {
        elHandle.addEventListener("mousedown", function (event) {
            event.preventDefault();
            var iStartX = event.clientX;
            var elPrev = listPanes[iPaneId - 1].elPane;
            var iStartWidth = elPrev.offsetWidth;

            function fnMouseMove(e) {
                var iDelta = e.clientX - iStartX;
                var iNewWidth = Math.max(200, iStartWidth + iDelta);
                elPrev.style.flex = "0 0 " + iNewWidth + "px";
            }
            function fnMouseUp() {
                document.removeEventListener("mousemove", fnMouseMove);
                document.removeEventListener("mouseup", fnMouseUp);
                fnFitAllTerminals();
            }
            document.addEventListener("mousemove", fnMouseMove);
            document.addEventListener("mouseup", fnMouseUp);
        });
    }

    /* --- Public API Helpers --- */

    function fnCloseAll() {
        while (listPanes.length > 0) {
            var dictPane = listPanes[0];
            while (dictPane.listTabs.length > 0) {
                fnDisposeTab(dictPane.listTabs[0]);
                dictPane.listTabs.splice(0, 1);
            }
            dictPane.elPane.remove();
            listPanes.splice(0, 1);
        }
        /* Clear strip and reset */
        var elStrip = document.getElementById("terminalStrip");
        elStrip.innerHTML = "";
        fnUpdateAddPaneButton();
    }

    function fnFitAllTerminals() {
        listPanes.forEach(function (dictPane) {
            if (dictPane.iActiveTabIndex >= 0 &&
                dictPane.iActiveTabIndex < dictPane.listTabs.length) {
                var dictTab = dictPane.listTabs[dictPane.iActiveTabIndex];
                if (dictTab.fitAddon) {
                    fnRefitTabPreservingSelection(
                        dictTab, dictTab.fitAddon);
                }
            }
        });
    }

    /* --- Init --- */

    document.addEventListener("DOMContentLoaded", function () {
        iCurrentScrollback = fiReadStoredScrollback();
        /* Remove placeholder pane from HTML */
        var elStrip = document.getElementById("terminalStrip");
        elStrip.innerHTML = "";

        document.getElementById("btnAddTerminalPane").addEventListener(
            "click", fnCreatePane
        );
        var elHelp = document.getElementById("btnTerminalHelp");
        var elPopup = document.getElementById("terminalHelpPopup");
        if (elHelp && elPopup) {
            elHelp.addEventListener("click", function () {
                elPopup.style.display =
                    elPopup.style.display === "none" ? "" : "none";
            });
            elPopup.querySelector(".help-popup-close")
                .addEventListener("click", function () {
                    elPopup.style.display = "none";
                });
        }
    });

    function _fbSendWhenReady(dictPane, sCommand) {
        /* A freshly created tab's socket is still CONNECTING, so the
           send waits for open. It reports true because the command IS
           going to be delivered -- the caller's question is "will this
           reach a shell", not "has it arrived yet". */
        var dictTab = dictPane.listTabs[dictPane.iActiveTabIndex];
        if (!dictTab || !dictTab.websocket) return false;
        var ws = dictTab.websocket;
        if (ws.readyState === WebSocket.OPEN) {
            ws.send(new TextEncoder().encode(sCommand + "\r"));
            return true;
        }
        if (ws.readyState !== WebSocket.CONNECTING) return false;
        ws.addEventListener("open", function () {
            setTimeout(function () {
                ws.send(new TextEncoder().encode(sCommand + "\r"));
            }, 500);
        }, { once: true });
        return true;
    }

    function fnKillTabDirect(dictTab) {
        if (!dictTab || !dictTab.websocket) return;
        var ws = dictTab.websocket;
        if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ sType: "kill" }));
            if (dictTab.terminal) {
                dictTab.terminal.write(
                    "\r\n\x1b[31m[Process killed]\x1b[0m\r\n"
                );
            }
        }
    }

    function fnApplyTerminalTheme(term, sColor, iPaneIndex, iTabIndex) {
        var iYdispBefore = (term.buffer && term.buffer.active)
            ? term.buffer.active.viewportY : -1;
        term.options.theme =
            Object.assign({}, DICT_TERMINAL_THEME, { cursor: sColor });
        var iYdispAfter = (term.buffer && term.buffer.active)
            ? term.buffer.active.viewportY : -1;
        if (iYdispBefore !== iYdispAfter) {
            console.log("[TERM-THEME]",
                "pane:", iPaneIndex, "tab:", iTabIndex,
                "ydisp:", iYdispBefore, "->", iYdispAfter);
        }
    }

    function fnUpdateCursorColor(sColor) {
        if (sColor === sCurrentCursorColor) return;
        sCurrentCursorColor = sColor;
        for (var i = 0; i < listPanes.length; i++) {
            var listTabs = listPanes[i].listTabs;
            for (var j = 0; j < listTabs.length; j++) {
                if (listTabs[j].terminal) {
                    fnApplyTerminalTheme(
                        listTabs[j].terminal, sColor, i, j);
                }
            }
        }
    }

    function fiReadStoredScrollback() {
        try {
            var sStored = localStorage.getItem(S_SCROLLBACK_STORAGE_KEY);
            if (sStored === "unlimited") return I_UNLIMITED_SCROLLBACK;
            var iValue = parseInt(sStored, 10);
            if (isFinite(iValue) && iValue >= I_MINIMUM_SCROLLBACK) {
                return Math.min(iValue, I_UNLIMITED_SCROLLBACK);
            }
        } catch (_) { /* localStorage may be unavailable */ }
        return I_DEFAULT_SCROLLBACK;
    }

    function fnApplyScrollbackToOpenTerminals() {
        listPanes.forEach(function (dictPane) {
            dictPane.listTabs.forEach(function (dictTab) {
                if (dictTab.terminal) {
                    dictTab.terminal.options.scrollback = iCurrentScrollback;
                }
            });
        });
    }

    function fnPersistScrollback(bUnlimited) {
        try {
            localStorage.setItem(
                S_SCROLLBACK_STORAGE_KEY,
                bUnlimited ? "unlimited" : String(iCurrentScrollback));
        } catch (_) { /* localStorage may be unavailable */ }
    }

    function fnSetScrollback(iLines, bUnlimited) {
        if (bUnlimited) {
            iCurrentScrollback = I_UNLIMITED_SCROLLBACK;
        } else if (isFinite(iLines)) {
            iCurrentScrollback = Math.max(
                I_MINIMUM_SCROLLBACK,
                Math.min(iLines, I_UNLIMITED_SCROLLBACK));
        } else {
            iCurrentScrollback = I_DEFAULT_SCROLLBACK;
        }
        fnPersistScrollback(bUnlimited);
        fnApplyScrollbackToOpenTerminals();
    }

    function fiGetScrollback() {
        return iCurrentScrollback;
    }

    function fbScrollbackIsUnlimited() {
        return iCurrentScrollback >= I_UNLIMITED_SCROLLBACK;
    }

    return {
        fnUpdateCursorColor: fnUpdateCursorColor,
        fnSetScrollback: fnSetScrollback,
        fiGetScrollback: fiGetScrollback,
        fbScrollbackIsUnlimited: fbScrollbackIsUnlimited,
        fnCreateTab: function () {
            if (listPanes.length === 0) {
                fnCreatePane();
            } else {
                fnCreateTab(0);
            }
        },
        fnEnsureTab: function () {
            if (listPanes.length === 0) {
                fnCreatePane();
            }
        },
        fnCreatePane: fnCreatePane,
        fnCloseAll: fnCloseAll,
        fnFitActiveTerminal: fnFitAllTerminals,
        /* Returns whether the command reached a shell. The Boolean is
           load-bearing and survives the terminal coming back: a caller
           that needs a shell must learn it cannot have one rather than
           firing and then polling forever for output that will never
           arrive. It answers false wherever no terminal exists — a
           host project today — which is exactly the case the void
           signature used to hide. */
        fbSendCommandInFreshTab: function (sCommand) {
            if (!fbTerminalIsAvailableHere()) return false;
            if (listPanes.length === 0) {
                fnCreatePane();
            } else {
                fnCreateTab(0);
            }
            /* An explicit command is as deliberate as a keystroke:
               the fresh tab is armed lazy, so dial it before asking
               whether the command will reach a shell. */
            var dictPane = listPanes[0];
            var dictTab = dictPane.listTabs[dictPane.iActiveTabIndex];
            if (dictTab) fnDialTabShell(dictTab);
            return _fbSendWhenReady(dictPane, sCommand);
        },
    };
})();
