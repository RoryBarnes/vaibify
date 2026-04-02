/* Vaibify — Resource monitor with canvas sparklines */

var VaibifyMonitor = (function () {
    "use strict";

    var I_MAX_DATA_POINTS = 60;
    var I_POLL_INTERVAL_MS = 5000;
    var I_SPARKLINE_WIDTH = 200;
    var I_SPARKLINE_HEIGHT = 40;

    var sContainerId = null;
    var iIntervalHandle = null;
    var listCpuHistory = [];
    var listMemoryHistory = [];
    var elMonitorPanel = null;
    var bVisible = false;
    var dictCanvasContextCache = {};

    /* --- Initialization --- */

    function fnInitialize() {
        var elButton = document.getElementById("btnMonitor");
        if (elButton) {
            elButton.addEventListener("click", fnTogglePanel);
        }
    }

    /* --- Panel Toggle --- */

    function fnTogglePanel() {
        if (bVisible) {
            fnHidePanel();
        } else {
            fnShowPanel();
        }
    }

    function fnShowPanel() {
        sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;

        if (!elMonitorPanel) {
            fnCreatePanel();
        }
        elMonitorPanel.style.display = "block";
        bVisible = true;
        fnStartMonitoring();
    }

    function fnHidePanel() {
        if (elMonitorPanel) {
            elMonitorPanel.style.display = "none";
        }
        bVisible = false;
        fnStopMonitoring();
    }

    /* --- Panel Creation --- */

    function fnCreatePanel() {
        elMonitorPanel = document.createElement("div");
        elMonitorPanel.id = "monitorPanel";
        elMonitorPanel.style.cssText =
            "position: fixed; bottom: 16px; right: 16px; " +
            "width: 260px; background: #282840; " +
            "border: 1px solid #3a3a58; border-radius: 6px; " +
            "padding: 14px; z-index: 1500; " +
            "box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);";

        elMonitorPanel.innerHTML =
            '<div style="display: flex; justify-content: space-between; ' +
            'align-items: center; margin-bottom: 12px;">' +
            '<span style="font-weight: 600; color: #13aed5; ' +
            'font-size: 13px; letter-spacing: 0.5px;">MONITOR</span>' +
            '<button id="btnMonitorClose" style="border: none; ' +
            'background: transparent; color: #8080a0; cursor: pointer; ' +
            'font-size: 16px;">&times;</button>' +
            '</div>' +
            '<div style="margin-bottom: 10px;">' +
            '<div style="font-size: 11px; color: #a0a0b8; ' +
            'text-transform: uppercase; letter-spacing: 0.5px; ' +
            'margin-bottom: 4px;">CPU</div>' +
            '<span id="monitorCpuText" style="font-size: 18px; ' +
            'font-weight: 300; color: #e0e0e8;">--</span>' +
            '<canvas id="canvasCpuSparkline" width="' +
            I_SPARKLINE_WIDTH + '" height="' + I_SPARKLINE_HEIGHT +
            '" style="display: block; width: 100%; ' +
            'margin-top: 4px;"></canvas>' +
            '</div>' +
            '<div>' +
            '<div style="font-size: 11px; color: #a0a0b8; ' +
            'text-transform: uppercase; letter-spacing: 0.5px; ' +
            'margin-bottom: 4px;">Memory</div>' +
            '<span id="monitorMemoryText" style="font-size: 18px; ' +
            'font-weight: 300; color: #e0e0e8;">--</span>' +
            '<canvas id="canvasMemorySparkline" width="' +
            I_SPARKLINE_WIDTH + '" height="' + I_SPARKLINE_HEIGHT +
            '" style="display: block; width: 100%; ' +
            'margin-top: 4px;"></canvas>' +
            '</div>';

        document.body.appendChild(elMonitorPanel);

        document.getElementById("btnMonitorClose").addEventListener(
            "click", fnHidePanel
        );
    }

    /* --- Monitoring --- */

    function fnStartMonitoring() {
        if (iIntervalHandle !== null) return;
        fnFetchMonitorData();
        iIntervalHandle = setInterval(
            fnFetchMonitorData, I_POLL_INTERVAL_MS
        );
    }

    function fnStopMonitoring() {
        if (iIntervalHandle !== null) {
            clearInterval(iIntervalHandle);
            iIntervalHandle = null;
        }
    }

    async function fnFetchMonitorData() {
        if (!sContainerId) return;
        try {
            var response = await fetch(
                "/api/monitor/" + sContainerId
            );
            if (!response.ok) return;
            var dictData = await response.json();
            fnUpdateDisplay(dictData);
        } catch (error) {
            /* Silently ignore fetch errors during polling */
        }
    }

    function fnUpdateDisplay(dictData) {
        var dCpuPercent = dictData.dCpuPercent || 0;
        var dMemoryPercent = dictData.dMemoryPercent || 0;
        var sMemoryUsage = dictData.sMemoryUsage || "";

        listCpuHistory.push(dCpuPercent);
        listMemoryHistory.push(dMemoryPercent);

        if (listCpuHistory.length > I_MAX_DATA_POINTS) {
            listCpuHistory.shift();
        }
        if (listMemoryHistory.length > I_MAX_DATA_POINTS) {
            listMemoryHistory.shift();
        }

        var elCpuText = document.getElementById("monitorCpuText");
        var elMemoryText = document.getElementById("monitorMemoryText");

        if (elCpuText) {
            elCpuText.textContent = dCpuPercent.toFixed(1) + "%";
        }
        if (elMemoryText) {
            elMemoryText.textContent = sMemoryUsage ?
                sMemoryUsage : dMemoryPercent.toFixed(1) + "%";
        }

        fnDrawSparkline(
            "canvasCpuSparkline", listCpuHistory, "#13aed5", 100
        );
        fnDrawSparkline(
            "canvasMemorySparkline", listMemoryHistory, "#c084fc", 100
        );
    }

    /* --- Canvas Sparklines --- */

    function fnDrawSparkline(sCanvasId, listData, sColor, dMaxValue) {
        var elCanvas = document.getElementById(sCanvasId);
        if (!elCanvas) return;

        if (!dictCanvasContextCache[sCanvasId]) {
            dictCanvasContextCache[sCanvasId] = elCanvas.getContext("2d");
        }
        var ctx = dictCanvasContextCache[sCanvasId];
        var iWidth = elCanvas.width;
        var iHeight = elCanvas.height;
        var iPadding = 2;

        ctx.clearRect(0, 0, iWidth, iHeight);

        if (listData.length < 2) return;

        var iDrawWidth = iWidth - 2 * iPadding;
        var iDrawHeight = iHeight - 2 * iPadding;
        var iPointCount = listData.length;
        var dStepX = iDrawWidth / (I_MAX_DATA_POINTS - 1);
        var dStartX = iPadding +
            (I_MAX_DATA_POINTS - iPointCount) * dStepX;

        /* Draw filled area */
        ctx.beginPath();
        ctx.moveTo(dStartX, iPadding + iDrawHeight);

        for (var i = 0; i < iPointCount; i++) {
            var dX = dStartX + i * dStepX;
            var dNormalized = Math.min(listData[i] / dMaxValue, 1);
            var dY = iPadding + iDrawHeight * (1 - dNormalized);
            if (i === 0) {
                ctx.lineTo(dX, dY);
            } else {
                ctx.lineTo(dX, dY);
            }
        }

        var dLastX = dStartX + (iPointCount - 1) * dStepX;
        ctx.lineTo(dLastX, iPadding + iDrawHeight);
        ctx.closePath();

        ctx.fillStyle = fnConvertToRgba(sColor, 0.15);
        ctx.fill();

        /* Draw line */
        ctx.beginPath();
        for (var j = 0; j < iPointCount; j++) {
            var dLineX = dStartX + j * dStepX;
            var dLineNormalized = Math.min(
                listData[j] / dMaxValue, 1
            );
            var dLineY = iPadding + iDrawHeight * (1 - dLineNormalized);
            if (j === 0) {
                ctx.moveTo(dLineX, dLineY);
            } else {
                ctx.lineTo(dLineX, dLineY);
            }
        }

        ctx.strokeStyle = sColor;
        ctx.lineWidth = 1.5;
        ctx.stroke();
    }

    function fnConvertToRgba(sHexColor, dAlpha) {
        var iRed = parseInt(sHexColor.substring(1, 3), 16);
        var iGreen = parseInt(sHexColor.substring(3, 5), 16);
        var iBlue = parseInt(sHexColor.substring(5, 7), 16);
        return "rgba(" + iRed + ", " + iGreen + ", " +
            iBlue + ", " + dAlpha + ")";
    }

    /* --- Init --- */

    document.addEventListener("DOMContentLoaded", fnInitialize);

    return {
        fnStartMonitoring: fnStartMonitoring,
        fnStopMonitoring: fnStopMonitoring,
        fnTogglePanel: fnTogglePanel,
    };
})();
