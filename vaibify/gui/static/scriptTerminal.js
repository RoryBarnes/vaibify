/* Pipeleyen — Multi-pane terminal management with xterm.js */

const PipeleyenTerminal = (function () {
    "use strict";

    var I_MAX_PANES = 3;
    var iTabCounter = 0;

    /*
     * listPanes: array of pane objects, each with:
     *   iId, listTabs, iActiveTabIndex, elPane
     */
    var listPanes = [];

    /* --- Terminal Theme --- */

    var DICT_TERMINAL_THEME = {
        background: "#0d0d1a",
        foreground: "#e0e0e8",
        cursor: "#13aed5",
        selectionBackground: "rgba(19, 174, 213, 0.3)",
        black: "#1e1e2e",
        red: "#c91111",
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

        elPane.querySelector(".terminal-pane-add").addEventListener(
            "click", function () {
                fnCreateTab(iPaneId);
            }
        );

        fnCreateTab(iPaneId);
        fnUpdateAddPaneButton();
    }

    function fnRemovePane(iPaneId) {
        var dictPane = listPanes[iPaneId];
        if (!dictPane) return;

        /* Close all tabs in this pane */
        while (dictPane.listTabs.length > 0) {
            fnCloseTabInPane(iPaneId, 0);
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

            /* Update add button data-pane */
            var elAdd = dictPane.elPane.querySelector(".terminal-pane-add");
            if (elAdd) {
                elAdd.dataset.pane = String(iNewId);
                var iCapturedId = iNewId;
                elAdd.onclick = function () {
                    fnCreateTab(iCapturedId);
                };
            }
        });
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

        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;

        iTabCounter++;
        var dictTab = {
            iId: iTabCounter,
            sLabel: "Term " + iTabCounter,
            terminal: null,
            fitAddon: null,
            websocket: null,
            resizeObserver: null,
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
                '<span class="kill-tab" title="Kill process">' +
                '&#9632;</span>' +
                '<span class="close-tab">&times;</span>';
            var iCapturedPane = iPaneId;
            var iCapturedTab = iTabIndex;
            elTab.addEventListener("click", function (event) {
                if (event.target.classList.contains("close-tab")) {
                    fnCloseTabInPane(iCapturedPane, iCapturedTab);
                } else if (event.target.classList.contains("kill-tab")) {
                    fnKillTabProcess(iCapturedPane, iCapturedTab);
                } else {
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
            dictTab.fitAddon.fit();
            dictTab.terminal.focus();
        }
        fnRenderPaneTabs(iPaneId);
    }

    function fnInitializeTerminal(dictPane, dictTab) {
        var elContainer = dictPane.elPane.querySelector(
            ".terminal-pane-container"
        );

        var terminal = new Terminal({
            cursorBlink: true,
            fontSize: 14,
            fontFamily:
                '"SF Mono", "Fira Code", "Cascadia Code", monospace',
            theme: DICT_TERMINAL_THEME,
        });

        var fitAddon = new FitAddon.FitAddon();
        terminal.loadAddon(fitAddon);
        terminal.open(elContainer);
        fitAddon.fit();

        dictTab.terminal = terminal;
        dictTab.fitAddon = fitAddon;

        fnConnectTerminalWebSocket(dictTab, terminal);
        fnBindTerminalResize(dictPane, dictTab, elContainer, fitAddon);

        terminal.focus();
    }

    function fnConnectTerminalWebSocket(dictTab, terminal) {
        var sProtocol =
            window.location.protocol === "https:" ? "wss:" : "ws:";
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var sToken = PipeleyenApp.fsGetSessionToken();
        var sUrl = sProtocol + "//" + window.location.host +
            "/ws/terminal/" + sContainerId +
            "?sToken=" + encodeURIComponent(sToken);
        var ws = new WebSocket(sUrl);
        dictTab.websocket = ws;
        ws.binaryType = "arraybuffer";

        ws.onopen = function () {
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

        ws.onclose = function () {
            terminal.write("\r\n[Connection closed]\r\n");
        };

        terminal.onData(function (sData) {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(new TextEncoder().encode(sData));
            }
        });

        terminal.onResize(function (size) {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    sType: "resize",
                    iRows: size.rows,
                    iColumns: size.cols,
                }));
            }
        });
    }

    function fnBindTerminalResize(dictPane, dictTab, elContainer, fitAddon) {
        var resizeObserver = new ResizeObserver(function () {
            if (dictPane.listTabs[dictPane.iActiveTabIndex] === dictTab) {
                fitAddon.fit();
            }
        });
        resizeObserver.observe(elContainer);
        dictTab.resizeObserver = resizeObserver;
    }

    function fnCloseTabInPane(iPaneId, iTabIndex) {
        var dictPane = listPanes[iPaneId];
        if (!dictPane) return;
        if (iTabIndex < 0 || iTabIndex >= dictPane.listTabs.length) return;

        var dictTab = dictPane.listTabs[iTabIndex];
        if (dictTab.websocket) dictTab.websocket.close();
        if (dictTab.terminal) dictTab.terminal.dispose();
        if (dictTab.resizeObserver) dictTab.resizeObserver.disconnect();

        dictPane.listTabs.splice(iTabIndex, 1);

        if (dictPane.listTabs.length === 0) {
            /* Remove pane if empty (unless it's the last pane) */
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
        } else if (dictPane.iActiveTabIndex === iTabIndex) {
            var iNewIndex = Math.min(iTabIndex, dictPane.listTabs.length - 1);
            dictPane.iActiveTabIndex = -1;
            fnActivateTabInPane(iPaneId, iNewIndex);
        }
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
                var dictTab = dictPane.listTabs[0];
                if (dictTab.websocket) dictTab.websocket.close();
                if (dictTab.terminal) dictTab.terminal.dispose();
                if (dictTab.resizeObserver) {
                    dictTab.resizeObserver.disconnect();
                }
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
                if (dictTab.fitAddon) dictTab.fitAddon.fit();
            }
        });
    }

    function fnFitActiveTerminal() {
        fnFitAllTerminals();
    }

    /* --- Init --- */

    document.addEventListener("DOMContentLoaded", function () {
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

    function fnKillTabProcess(iPaneId, iTabIndex) {
        var dictPane = listPanes[iPaneId];
        if (!dictPane) return;
        var dictTab = dictPane.listTabs[iTabIndex];
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

    return {
        fnCreateTab: function () {
            if (listPanes.length === 0) {
                fnCreatePane();
            } else {
                fnCreateTab(0);
            }
        },
        fnCreatePane: fnCreatePane,
        fnCloseAll: fnCloseAll,
        fnFitActiveTerminal: fnFitActiveTerminal,
        fnSendCommand: function (sCommand) {
            if (listPanes.length === 0) {
                fnCreatePane();
            }
            var dictPane = listPanes[0];
            var dictTab = dictPane.listTabs[dictPane.iActiveTabIndex];
            if (!dictTab || !dictTab.websocket) return;
            var ws = dictTab.websocket;
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(new TextEncoder().encode(sCommand + "\r"));
            } else if (ws.readyState === WebSocket.CONNECTING) {
                var sPending = sCommand;
                ws.addEventListener("open", function () {
                    setTimeout(function () {
                        ws.send(new TextEncoder().encode(
                            sPending + "\r"));
                    }, 200);
                }, { once: true });
            }
        },
    };
})();
