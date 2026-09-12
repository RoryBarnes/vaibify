/* Vaibify — Per-file reproduction outcomes, rendered one way for every card */

var VaibifyFileOutcomes = (function () {
    "use strict";

    /*
     * One table for the three places a reproduction's per-file
     * verdicts reach the screen: the Level 3 attestation card, the
     * "Your reproduction" card beneath it, and the "Reproduce a
     * published project" result. Every status word and every hash
     * in those tables comes from the backend's listFileOutcomes --
     * nothing here compares a hash, because a second comparison in
     * JavaScript would be a second authority on a question that has
     * one.
     */

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;
    var _I_SHORT_HASH_CHARACTERS = 12;

    var _DICT_STATUS_WORDS = {
        matched: "re-derived, byte-identical",
        diverged: "diverged",
        missing: "not produced",
        carried: "carried in unchanged",
    };

    /* Diverged and missing first, because that is what the reader
       acts on; then what was given rather than re-derived; then what
       reproduced. The same order the "+" card has always used. */
    var _DICT_STATUS_RANK = {
        diverged: 0,
        missing: 0,
        carried: 1,
        matched: 2,
    };

    function _fiStatusRank(sStatus) {
        var iRank = _DICT_STATUS_RANK[sStatus];
        return typeof iRank === "number" ? iRank : 3;
    }

    function flistSortOutcomes(listFileOutcomes) {
        return (listFileOutcomes || []).map(function (dictOutcome, iIndex) {
            return {dictOutcome: dictOutcome, iIndex: iIndex};
        }).sort(function (dictLeft, dictRight) {
            var iDelta = _fiStatusRank(dictLeft.dictOutcome.sStatus) -
                _fiStatusRank(dictRight.dictOutcome.sStatus);
            return iDelta !== 0 ? iDelta : dictLeft.iIndex - dictRight.iIndex;
        }).map(function (dictRanked) {
            return dictRanked.dictOutcome;
        });
    }

    function _fsRenderHashCell(sHash) {
        if (!sHash) return "<td class=\"file-outcome-hash\">—</td>";
        return '<td class="file-outcome-hash" title="' +
            fnEscapeHtml(sHash) + '"><code>' +
            fnEscapeHtml(String(sHash).slice(0, _I_SHORT_HASH_CHARACTERS)) +
            "</code></td>";
    }

    function _fsRenderOutcomeRow(dictOutcome) {
        var sStatus = dictOutcome.sStatus || "";
        var sWord = _DICT_STATUS_WORDS[sStatus] || sStatus;
        return '<tr class="file-outcome-' + fnEscapeHtml(sStatus) + '">' +
            "<td>" + fnEscapeHtml(dictOutcome.sPath || "") + "</td>" +
            _fsRenderHashCell(dictOutcome.sExpected) +
            _fsRenderHashCell(dictOutcome.sObserved) +
            "<td>" + fnEscapeHtml(sWord) + "</td></tr>";
    }

    function fsRenderFileOutcomesTable(listFileOutcomes) {
        if (!listFileOutcomes || !listFileOutcomes.length) return "";
        var sRows = flistSortOutcomes(listFileOutcomes)
            .map(_fsRenderOutcomeRow).join("");
        return '<table class="file-outcomes"><thead><tr>' +
            "<th>File</th><th>Expected</th><th>Observed</th>" +
            "<th>Outcome</th></tr></thead><tbody>" + sRows +
            "</tbody></table>";
    }

    function flistOutcomesFromPathLists(listDiverged, listCarried,
                                        listMatched) {
        /* An older report carries three path lists and no hashes.
           Shaped into outcomes so the one table renders it; the hash
           cells read as absent rather than invented. */
        function flistShape(listPaths, sStatus) {
            return (listPaths || []).map(function (sPath) {
                return {sPath: sPath, sExpected: null, sObserved: null,
                        sStatus: sStatus};
            });
        }
        return flistShape(listDiverged, "diverged")
            .concat(flistShape(listCarried, "carried"))
            .concat(flistShape(listMatched, "matched"));
    }

    function fdictLineMarksFromOutcomes(listFileOutcomes) {
        /* Path -> status, exactly as the backend graded it. This is
           what the manifest viewer colours by; it never looks at the
           hashes on the lines it colours. */
        var dictLineMarks = {};
        (listFileOutcomes || []).forEach(function (dictOutcome) {
            if (dictOutcome && dictOutcome.sPath && dictOutcome.sStatus) {
                dictLineMarks[dictOutcome.sPath] = dictOutcome.sStatus;
            }
        });
        return dictLineMarks;
    }

    return {
        fsRenderFileOutcomesTable: fsRenderFileOutcomesTable,
        flistSortOutcomes: flistSortOutcomes,
        flistOutcomesFromPathLists: flistOutcomesFromPathLists,
        fdictLineMarksFromOutcomes: fdictLineMarksFromOutcomes,
    };
})();
