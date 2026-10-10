/* Vaibify — Leftover processes and files: the hub glyph and its panel.
   Every sentence shown here is the server's, rendered verbatim and
   escaped; this module words nothing about the host's state itself. */

var VaibifyRemnants = (function () {
    "use strict";

    var _S_READ_URL = "/api/system/remnants";
    var _S_RESCAN_URL = "/api/system/remnants/rescan";
    var _S_REMOVE_URL = "/api/system/remnants/remove";
    var _S_PANEL_TITLE = "Leftover processes and files";
    var _S_CATEGORY_UNKNOWN = "unknown";
    var _I_RESCAN_POLL_MILLISECONDS = 1000;
    var _I_RESCAN_POLL_LIMIT = 20;

    var _dictCategoryTitles = {
        untrackedSession: "Interactive sessions nobody recorded",
        quarantinedRecord: "Quarantined journal records",
        unregisteredKeepAlive: "Keep-alives no registry holds",
        unownedSessionLane: "Keep-alives of containers no window holds",
        containerWithoutInit: "Containers without an init process",
        untrackedStoppedContainer: "Stopped containers vaibify no longer tracks"
    };

    /* The picker poll hands the registry's summary here on every tick;
       the glyph is hidden at zero and carries the server's title. */
    function fnRenderSummary(dictSummary) {
        var elGlyph = document.getElementById("btnRemnants");
        if (!elGlyph) return;
        var iCount = dictSummary ? (dictSummary.iCount || 0) : 0;
        var bFailed = !!(dictSummary && dictSummary.bReaperFailed);
        if (!dictSummary || (iCount === 0 && !bFailed)) {
            elGlyph.style.display = "none";
            return;
        }
        elGlyph.style.display = "";
        elGlyph.textContent = "⚠ " + iCount;
        elGlyph.title = dictSummary.sGlyphTitle || _S_PANEL_TITLE;
        elGlyph.classList.toggle("btn-icon--remnants-failed", bFailed);
    }

    function fnBindRemnantsGlyph() {
        var elGlyph = document.getElementById("btnRemnants");
        if (!elGlyph) return;
        elGlyph.addEventListener("click", function () { fnOpenPanel(); });
    }

    async function fnOpenPanel() {
        try {
            var dictRemnants = await VaibifyApi.fdictGet(_S_READ_URL);
            _fnShowPanel(dictRemnants);
        } catch (error) {
            VaibifyDiagnosis.fnReportFailureFromError(error);
        }
    }

    function _fnShowPanel(dictRemnants) {
        VaibifyModals.fnShowInfoModal(
            _S_PANEL_TITLE, _fsRenderPanel(dictRemnants));
        _fnBindPanelButtons(dictRemnants);
    }

    function _fsRenderPanel(dictRemnants) {
        var listItems = dictRemnants.listItems || [];
        var sHtml = "";
        if (dictRemnants.sScanError) {
            sHtml += '<p class="remnants-scan-error">' +
                VaibifyUtilities.fnEscapeHtml(dictRemnants.sScanError) + "</p>";
        }
        sHtml += _fsRenderScannedAt(dictRemnants);
        if (!listItems.length && !dictRemnants.sScanError) {
            sHtml += '<p class="muted-text">Nothing left over was found.</p>';
        }
        _flistCategories(listItems).forEach(function (sCategory) {
            sHtml += _fsRenderCategory(sCategory, listItems.filter(
                function (dictItem) { return dictItem.sCategory === sCategory; }));
        });
        sHtml += '<div class="remnants-actions">' +
            '<button class="btn btn-primary remnants-remove-selected" type="button"' +
            (_fbAnyActionable(listItems) ? "" : " disabled") +
            ">Remove selected</button> " +
            '<button class="btn remnants-remove-proven" type="button"' +
            (_fbAnyProvenActionable(listItems) ? "" : " disabled") +
            ">Remove all proven</button> " +
            '<button class="btn remnants-rescan" type="button">Rescan</button>' +
            '<p class="remnants-outcomes" style="display:none"></p></div>';
        sHtml += _fsRenderReaperHealth(dictRemnants.dictReaperHealth || {});
        return sHtml;
    }

    function _fsRenderScannedAt(dictRemnants) {
        if (dictRemnants.bScanning) {
            return '<p class="muted-text remnants-scanned">A scan is running.</p>';
        }
        if (!dictRemnants.sScannedIso) {
            return '<p class="muted-text remnants-scanned">No scan has run yet.</p>';
        }
        return '<p class="muted-text remnants-scanned">Scanned ' +
            VaibifyUtilities.fnEscapeHtml(dictRemnants.sScannedIso) + "</p>";
    }

    function _flistCategories(listItems) {
        var listSeen = [];
        listItems.forEach(function (dictItem) {
            if (listSeen.indexOf(dictItem.sCategory) === -1) {
                listSeen.push(dictItem.sCategory);
            }
        });
        return listSeen;
    }

    function _fsRenderCategory(sCategory, listItems) {
        var sTitle = _dictCategoryTitles[sCategory] || sCategory;
        var sHtml = '<div class="remnants-category" data-category="' +
            VaibifyUtilities.fnEscapeHtml(sCategory) + '"><h3>' +
            VaibifyUtilities.fnEscapeHtml(sTitle) + "</h3>";
        ["proven", "possibly", _S_CATEGORY_UNKNOWN].forEach(function (sTier) {
            var listOfTier = listItems.filter(
                function (dictItem) { return dictItem.sTier === sTier; });
            if (!listOfTier.length) return;
            sHtml += '<h4 class="remnants-tier">' +
                _fsTierHeading(sTier) + "</h4><ul class=\"remnants-items\">";
            listOfTier.forEach(function (dictItem) {
                sHtml += _fsRenderItem(dictItem);
            });
            sHtml += "</ul>";
        });
        return sHtml + "</div>";
    }

    function _fsTierHeading(sTier) {
        if (sTier === "proven") return "Proven orphaned";
        if (sTier === "possibly") return "Possibly orphaned";
        return "Could not be checked";
    }

    function _fsRenderItem(dictItem) {
        var bActionable = dictItem.sAction && dictItem.sAction !== "none";
        var sId = VaibifyUtilities.fnEscapeHtml(dictItem.sItemId || "");
        var sCheckbox = bActionable
            ? '<input type="checkbox" class="remnants-select" data-item-id="' +
              sId + '" data-tier="' + VaibifyUtilities.fnEscapeHtml(dictItem.sTier) +
              '" data-confirm="' + (dictItem.bConfirmRequired ? "true" : "false") +
              '"> '
            : "";
        return '<li class="remnants-item" data-item-id="' + sId + '"><label>' +
            sCheckbox + '<span class="remnants-evidence">' +
            VaibifyUtilities.fnEscapeHtml(dictItem.sEvidence || "") +
            "</span></label>" +
            (dictItem.sRemedy
                ? ' <span class="remnants-remedy muted-text">' +
                  VaibifyUtilities.fnEscapeHtml(dictItem.sRemedy) + "</span>"
                : "") +
            "</li>";
    }

    function _fbAnyActionable(listItems) {
        return listItems.some(function (dictItem) {
            return dictItem.sAction && dictItem.sAction !== "none";
        });
    }

    function _fbAnyProvenActionable(listItems) {
        return listItems.some(function (dictItem) {
            return dictItem.sTier === "proven" && dictItem.sAction &&
                dictItem.sAction !== "none";
        });
    }

    function _fsRenderReaperHealth(dictHealth) {
        var listNames = Object.keys(dictHealth).sort();
        var sHtml = '<div class="remnants-health"><h3>Automatic cleanups</h3>';
        if (!listNames.length) {
            return sHtml + '<p class="muted-text">No cleanup has run yet.</p></div>';
        }
        sHtml += "<ul>";
        listNames.forEach(function (sName) {
            var dictRecord = dictHealth[sName];
            var sLine = sName + ": " + (dictRecord.sOutcome || "") +
                ", removed " + (dictRecord.iRemoved || 0) +
                (dictRecord.sReason ? " (" + dictRecord.sReason + ")" : "") +
                (dictRecord.sRemedy ? " " + dictRecord.sRemedy : "") +
                (dictRecord.sLastRunIso ? " at " + dictRecord.sLastRunIso : "");
            sHtml += '<li class="remnants-health-row remnants-health-row--' +
                VaibifyUtilities.fnEscapeHtml(dictRecord.sOutcome || "") + '">' +
                VaibifyUtilities.fnEscapeHtml(sLine) + "</li>";
        });
        return sHtml + "</ul></div>";
    }

    function _fnBindPanelButtons(dictRemnants) {
        var elModal = document.getElementById("modalInfo");
        if (!elModal) return;
        var elRemoveSelected = elModal.querySelector(".remnants-remove-selected");
        var elRemoveProven = elModal.querySelector(".remnants-remove-proven");
        var elRescan = elModal.querySelector(".remnants-rescan");
        if (elRemoveSelected) {
            elRemoveSelected.addEventListener("click", function () {
                _fnRemoveItems(_flistSelectedItems(elModal, false), dictRemnants);
            });
        }
        if (elRemoveProven) {
            elRemoveProven.addEventListener("click", function () {
                _fnRemoveItems(_flistSelectedItems(elModal, true), dictRemnants);
            });
        }
        if (elRescan) {
            elRescan.addEventListener("click", function () { _fnRescan(); });
        }
    }

    /* Selected checkboxes, or every proven actionable item. Sessions are
       never proven, so "Remove all proven" can never include one. */
    function _flistSelectedItems(elModal, bAllProven) {
        var listSelected = [];
        elModal.querySelectorAll(".remnants-select").forEach(function (elBox) {
            var bChosen = bAllProven
                ? elBox.getAttribute("data-tier") === "proven"
                : elBox.checked;
            if (bChosen) {
                listSelected.push({
                    sItemId: elBox.getAttribute("data-item-id"),
                    bConfirmRequired: elBox.getAttribute("data-confirm") === "true"
                });
            }
        });
        return listSelected;
    }

    function _fnRemoveItems(listSelected, dictRemnants) {
        if (!listSelected.length) {
            _fnShowOutcomes(["Select at least one item first."]);
            return;
        }
        var listNeedingConfirmation = listSelected.filter(
            function (dictSelected) { return dictSelected.bConfirmRequired; });
        if (!listNeedingConfirmation.length) {
            _fnPostRemoval(listSelected);
            return;
        }
        VaibifyModals.fnShowConfirmModal(
            "Confirm before ending these",
            _fsConfirmationText(listNeedingConfirmation, dictRemnants),
            function () { _fnPostRemoval(listSelected); });
    }

    function _fsConfirmationText(listNeedingConfirmation, dictRemnants) {
        var dictById = {};
        (dictRemnants.listItems || []).forEach(function (dictItem) {
            dictById[dictItem.sItemId] = dictItem;
        });
        return listNeedingConfirmation.map(function (dictSelected) {
            var dictItem = dictById[dictSelected.sItemId];
            return dictItem ? dictItem.sEvidence : dictSelected.sItemId;
        }).join("\n\n");
    }

    async function _fnPostRemoval(listSelected) {
        try {
            var dictResult = await VaibifyApi.fdictPost(_S_REMOVE_URL, {
                listItemIds: listSelected.map(
                    function (dictSelected) { return dictSelected.sItemId; })
            });
            _fnShowOutcomes((dictResult.listOutcomes || []).map(
                function (dictOutcome) { return dictOutcome.sOutcome; }));
        } catch (error) {
            VaibifyDiagnosis.fnReportFailureFromError(error);
        }
    }

    function _fnShowOutcomes(listSentences) {
        var elOutcomes = document.querySelector("#modalInfo .remnants-outcomes");
        if (!elOutcomes) return;
        elOutcomes.style.display = "";
        elOutcomes.textContent = listSentences.join(" ");
    }

    /* A rescan only asks the hub to start a pass; the panel then waits for
       a newer scan instant and re-renders what the hub found. */
    async function _fnRescan() {
        var elOutcomes = document.querySelector("#modalInfo .remnants-outcomes");
        try {
            var dictBefore = await VaibifyApi.fdictGet(_S_READ_URL);
            await VaibifyApi.fdictPost(_S_RESCAN_URL, {});
            if (elOutcomes) {
                elOutcomes.style.display = "";
                elOutcomes.textContent = "Rescanning…";
            }
            var dictAfter = await _fdictAwaitNewerScan(dictBefore.sScannedIso);
            _fnShowPanel(dictAfter);
        } catch (error) {
            VaibifyDiagnosis.fnReportFailureFromError(error);
        }
    }

    async function _fdictAwaitNewerScan(sPreviousIso) {
        var dictLatest = null;
        for (var iAttempt = 0; iAttempt < _I_RESCAN_POLL_LIMIT; iAttempt += 1) {
            await new Promise(function (fnResolve) {
                setTimeout(fnResolve, _I_RESCAN_POLL_MILLISECONDS);
            });
            dictLatest = await VaibifyApi.fdictGet(_S_READ_URL);
            if (!dictLatest.bScanning && dictLatest.sScannedIso !== sPreviousIso) {
                return dictLatest;
            }
        }
        return dictLatest;
    }

    return {
        fnRenderSummary: fnRenderSummary,
        fnBindRemnantsGlyph: fnBindRemnantsGlyph,
        fnOpenPanel: fnOpenPanel
    };
})();
