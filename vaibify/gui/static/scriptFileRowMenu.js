/* Vaibify — The menu a Files-panel row offers.

   A file row offers Download to this computer, Open in viewer and Copy
   path; a folder row offers Download as .tar and Copy path. The menu is
   opened by a right-click anywhere on the row and by the context-menu
   key on the row's download button, which is the one control on the row
   a keyboard reaches, so every capability has a route that needs no
   mouse. It borrows the picklist styling the sync menus use and owns
   its own element, because the sync manager's menus belong to it. */

var VaibifyFileRowMenu = (function () {
    "use strict";

    var _S_MENU_ID = "fileRowMenu";
    var _elReturnFocus = null;
    var _bDismissHandlersBound = false;

    function flistBuildItems(dictRow) {
        var fnCopyPath = function () {
            VaibifyFileOps.fnCopyToClipboard(dictRow.sPath);
        };
        if (dictRow.bIsDirectory) {
            return [
                {sLabel: "Download as .tar", fnRun: function () {
                    VaibifyFilePull.fnDownloadToThisComputer(
                        dictRow.sPath, true);
                }},
                {sLabel: "Copy path", fnRun: fnCopyPath},
            ];
        }
        return [
            {sLabel: "Download to this computer", fnRun: function () {
                VaibifyFilePull.fnDownloadToThisComputer(
                    dictRow.sPath, false);
            }},
            {sLabel: "Open in viewer", fnRun: function () {
                VaibifyFigureViewer.fnDisplayInNextViewer(dictRow.sPath);
            }},
            {sLabel: "Copy path", fnRun: fnCopyPath},
        ];
    }

    function _felBuildItemButton(dictItem) {
        var elItem = document.createElement("li");
        var elButton = document.createElement("button");
        elButton.type = "button";
        elButton.className = "picklist-item";
        elButton.setAttribute("role", "menuitem");
        elButton.textContent = dictItem.sLabel;
        elButton.addEventListener("click", function (event) {
            event.stopPropagation();
            fnDismiss();
            dictItem.fnRun();
        });
        elItem.appendChild(elButton);
        return elItem;
    }

    function _fnPlaceMenu(elMenu, iLeft, iTop) {
        elMenu.style.left = Math.round(iLeft) + "px";
        elMenu.style.top = Math.round(iTop) + "px";
        elMenu.hidden = false;
        var rectMenu = elMenu.getBoundingClientRect();
        var iWindowWidth = window.innerWidth || 1200;
        var iWindowHeight = window.innerHeight || 800;
        if (rectMenu.right > iWindowWidth) {
            elMenu.style.left = Math.max(
                0, iWindowWidth - rectMenu.width - 8) + "px";
        }
        if (rectMenu.bottom > iWindowHeight) {
            elMenu.style.top = Math.max(
                0, iWindowHeight - rectMenu.height - 8) + "px";
        }
    }

    function fnOpen(dictRow, iLeft, iTop, elReturnFocus) {
        var elMenu = document.getElementById(_S_MENU_ID);
        if (!elMenu) return;
        fnDismiss();
        _elReturnFocus = elReturnFocus || null;
        var elList = elMenu.querySelector(".picklist-items");
        elList.textContent = "";
        flistBuildItems(dictRow).forEach(function (dictItem) {
            elList.appendChild(_felBuildItemButton(dictItem));
        });
        _fnPlaceMenu(elMenu, iLeft, iTop);
        elList.querySelector(".picklist-item").focus();
    }

    function fnDismiss() {
        var elMenu = document.getElementById(_S_MENU_ID);
        if (!elMenu || elMenu.hidden) return;
        elMenu.hidden = true;
        elMenu.querySelector(".picklist-items").textContent = "";
        if (_elReturnFocus && document.body.contains(_elReturnFocus)) {
            _elReturnFocus.focus();
        }
        _elReturnFocus = null;
    }

    function fbIsOpen() {
        var elMenu = document.getElementById(_S_MENU_ID);
        return Boolean(elMenu) && !elMenu.hidden;
    }

    function _fnDismissOnPressOutside(event) {
        var elMenu = document.getElementById(_S_MENU_ID);
        if (elMenu && !elMenu.contains(event.target)) fnDismiss();
    }

    function _fnDismissOnEscape(event) {
        if (event.key === "Escape" && fbIsOpen()) fnDismiss();
    }

    function fnBindDismissHandlers() {
        /* mousedown, not click, for "outside": a right-click on another
           row fires mousedown before its contextmenu event, so the old
           menu is gone before the new one opens, and a click-based
           dismissal would close the new menu in the same gesture. */
        if (_bDismissHandlersBound) return;
        _bDismissHandlersBound = true;
        document.addEventListener("mousedown", _fnDismissOnPressOutside);
        document.addEventListener("keydown", _fnDismissOnEscape);
        window.addEventListener("resize", fnDismiss);
    }

    document.addEventListener("DOMContentLoaded", fnBindDismissHandlers);

    return {
        flistBuildItems: flistBuildItems,
        fnOpen: fnOpen,
        fnDismiss: fnDismiss,
        fbIsOpen: fbIsOpen,
    };
})();
