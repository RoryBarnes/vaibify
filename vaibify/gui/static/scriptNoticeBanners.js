/* Vaibify — what every notice banner above the steps shares: a "?" that
   says why the researcher is seeing it, and a dismissal that hides what
   was read rather than what comes next.

   A researcher must never meet a notice they cannot clear, and a notice
   worth showing is worth explaining (researcher-reported, 2026-11-24).
   The "?" opens the help popup with the banner's own text. The
   dismissal holds only while two things are unchanged: the view it was
   made in (the view generation advances on every project open, so the
   banner returns on the next open) and the server payload it was made
   over (so a new refused host, a changed proxy state or a different set
   of notices shows the banner again at once). The content key is the
   server's payload itself, serialized, never a predicate of the page's
   own: the banner hides exactly what the server said, nothing more. */

var VaibifyNoticeBanners = (function () {
    "use strict";

    var _S_HELP_CLASS = "notice-banner-help";
    var _S_DISMISS_SELECTOR =
        ".build-warnings-banner-dismiss, .drift-banner-dismiss";
    var _S_DISMISS_LABEL =
        "Hide this notice until it changes or the project is next opened";

    /* Drafts, for the researcher to rewrite. Keyed by the banner's
       element id (the memory banners by their holder's). */
    var _DICT_HELP_TEXT = {
        driftBanner:
            "You are seeing this because the project's repository in " +
            "this container and its copy on the remote (origin) have " +
            "moved apart: the container is behind, has uncommitted " +
            "files of its own, or both sides have new commits. The " +
            "button beside the message brings them back together. " +
            "The × hides this until the container is next opened; " +
            "the difference stays until it is resolved.",
        configurationDriftBanner:
            "You are seeing this because this project's settings " +
            "changed after its container was built or started, so the " +
            "running container does not have them yet. Each line names " +
            "what differs and what applies it, usually a rebuild or a " +
            "restart. Hiding this changes nothing in the container; it " +
            "returns the next time the container is opened while the " +
            "difference remains.",
        pinnedEnvironmentDriftBanner:
            "You are seeing this because this container runs the " +
            "author's pinned image, and its settings in vaibify.yml " +
            "differ from the ones that image was built with. The image " +
            "is not out of date, and rebuilding would replace the " +
            "author's environment, so each line says what differs " +
            "instead.",
        buildWarningsBanner:
            "You are seeing this because the container reported " +
            "problems while it was last starting, such as a repository " +
            "it could not clone, a package it could not install, or a " +
            "secret this computer could not supply. The container " +
            "started anyway. Each line names a problem: fix its cause, " +
            "then restart the container.",
        networkBanner:
            "This row describes the container's network: the mode " +
            "vaibify.yml names (web, allowlist, isolated or open), " +
            "whether the proxy that enforces web and allowlist modes " +
            "is running, and any connections the proxy refused. It " +
            "appears by itself only when something needs attention: " +
            "an open container, a container created with a different " +
            "mode than the file names, a file that names no mode, a " +
            "proxy that is down or could not be read, or refused " +
            "hosts. \"Allow host\" adds a refused host to the allowed " +
            "list, effective when the container next starts. The " +
            "View menu's Network Status shows this row at any time.",
        zenodoTokenBanner:
            "You are seeing this because a Zenodo token was found " +
            "inside this container and moved to this computer's " +
            "keychain, where an agent in the container cannot read it. " +
            "An agent can write to the container, so vaibify cannot " +
            "tell whether the token is yours: it is not used until you " +
            "confirm it here, and declining removes it.",
        remoteBindingBanner:
            "Before vaibify spends your Overleaf or Zenodo token, it " +
            "checks that the target (an Overleaf project or a Zenodo " +
            "record) is one you confirmed on this computer, because an " +
            "agent in the container can edit project.json. A line " +
            "appears here when project.json names a target that " +
            "differs from the one you confirmed, or when Auto Archive " +
            "would send to a target nobody has confirmed. \"Review and " +
            "confirm…\" sets what project.json names beside what " +
            "you confirmed and what your token can see. The View " +
            "menu's Remote Bindings shows this at any time.",
        resourceLimitDriftBanner:
            "You are seeing this because the CPU or memory limit this " +
            "container is running with differs from the one in its " +
            "settings. A limit takes effect when the container starts, " +
            "so restarting the container applies the settings.",
        resourceAdvisoryBanner:
            "You are seeing this because vaibify estimates that an AI " +
            "agent in this container may need more memory than the " +
            "project's limit allows, and a process that runs out of " +
            "memory is stopped. Raise the memory limit in the " +
            "container's settings, then restart it. This is advice, " +
            "not an error.",
        memoryIncidentBanners:
            "You are seeing this because a process in this container " +
            "used more memory than the container's limit, and the " +
            "system stopped it. Raising the memory limit in the " +
            "container's settings, then restarting, gives processes " +
            "more room. The × hides this incident; a new incident " +
            "shows its own notice.",
    };

    var _dictDismissals = {};

    function fsLookUpHelpText(sBannerKey) {
        return _DICT_HELP_TEXT[sBannerKey] || "";
    }

    function felementBuildHelpButton(sBannerKey) {
        var elButton = document.createElement("button");
        elButton.type = "button";
        elButton.className = "help-icon " + _S_HELP_CLASS;
        elButton.dataset.helpFor = sBannerKey;
        elButton.title = fsLookUpHelpText(sBannerKey);
        elButton.setAttribute("aria-label", "Why am I seeing this?");
        elButton.textContent = "?";
        elButton.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            VaibifySyncManager.fnShowHelpPopup(fsLookUpHelpText(sBannerKey));
        });
        return elButton;
    }

    function fnAttachHelpButton(elBanner, sBannerKey) {
        /* Beside the banner's x when it has one, else at the end of its
           header. A banner rebuilt from markup gets one "?" per build. */
        if (!elBanner || elBanner.querySelector("." + _S_HELP_CLASS)) return;
        var elHelp = felementBuildHelpButton(sBannerKey);
        var elDismiss = elBanner.querySelector(_S_DISMISS_SELECTOR);
        if (elDismiss) {
            elDismiss.parentNode.insertBefore(elHelp, elDismiss);
            return;
        }
        var elHeader = elBanner.querySelector(".build-warnings-banner-header");
        (elHeader || elBanner).appendChild(elHelp);
    }

    function fsBuildContentKey(jsonPayload) {
        return JSON.stringify(jsonPayload === undefined ? null : jsonPayload);
    }

    function fbIsDismissed(sBannerKey, sContentKey) {
        var dictDismissal = _dictDismissals[sBannerKey];
        if (!dictDismissal) return false;
        if (dictDismissal.iViewGeneration === VaibifyApp.fiGetViewGeneration() &&
                dictDismissal.sContentKey === sContentKey) {
            return true;
        }
        delete _dictDismissals[sBannerKey];
        return false;
    }

    function fnForgetDismissal(sBannerKey) {
        delete _dictDismissals[sBannerKey];
    }

    function felementBuildDismissButton(sBannerKey, sContentKey, fnHide) {
        /* The x that remembers what it hid: this payload, in this view. */
        var elDismiss = document.createElement("button");
        elDismiss.type = "button";
        elDismiss.className = "build-warnings-banner-dismiss";
        elDismiss.setAttribute("aria-label", _S_DISMISS_LABEL);
        elDismiss.textContent = "×";
        elDismiss.addEventListener("click", function () {
            _dictDismissals[sBannerKey] = {
                sContentKey: sContentKey,
                iViewGeneration: VaibifyApp.fiGetViewGeneration(),
            };
            fnHide();
        });
        return elDismiss;
    }

    return {
        fsLookUpHelpText: fsLookUpHelpText,
        felementBuildHelpButton: felementBuildHelpButton,
        fnAttachHelpButton: fnAttachHelpButton,
        fsBuildContentKey: fsBuildContentKey,
        fbIsDismissed: fbIsDismissed,
        fnForgetDismissal: fnForgetDismissal,
        felementBuildDismissButton: felementBuildDismissButton,
    };
})();
