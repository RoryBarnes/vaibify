/* Vaibify — Resource monitor with canvas sparklines */

var VaibifyMonitor = (function () {
    "use strict";

    var I_MAX_DATA_POINTS = 60;
    var I_POLL_INTERVAL_MS = 5000;
    var I_SPARKLINE_WIDTH = 200;
    var I_SPARKLINE_HEIGHT = 40;
    var F_DISK_BANNER_FRACTION = 0.05;

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
        dictCanvasContextCache = {};
        fnStopMonitoring();
    }

    /* --- Panel Creation --- */

    function fsMonitorPanelStyle() {
        return (
            "position: fixed; bottom: 16px; right: 16px; " +
            "width: 260px; background: #282840; " +
            "border: 1px solid #3a3a58; border-radius: 6px; " +
            "padding: 14px; z-index: 1500; " +
            "box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);"
        );
    }

    function fsMonitorHeaderHtml() {
        return (
            '<div style="display: flex; justify-content: space-between; ' +
            'align-items: center; margin-bottom: 12px;">' +
            '<span style="font-weight: 600; color: #13aed5; ' +
            'font-size: 13px; letter-spacing: 0.5px;">MONITOR</span>' +
            '<button id="btnMonitorClose" style="border: none; ' +
            'background: transparent; color: #8080a0; cursor: pointer; ' +
            'font-size: 16px;">&times;</button></div>'
        );
    }

    function fsMonitorMetricHtml(sLabel, sTextId, sCanvasId) {
        return (
            '<div style="margin-bottom: 10px;">' +
            '<div style="font-size: 11px; color: #a0a0b8; ' +
            'text-transform: uppercase; letter-spacing: 0.5px; ' +
            'margin-bottom: 4px;">' + sLabel + '</div>' +
            '<span id="' + sTextId + '" style="font-size: 18px; ' +
            'font-weight: 300; color: #e0e0e8;">--</span>' +
            '<canvas id="' + sCanvasId + '" width="' +
            I_SPARKLINE_WIDTH + '" height="' + I_SPARKLINE_HEIGHT +
            '" style="display: block; width: 100%; ' +
            'margin-top: 4px;"></canvas></div>'
        );
    }

    function fsMonitorDiskHtml() {
        return (
            '<div style="margin-bottom: 10px;">' +
            '<div style="font-size: 11px; color: #a0a0b8; ' +
            'text-transform: uppercase; letter-spacing: 0.5px; ' +
            'margin-bottom: 4px;">DISK</div>' +
            '<span id="monitorDiskText" style="font-size: 18px; ' +
            'font-weight: 300; color: #e0e0e8;">--</span></div>'
        );
    }

    function fsMonitorBannerHtml() {
        return (
            '<div id="monitorBanner" style="display: none; ' +
            'margin-bottom: 10px; padding: 8px; ' +
            'background: #4a1f1f; border: 1px solid #c14a4a; ' +
            'border-radius: 4px; color: #f0c0c0; font-size: 12px; ' +
            'line-height: 1.4;"></div>'
        );
    }

    function fsMonitorUnavailableHtml() {
        return (
            '<div id="monitorUnavailable" style="display: none; ' +
            'margin-top: 6px; padding: 8px; background: #2f2f48; ' +
            'border: 1px solid #5a5a78; border-radius: 4px; ' +
            'color: #c0c0d8; font-size: 12px; line-height: 1.4;"></div>'
        );
    }

    function fnCreatePanel() {
        elMonitorPanel = document.createElement("div");
        elMonitorPanel.id = "monitorPanel";
        elMonitorPanel.style.cssText = fsMonitorPanelStyle();
        elMonitorPanel.innerHTML =
            fsMonitorHeaderHtml() +
            fsMonitorBannerHtml() +
            fsMonitorMetricHtml(
                "CPU", "monitorCpuText", "canvasCpuSparkline") +
            fsMonitorMetricHtml(
                "Memory", "monitorMemoryText", "canvasMemorySparkline") +
            fsMonitorDiskHtml() +
            fsMonitorUnavailableHtml();
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

    function fsHumanizeReason(sReason) {
        if (sReason === "daemon-unreachable") {
            return "Docker daemon not reachable";
        }
        if (sReason === "container-not-running") {
            return "container is not running";
        }
        if (sReason === "timeout") {
            return "docker stats timed out";
        }
        if (sReason === "parse-error") {
            return "could not parse docker stats output";
        }
        return sReason || "unknown";
    }

    function fnSetUnavailableNotice(sReason) {
        var elNotice = document.getElementById("monitorUnavailable");
        if (!elNotice) return;
        if (sReason) {
            elNotice.style.display = "block";
            elNotice.textContent = "Resource monitor unavailable: " +
                fsHumanizeReason(sReason);
        } else {
            elNotice.style.display = "none";
            elNotice.textContent = "";
        }
    }

    function fnAppendHistory(listHistory, dValue) {
        listHistory.push(dValue);
        if (listHistory.length > I_MAX_DATA_POINTS) {
            listHistory.shift();
        }
    }

    function fnUpdateCpuMemoryDisplay(dictData) {
        var fCpuPercent = dictData.fCpuPercent || 0;
        var fMemoryPercent = dictData.fMemoryPercent || 0;
        var sMemoryUsage = dictData.sMemoryUsage || "";
        fnAppendHistory(listCpuHistory, fCpuPercent);
        fnAppendHistory(listMemoryHistory, fMemoryPercent);
        var elCpuText = document.getElementById("monitorCpuText");
        var elMemoryText = document.getElementById("monitorMemoryText");
        if (elCpuText) {
            elCpuText.textContent = fCpuPercent.toFixed(1) + "%";
        }
        if (elMemoryText) {
            elMemoryText.textContent = sMemoryUsage ?
                sMemoryUsage : fMemoryPercent.toFixed(1) + "%";
        }
        fnDrawSparkline(
            "canvasCpuSparkline", listCpuHistory, "#13aed5", 100);
        fnDrawSparkline(
            "canvasMemorySparkline", listMemoryHistory, "#c084fc", 100);
    }

    function fsFormatDiskText(dictDisk) {
        if (!dictDisk || !dictDisk.bAvailable) {
            return "unavailable";
        }
        var sUsed = dictDisk.sUsedHuman || "?";
        var sTotal = dictDisk.sTotalHuman || "?";
        return sUsed + " / " + sTotal;
    }

    function fnUpdateDiskDisplay(dictData) {
        var elDiskText = document.getElementById("monitorDiskText");
        if (!elDiskText) return;
        var dictDisk = dictData.dictDisk || {};
        elDiskText.textContent = fsFormatDiskText(dictDisk);
        var bDangerouslyLow = !!dictDisk.bAvailable
            && (dictDisk.fFreeFraction || 0) < F_DISK_BANNER_FRACTION;
        elDiskText.style.color = bDangerouslyLow ? "#ff6464" : "#e0e0e8";
    }

    function fsBuildBannerMessage(dictData) {
        var dictDisk = dictData.dictDisk || {};
        if (!dictData.bDiskWarning || !dictDisk.bAvailable) {
            return "";
        }
        var fFreePercent = (dictDisk.fFreeFraction || 0) * 100;
        return "Container disk almost full: " +
            fFreePercent.toFixed(1) + "% free (" +
            (dictDisk.sFreeHuman || "?") + " of " +
            (dictDisk.sTotalHuman || "?") +
            "). Run `vaibify clean` or grow the Colima VM.";
    }

    function fnUpdateBanner(dictData) {
        var elBanner = document.getElementById("monitorBanner");
        if (!elBanner) return;
        var sMessage = fsBuildBannerMessage(dictData);
        if (sMessage) {
            elBanner.style.display = "block";
            elBanner.textContent = sMessage;
        } else {
            elBanner.style.display = "none";
            elBanner.textContent = "";
        }
    }

    function fnUpdateDisplay(dictData) {
        var bAvailable = dictData.bAvailable !== false;
        fnSetUnavailableNotice(bAvailable ? "" : (dictData.sReason || ""));
        fnUpdateCpuMemoryDisplay(dictData);
        fnUpdateDiskDisplay(dictData);
        fnUpdateBanner(dictData);
    }

    /* --- Canvas Sparklines --- */

    function fnDrawSparklineFill(
        ctx, listData, dMaxValue, dStartX, dStepX,
        iPadding, iDrawHeight, sColor
    ) {
        var iPointCount = listData.length;
        ctx.beginPath();
        ctx.moveTo(dStartX, iPadding + iDrawHeight);
        for (var i = 0; i < iPointCount; i++) {
            var dX = dStartX + i * dStepX;
            var dNormalized = Math.min(listData[i] / dMaxValue, 1);
            ctx.lineTo(dX, iPadding + iDrawHeight * (1 - dNormalized));
        }
        var dLastX = dStartX + (iPointCount - 1) * dStepX;
        ctx.lineTo(dLastX, iPadding + iDrawHeight);
        ctx.closePath();
        ctx.fillStyle = fnConvertToRgba(sColor, 0.15);
        ctx.fill();
    }

    function fnDrawSparklineStroke(
        ctx, listData, dMaxValue, dStartX, dStepX,
        iPadding, iDrawHeight, sColor
    ) {
        ctx.beginPath();
        for (var j = 0; j < listData.length; j++) {
            var dX = dStartX + j * dStepX;
            var dNormalized = Math.min(listData[j] / dMaxValue, 1);
            var dY = iPadding + iDrawHeight * (1 - dNormalized);
            if (j === 0) { ctx.moveTo(dX, dY); }
            else { ctx.lineTo(dX, dY); }
        }
        ctx.strokeStyle = sColor;
        ctx.lineWidth = 1.5;
        ctx.stroke();
    }

    function fnDrawSparkline(sCanvasId, listData, sColor, dMaxValue) {
        var elCanvas = document.getElementById(sCanvasId);
        if (!elCanvas) return;
        if (!dictCanvasContextCache[sCanvasId]) {
            dictCanvasContextCache[sCanvasId] = elCanvas.getContext("2d");
        }
        var ctx = dictCanvasContextCache[sCanvasId];
        var iPadding = 2;
        ctx.clearRect(0, 0, elCanvas.width, elCanvas.height);
        if (listData.length < 2) return;
        var iDrawWidth = elCanvas.width - 2 * iPadding;
        var iDrawHeight = elCanvas.height - 2 * iPadding;
        var dStepX = iDrawWidth / (I_MAX_DATA_POINTS - 1);
        var dStartX = iPadding +
            (I_MAX_DATA_POINTS - listData.length) * dStepX;
        fnDrawSparklineFill(
            ctx, listData, dMaxValue, dStartX, dStepX,
            iPadding, iDrawHeight, sColor);
        fnDrawSparklineStroke(
            ctx, listData, dMaxValue, dStartX, dStepX,
            iPadding, iDrawHeight, sColor);
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
