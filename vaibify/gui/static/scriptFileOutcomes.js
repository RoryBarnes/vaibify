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

    /* The assessed rerun's per-file outcome (schema 7), in the
       comparison policy's own words, and the class each wears. A file
       whose bytes differ but whose published comparisons passed is a
       PASS and must never be coloured as a divergence; one vaibify
       could not assess is neither a pass nor a failure. */
    var _DICT_OUTCOME_WORDS = {
        "byte-identical": "re-derived, byte-identical",
        "comparisons passed": "bytes differ; published comparisons passed",
        "failed": "failed",
        "insufficient evidence": "not assessed",
        "carried": "carried in unchanged",
        "unchanged": "pinned input, unchanged",
        "changed": "pinned input, changed",
        "missing": "pinned input, missing",
        "not graded": "not graded (published inputs changed)",
    };
    var _DICT_OUTCOME_CLASSES = {
        "byte-identical": "matched",
        "comparisons passed": "comparisons-passed",
        "failed": "diverged",
        "insufficient evidence": "not-assessed",
        "carried": "carried",
        "unchanged": "matched",
        "changed": "diverged",
        "missing": "missing",
        "not graded": "not-assessed",
    };
    var _DICT_OUTCOME_RANK = {
        "failed": 0, "changed": 0, "missing": 0,
        "insufficient evidence": 1, "not graded": 1,
        "comparisons passed": 2, "carried": 3,
        "byte-identical": 4, "unchanged": 4,
    };

    function _fiStatusRank(sStatus) {
        var iRank = _DICT_STATUS_RANK[sStatus];
        return typeof iRank === "number" ? iRank : 3;
    }

    function _fiOutcomeRank(dictOutcome) {
        if (dictOutcome.sOutcome) {
            var iRank = _DICT_OUTCOME_RANK[dictOutcome.sOutcome];
            return typeof iRank === "number" ? iRank : 5;
        }
        return _fiStatusRank(dictOutcome.sStatus);
    }

    function flistSortOutcomes(listFileOutcomes) {
        return (listFileOutcomes || []).map(function (dictOutcome, iIndex) {
            return {dictOutcome: dictOutcome, iIndex: iIndex};
        }).sort(function (dictLeft, dictRight) {
            var iDelta = _fiOutcomeRank(dictLeft.dictOutcome) -
                _fiOutcomeRank(dictRight.dictOutcome);
            return iDelta !== 0 ? iDelta : dictLeft.iIndex - dictRight.iIndex;
        }).map(function (dictRanked) {
            return dictRanked.dictOutcome;
        });
    }

    function _fsRenderHash(sLabel, sHash) {
        if (!sHash) return sLabel + " \u2014";
        return sLabel + ' <code title="' + fnEscapeHtml(sHash) + '">' +
            fnEscapeHtml(String(sHash).slice(0, _I_SHORT_HASH_CHARACTERS)) +
            "</code>";
    }

    function _fsOutcomeWord(dictOutcome) {
        /* The assessed outcome when the record carries one, with the
           policy's reason -- "within vaibify's image-difference
           threshold (policy v1)", or why a figure cannot be assessed.
           Otherwise the byte-only verifier's word. A pinned input
           (script, input data, environment file) was never
           regenerated, only checked unchanged, so it must not wear the
           word an output earns by being re-derived. */
        if (dictOutcome.sOutcome) {
            var sWord = _DICT_OUTCOME_WORDS[dictOutcome.sOutcome] ||
                dictOutcome.sOutcome;
            return dictOutcome.sOutcomeReason ?
                sWord + ": " + dictOutcome.sOutcomeReason : sWord;
        }
        var sStatus = dictOutcome.sStatus || "";
        if (dictOutcome.sRole === "pinned-input") {
            if (sStatus === "matched") return "pinned input, unchanged";
            if (sStatus === "missing") return "pinned input, missing";
        }
        return _DICT_STATUS_WORDS[sStatus] || sStatus;
    }

    function _fsRenderVaibifyNote(dictOutcome) {
        /* The server's correction beside a frozen policy's reason
           (comparisonPolicyNotes): the reason above stays the policy's
           own words, and this says what vaibify knows about it now. */
        if (!dictOutcome.sVaibifyNote) return "";
        return '<div class="file-outcome-note">' +
            fnEscapeHtml(dictOutcome.sVaibifyNote) + "</div>";
    }

    function _fsRenderOutcomeRow(dictOutcome) {
        /* Two lines per file, not four columns: these cards sit in a
           narrow panel, where a table gave the hashes their full width
           and squeezed the path and the verdict to a character or two
           (researcher-reported, 2026-09-27). The full hash stays one
           hover away. */
        var sStatus = dictOutcome.sOutcome ?
            (_DICT_OUTCOME_CLASSES[dictOutcome.sOutcome] || "not-assessed") :
            (dictOutcome.sStatus || "");
        var sWord = _fsOutcomeWord(dictOutcome);
        return '<li class="file-outcome file-outcome-' +
            fnEscapeHtml(sStatus) + '"><div class="file-outcome-head">' +
            '<span class="file-outcome-path">' +
            fnEscapeHtml(dictOutcome.sPath || "") + "</span>" +
            '<span class="file-outcome-word">' + fnEscapeHtml(sWord) +
            "</span></div>" + _fsRenderVaibifyNote(dictOutcome) +
            '<div class="file-outcome-hashes">' +
            _fsRenderHash("expected", dictOutcome.sExpected) + " \u00b7 " +
            _fsRenderHash("observed", dictOutcome.sObserved) + "</div></li>";
    }

    function fsRenderFileOutcomesTable(listFileOutcomes) {
        if (!listFileOutcomes || !listFileOutcomes.length) return "";
        var sRows = flistSortOutcomes(listFileOutcomes)
            .map(_fsRenderOutcomeRow).join("");
        return '<ul class="file-outcomes">' + sRows + "</ul>";
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
