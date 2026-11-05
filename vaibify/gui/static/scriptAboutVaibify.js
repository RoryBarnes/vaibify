/* Vaibify — which vaibify is running, at the top of each hub's help.

   Both hubs' "?" windows open through fnShowHubHelp, so the About
   section has one home. Every value in it is the server's answer
   (GET /api/system/about): the version, the commit, whether the code
   is a source checkout or an installed package, and the Python version.
   It names no path, because help windows end up in screenshots. */

var VaibifyAbout = (function () {
    "use strict";

    var _S_SECTION_ID = "hubHelpAbout";

    function fnShowHubHelp(sTitle, sHelpHtml) {
        VaibifyModals.fnShowInfoModal(sTitle,
            '<section class="hub-help-about" id="' + _S_SECTION_ID + '">' +
            '<h3>About vaibify</h3>' +
            '<p class="muted-text">Reading the version&hellip;</p>' +
            '</section>' + sHelpHtml);
        _fnFillAboutSection();
    }

    async function _fnFillAboutSection() {
        var dictAbout;
        try {
            dictAbout = await VaibifyApi.fdictGet("/api/system/about");
        } catch (error) {
            var elFailed = _felementAboutBody();
            if (!elFailed) return;
            VaibifyDiagnosis.fnRenderFailureInline(
                elFailed, "The version could not be read: ", error);
            return;
        }
        var elBody = _felementAboutBody();
        if (elBody) elBody.replaceWith(_felementAboutList(dictAbout));
    }

    function _felementAboutBody() {
        /* Null once the window was closed, or reopened as another one. */
        var elSection = document.getElementById(_S_SECTION_ID);
        return elSection ? elSection.querySelector("p") : null;
    }

    function _felementAboutList(dictAbout) {
        var elList = document.createElement("dl");
        elList.className = "hub-help-about-facts";
        [["Version", dictAbout.sVersion || "not recorded"],
         ["Commit", dictAbout.sCommit || "not recorded"],
         ["Running from", dictAbout.sSource +
            (dictAbout.bModified === true ? ", with local changes" : "")],
         ["Python", dictAbout.sPythonVersion],
        ].forEach(function (listPair) {
            var elTerm = document.createElement("dt");
            elTerm.textContent = listPair[0];
            var elValue = document.createElement("dd");
            elValue.textContent = listPair[1];
            elList.appendChild(elTerm);
            elList.appendChild(elValue);
        });
        return elList;
    }

    return {
        fnShowHubHelp: fnShowHubHelp,
    };
})();
