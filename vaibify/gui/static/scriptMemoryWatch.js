/* Vaibify — the memory watch: header chip, incident banners, toasts.

   Renders the hub's answer from GET /api/monitor/{id}/memory and
   derives nothing from it: the level, the chip text and every sentence
   are the server's. The hub samples each owned container on its own
   loop, so this poll runs whenever a container is open -- including
   the Blank Project, where the file-status poll does not run -- and
   stops when the researcher leaves.

   A kill is told once, not on every poll: one error toast per incident
   id, remembered in localStorage so a reload or a second tab does not
   tell it again, and one banner per incident the researcher has not
   dismissed. Dismissing hides that banner only; the chip keeps the
   kill count for as long as the hub holds the incidents. "Near the
   limit" toasts once per episode, keyed by the time the server says
   the episode began, so it never repeats while memory stays high. */

var VaibifyMemoryWatch = (function () {
    "use strict";

    var I_POLL_INTERVAL_MS = 10000;
    var I_MAX_REMEMBERED_IDS = 200;
    var S_SHOWN_KEY = "vaibify.memoryWatch.toastedIncidents";
    var S_DISMISSED_KEY = "vaibify.memoryWatch.dismissedIncidents";
    var S_NEAR_KEY = "vaibify.memoryWatch.toastedNearEpisodes";
    var S_POLL_FAILED_TITLE = "Memory could not be checked: this " +
        "dashboard could not reach the hub's memory watch.";

    var _dictState = {
        sContainerId: "",
        sContainerName: "",
        iTimer: null,
        iGeneration: 0,
        bInFlight: false,
        sBannerSignature: "",
    };
    /* Ids this page has already handled, kept beside localStorage so a
       browser that refuses storage still toasts each incident once. */
    var _dictRememberedThisPage = {};

    function fnStart(sContainerId) {
        fnStop();
        if (!sContainerId) return;
        _dictState.sContainerId = sContainerId;
        var iGeneration = _dictState.iGeneration;
        _fnPoll(iGeneration);
        _dictState.iTimer = setInterval(function () {
            _fnPoll(iGeneration);
        }, I_POLL_INTERVAL_MS);
    }

    function fnStop() {
        if (_dictState.iTimer !== null) {
            clearInterval(_dictState.iTimer);
            _dictState.iTimer = null;
        }
        _dictState.iGeneration += 1;
        _dictState.sContainerId = "";
        _dictState.sContainerName = "";
        _dictState.bInFlight = false;
        _fnHideChip();
        _fnRenderBanners([]);
    }

    async function _fnPoll(iGeneration) {
        var sContainerId = _dictState.sContainerId;
        if (!sContainerId || _dictState.bInFlight) return;
        _dictState.bInFlight = true;
        try {
            var dictMemory = await VaibifyApi.fdictGet(
                "/api/monitor/" + encodeURIComponent(sContainerId) +
                "/memory");
            if (iGeneration !== _dictState.iGeneration) return;
            _fnRender(dictMemory);
        } catch (error) {
            if (iGeneration !== _dictState.iGeneration) return;
            _fnRenderPollFailure();
        } finally {
            if (iGeneration === _dictState.iGeneration) {
                _dictState.bInFlight = false;
            }
        }
    }

    function _fnRender(dictMemory) {
        var listIncidents = dictMemory.listIncidents || [];
        _dictState.sContainerName = dictMemory.sContainerName || "";
        _fnRenderChip(dictMemory);
        _fnRenderBanners(listIncidents.filter(function (dictIncident) {
            return !_fbRemembered(S_DISMISSED_KEY, dictIncident.sIncidentId);
        }));
        _fnToastNewIncidents(listIncidents);
        _fnToastNearEpisode(dictMemory.dictCurrent || {});
    }

    /* --- The chip --- */

    function _fnRenderChip(dictMemory) {
        var elChip = document.getElementById("memoryWatchChip");
        if (!elChip) return;
        var dictCurrent = dictMemory.dictCurrent || {};
        if (!dictCurrent.sChipText) {
            _fnHideChip();
            return;
        }
        elChip.textContent = dictCurrent.sChipText;
        elChip.title = dictCurrent.sSentence || "";
        elChip.setAttribute("data-level", dictCurrent.sLevel || "unknown");
        elChip.classList.toggle(
            "memory-watch-chip-killed", (dictMemory.iKillCount || 0) > 0);
        elChip.style.display = "";
    }

    function _fnRenderPollFailure() {
        /* The hub's answer did not arrive, so there is no server
           sentence to show. What is on screen must not keep reading
           as current: the chip goes gray and says why. */
        var elChip = document.getElementById("memoryWatchChip");
        if (!elChip || !_dictState.sContainerId) return;
        elChip.textContent = "Memory unknown";
        elChip.title = S_POLL_FAILED_TITLE;
        elChip.setAttribute("data-level", "unknown");
        elChip.style.display = "";
    }

    function _fnHideChip() {
        var elChip = document.getElementById("memoryWatchChip");
        if (!elChip) return;
        elChip.style.display = "none";
        elChip.textContent = "";
        elChip.title = "";
        elChip.classList.remove("memory-watch-chip-killed");
    }

    function _fnOpenMemorySettings() {
        if (!_dictState.sContainerName) return;
        VaibifyContainerManager.fnShowContainerSettings(
            _dictState.sContainerName, "settingMemoryLimit");
    }

    /* --- Banners: one per undismissed incident --- */

    function _fnRenderBanners(listIncidents) {
        var elHolder = document.getElementById("memoryIncidentBanners");
        if (!elHolder) return;
        var sSignature = listIncidents.map(function (dictIncident) {
            return dictIncident.sIncidentId + "\u0000" +
                dictIncident.sSentence;
        }).join("\u0001");
        /* Rebuilt only when what it says changes, so a poll never
           swaps the node under a click on its dismiss button. */
        if (sSignature === _dictState.sBannerSignature) return;
        _dictState.sBannerSignature = sSignature;
        elHolder.textContent = "";
        listIncidents.forEach(function (dictIncident) {
            elHolder.appendChild(_felementBuildBanner(dictIncident));
        });
    }

    function _felementBuildBanner(dictIncident) {
        var elBanner = document.createElement("div");
        elBanner.className = "build-warnings-banner memory-incident-banner";
        elBanner.setAttribute("data-incident-id", dictIncident.sIncidentId);
        var elHeader = document.createElement("div");
        elHeader.className = "build-warnings-banner-header";
        var elTitle = document.createElement("span");
        elTitle.textContent = "A process in this container was killed " +
            "for lack of memory";
        var elDismiss = document.createElement("button");
        elDismiss.type = "button";
        elDismiss.className = "build-warnings-banner-dismiss";
        elDismiss.setAttribute("aria-label", "Hide this notice");
        elDismiss.textContent = "×";
        elDismiss.addEventListener("click", function () {
            _fnRemember(S_DISMISSED_KEY, dictIncident.sIncidentId);
            elBanner.remove();
        });
        elHeader.appendChild(elTitle);
        elHeader.appendChild(elDismiss);
        var elSentence = document.createElement("p");
        elSentence.className = "memory-incident-sentence";
        elSentence.textContent = dictIncident.sSentence || "";
        elBanner.appendChild(elHeader);
        elBanner.appendChild(elSentence);
        return elBanner;
    }

    /* --- Toasts: once per incident, once per near episode --- */

    function _fnToastNewIncidents(listIncidents) {
        listIncidents.forEach(function (dictIncident) {
            var sIncidentId = dictIncident.sIncidentId;
            if (!sIncidentId || _fbRemembered(S_SHOWN_KEY, sIncidentId)) {
                return;
            }
            _fnRemember(S_SHOWN_KEY, sIncidentId);
            VaibifyApp.fnShowToast(
                dictIncident.sSentence || "", "error", _fnOpenMemorySettings);
        });
    }

    function _fnToastNearEpisode(dictCurrent) {
        if (dictCurrent.sLevel !== "near" || !dictCurrent.sNearSinceIso) {
            return;
        }
        var sEpisode = (_dictState.sContainerName || "") + "|" +
            dictCurrent.sNearSinceIso;
        if (_fbRemembered(S_NEAR_KEY, sEpisode)) return;
        _fnRemember(S_NEAR_KEY, sEpisode);
        VaibifyApp.fnShowToast(
            dictCurrent.sSentence || "", "warning", _fnOpenMemorySettings);
    }

    /* --- Remembered ids: localStorage, with a per-page fallback --- */

    function _flistReadStored(sKey) {
        try {
            var listIds = JSON.parse(
                window.localStorage.getItem(sKey) || "[]");
            return Array.isArray(listIds) ? listIds : [];
        } catch (error) {
            return [];
        }
    }

    function _fbRemembered(sKey, sId) {
        var dictPage = _dictRememberedThisPage[sKey] || {};
        if (dictPage[sId]) return true;
        return _flistReadStored(sKey).indexOf(sId) >= 0;
    }

    function _fnRemember(sKey, sId) {
        if (!_dictRememberedThisPage[sKey]) {
            _dictRememberedThisPage[sKey] = {};
        }
        _dictRememberedThisPage[sKey][sId] = true;
        var listIds = _flistReadStored(sKey);
        if (listIds.indexOf(sId) >= 0) return;
        listIds.push(sId);
        try {
            window.localStorage.setItem(sKey, JSON.stringify(
                listIds.slice(-I_MAX_REMEMBERED_IDS)));
        } catch (error) {
            /* Storage refused: the per-page record still holds it. */
        }
    }

    function _fnBindChip() {
        var elChip = document.getElementById("memoryWatchChip");
        if (!elChip) return;
        elChip.addEventListener("click", _fnOpenMemorySettings);
        elChip.addEventListener("keydown", function (event) {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                _fnOpenMemorySettings();
            }
        });
    }

    document.addEventListener("DOMContentLoaded", _fnBindChip);

    return {
        fnStart: fnStart,
        fnStop: fnStop,
    };
})();
