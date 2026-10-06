/* Vaibify — Reads what was dropped onto the Files panel as a flat plan.

   A drop carries files, folders, or both. Folders reach the page only
   as FileSystemEntry objects, and two facts about them shape this
   module:

   - The entries must be taken from the DataTransfer SYNCHRONOUSLY, in
     the drop handler: the DataTransferItem objects stop answering once
     the handler yields. The entries themselves outlive it, so the walk
     below may be asynchronous.
   - A directory reader hands back a fragment of its directory per
     readEntries call (Chromium: at most 100), and the end is an EMPTY
     batch, so one call silently drops everything past the first
     hundred files of a large folder.

   Everything here is a pure function of entry-like objects (isFile,
   isDirectory, name, file(), createReader()), so the same code is
   driven by real entries in a browser and by hand-built ones in a test.
   Which names the server refuses is the server's decision, and the
   panel renders its verdict; the two names skipped here are skipped
   only so that a repository's internals are not turned into thousands
   of refusals the researcher has to read. */

var VaibifyFileDropWalker = (function () {
    "use strict";

    var _LIST_SKIPPED_NAMES = [".git", ".vaibify"];

    function flistCaptureEntries(dataTransfer) {
        /* Synchronous on purpose; see the module comment. A browser
           without webkitGetAsEntry (or an item it will not describe as
           an entry) is represented by its File, wrapped to look like a
           file entry, so the walker has one shape to read. */
        var listItems = Array.prototype.slice.call(
            dataTransfer.items || []);
        if (listItems.length === 0) {
            return Array.prototype.slice.call(dataTransfer.files || [])
                .map(_fentryWrapFile);
        }
        var listEntries = [];
        listItems.forEach(function (item) {
            if (item.kind !== "file") return;
            var entry = item.webkitGetAsEntry
                ? item.webkitGetAsEntry() : null;
            /* A folder is read through its entry; its File is a
               zero-byte stand-in. A plain file keeps the File taken
               HERE, because a second, asynchronous entry.file() call
               is one more thing that can fail after the handler has
               returned (WebKit refuses it for a file the page put into
               the DataTransfer itself). */
            var bFolder = Boolean(entry && entry.isDirectory);
            var file = bFolder ? null : item.getAsFile();
            if (file) listEntries.push(_fentryWrapFile(file));
            else if (entry) listEntries.push(entry);
        });
        return listEntries;
    }

    function _fentryWrapFile(file) {
        return {
            isFile: true,
            isDirectory: false,
            name: file.name,
            file: function (fnResolve) { fnResolve(file); },
        };
    }

    function _fpromiseReadBatch(readerDirectory) {
        return new Promise(function (fnResolve, fnReject) {
            readerDirectory.readEntries(fnResolve, fnReject);
        });
    }

    async function flistReadAllEntries(readerDirectory) {
        var listAll = [];
        var listBatch = await _fpromiseReadBatch(readerDirectory);
        while (listBatch.length > 0) {
            Array.prototype.push.apply(listAll, listBatch);
            listBatch = await _fpromiseReadBatch(readerDirectory);
        }
        return listAll;
    }

    function _fpromiseFileOfEntry(entryFile) {
        return new Promise(function (fnResolve, fnReject) {
            entryFile.file(fnResolve, fnReject);
        });
    }

    function _fsJoinPath(sParentPath, sName) {
        return sParentPath ? sParentPath + "/" + sName : sName;
    }

    function _fiCountPlanned(dictPlan) {
        return dictPlan.listFileItems.length +
            dictPlan.listEmptyDirectories.length;
    }

    async function _fnPlanFile(entryFile, sParentPath, dictPlan) {
        try {
            var file = await _fpromiseFileOfEntry(entryFile);
            dictPlan.listFileItems.push({
                sRelativePath: sParentPath,
                sName: entryFile.name,
                file: file,
            });
        } catch (error) {
            dictPlan.listUnreadable.push({
                sPath: _fsJoinPath(sParentPath, entryFile.name),
                sReason: (error && error.message) || String(error),
            });
        }
    }

    async function _fnPlanDirectory(entryDirectory, sParentPath, dictPlan) {
        var sOwnPath = _fsJoinPath(sParentPath, entryDirectory.name);
        var listChildren;
        try {
            listChildren = await flistReadAllEntries(
                entryDirectory.createReader());
        } catch (error) {
            dictPlan.listUnreadable.push({
                sPath: sOwnPath,
                sReason: (error && error.message) || String(error),
            });
            return;
        }
        var iPlannedBefore = _fiCountPlanned(dictPlan);
        for (var iChild = 0; iChild < listChildren.length; iChild++) {
            await _fnPlanEntry(listChildren[iChild], sOwnPath, dictPlan);
        }
        if (_fiCountPlanned(dictPlan) === iPlannedBefore) {
            dictPlan.listEmptyDirectories.push({
                sRelativePath: sParentPath,
                sName: entryDirectory.name,
            });
        }
    }

    async function _fnPlanEntry(entry, sParentPath, dictPlan) {
        if (_LIST_SKIPPED_NAMES.indexOf(entry.name) !== -1) {
            dictPlan.listSkippedPaths.push(
                _fsJoinPath(sParentPath, entry.name));
        } else if (entry.isDirectory) {
            await _fnPlanDirectory(entry, sParentPath, dictPlan);
        } else if (entry.isFile) {
            await _fnPlanFile(entry, sParentPath, dictPlan);
        }
    }

    async function fdictWalkEntries(listEntries) {
        /* The flat plan for a drop. listFileItems carry the directory
           part (sRelativePath, "/"-separated, no leading slash), the
           leaf name and the File; listEmptyDirectories name folders
           that held nothing to upload (a folder holding only skipped
           subtrees is one of them, because the researcher dropped it
           and expects it to exist); listSkippedPaths counts the
           subtrees left behind; listUnreadable names what the browser
           would not hand over, with its reason. */
        var dictPlan = {
            listFileItems: [],
            listEmptyDirectories: [],
            listSkippedPaths: [],
            listUnreadable: [],
        };
        for (var iEntry = 0; iEntry < listEntries.length; iEntry++) {
            await _fnPlanEntry(listEntries[iEntry], "", dictPlan);
        }
        return dictPlan;
    }

    return {
        flistCaptureEntries: flistCaptureEntries,
        flistReadAllEntries: flistReadAllEntries,
        fdictWalkEntries: fdictWalkEntries,
    };
})();
