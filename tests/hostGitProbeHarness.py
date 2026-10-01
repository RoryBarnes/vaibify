"""Shared harness for the host-side git safety probes.

Builds a throwaway repository that carries ONE repository-local
mechanism by which git can run a program, then reports which
mechanisms actually ran. Every mechanism writes a marker file when it
runs, and every marker lives under the caller's temporary directory,
so a probe can never touch anything else: the "programs" are tiny
shell scripts this module writes itself.

Nothing here decides what vaibify should do about an exposure. It
only measures what the current code does, so the tests built on it
document the current behavior and fail when that behavior changes.
"""

import contextlib
import http.server
import os
import re
import shlex
import shutil
import stat
import subprocess
import threading

S_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
S_TRACKED_FILE = "data.txt"
S_MANIFEST_FILE = "MANIFEST.sha256"
S_FILTER_NAME = "probe"

T_LOCAL_MECHANISMS = (
    "fsmonitor", "filterClean", "filterSmudge", "filterProcess",
    "filterCleanViaInfoAttributes", "filterCleanViaAttributesFile",
    "textconv", "externalDiff", "pager", "hookPreCommit",
    "hookPostIndexChange", "hookPostMerge", "hooksPathConfig", "includePath",
    "includeIfGitdir", "gpgProgram",
)
T_NETWORK_MECHANISMS = (
    "insteadOfExt", "sshCommand", "credentialHelper", "gitProxy",
)
T_ALL_MECHANISMS = T_LOCAL_MECHANISMS + T_NETWORK_MECHANISMS


def ftReadGitVersion():
    """Return the local git version as a tuple of ints, e.g. (2, 50, 1)."""
    sText = subprocess.run(
        ["git", "--version"], capture_output=True, text=True,
    ).stdout
    listNumbers = re.findall(r"\d+", sText)[:3]
    return tuple(int(sNumber) for sNumber in listNumbers)


def fsDescribeGitVersion():
    """Return the local git version as dotted text."""
    return ".".join(str(iPart) for iPart in ftReadGitVersion())


def fdictBuildIsolatedEnvironment(sHome):
    """Return an environment in which no ambient git config applies."""
    dictEnvironment = {
        sKey: sValue for sKey, sValue in os.environ.items()
        if not sKey.startswith("GIT_")
    }
    dictEnvironment.update({
        "HOME": sHome,
        "XDG_CONFIG_HOME": os.path.join(sHome, ".config"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Probe", "GIT_AUTHOR_EMAIL": "probe@invalid",
        "GIT_COMMITTER_NAME": "Probe",
        "GIT_COMMITTER_EMAIL": "probe@invalid",
    })
    return dictEnvironment


def fnRunSetupGit(listArguments, sCwd, dictEnvironment):
    """Run one git command while building a probe repository."""
    subprocess.run(
        ["git", *listArguments], cwd=sCwd, env=dictEnvironment,
        capture_output=True, text=True, check=True,
    )


def fsMarkerPath(sMarkerDirectory, sMechanism):
    """Return the marker file one mechanism writes when it runs."""
    return os.path.join(sMarkerDirectory, sMechanism)


def fsWriteProbeScript(sScriptDirectory, sMarkerDirectory, sMechanism, sKind):
    """Write an executable script that records that it ran; return its path.

    ``sKind`` selects what the script does after writing the marker:
    ``plain`` exits 0, ``passthrough`` copies stdin to stdout (a
    filter or a pager), ``textconv`` prints the file it is handed, and
    ``fail`` exits 1 (a transport or an ssh that must not go on).
    """
    dictTail = {
        "plain": "exit 0\n",
        "passthrough": "cat\n",
        "textconv": 'if [ -f "$1" ]; then cat "$1"; fi\n',
        "fail": "exit 1\n",
    }
    sScriptPath = os.path.join(sScriptDirectory, sMechanism + ".sh")
    sMarker = fsMarkerPath(sMarkerDirectory, sMechanism)
    with open(sScriptPath, "w") as fileScript:
        fileScript.write(
            "#!/bin/sh\nprintf ran >> " + shlex.quote(sMarker) + "\n"
            + dictTail[sKind]
        )
    os.chmod(sScriptPath, os.stat(sScriptPath).st_mode | stat.S_IXUSR)
    return sScriptPath


def fnAppendText(sPath, sText):
    """Append text to a file, creating it when absent."""
    with open(sPath, "a") as fileTarget:
        fileTarget.write(sText)


def fnInstallFilter(sRepo, sScript, sAttributesFile, sKeyBody):
    """Select the probe filter through one attributes file and config."""
    fnAppendText(sAttributesFile, "* filter=probe diff=probe\n")
    fnAppendText(
        os.path.join(sRepo, ".git", "config"),
        '[filter "probe"]\n' + sKeyBody.replace("SCRIPT", sScript),
    )


def fnInstallMechanism(sRepo, sMechanism, dictPaths):
    """Install one mechanism into a repository's own .git directory."""
    sConfig = os.path.join(sRepo, ".git", "config")
    fnInstaller = DICT_INSTALLERS[sMechanism]
    fnInstaller(sRepo, sConfig, dictPaths)


def _fsScript(dictPaths, sMechanism, sKind):
    """Write the probe script for a mechanism using the shared paths."""
    return fsWriteProbeScript(
        dictPaths["sScripts"], dictPaths["sMarkers"], sMechanism, sKind,
    )


def _fnInstallFsmonitor(sRepo, sConfig, dictPaths):
    fnAppendText(sConfig, "[core]\n\tfsmonitor = "
                 + _fsScript(dictPaths, "fsmonitor", "plain") + "\n")


def _fnInstallFilterClean(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "filterClean", "passthrough")
    fnInstallFilter(sRepo, sScript, os.path.join(sRepo, ".gitattributes"),
                    "\tclean = SCRIPT\n")


def _fnInstallFilterSmudge(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "filterSmudge", "passthrough")
    fnInstallFilter(sRepo, sScript, os.path.join(sRepo, ".gitattributes"),
                    "\tsmudge = SCRIPT\n")


def _fnInstallFilterProcess(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "filterProcess", "fail")
    fnInstallFilter(sRepo, sScript, os.path.join(sRepo, ".gitattributes"),
                    "\tprocess = SCRIPT\n")


def _fnInstallFilterViaInfo(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "filterCleanViaInfoAttributes",
                        "passthrough")
    sInfo = os.path.join(sRepo, ".git", "info")
    os.makedirs(sInfo, exist_ok=True)
    fnInstallFilter(sRepo, sScript, os.path.join(sInfo, "attributes"),
                    "\tclean = SCRIPT\n")


def _fnInstallFilterViaAttributesFile(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "filterCleanViaAttributesFile",
                        "passthrough")
    sAttributes = os.path.join(sRepo, ".git", "probeAttributes")
    fnInstallFilter(sRepo, sScript, sAttributes, "\tclean = SCRIPT\n")
    fnAppendText(sConfig, "[core]\n\tattributesFile = " + sAttributes + "\n")


def _fnInstallTextconv(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "textconv", "textconv")
    fnAppendText(os.path.join(sRepo, ".gitattributes"), "* diff=probe\n")
    fnAppendText(sConfig, '[diff "probe"]\n\ttextconv = ' + sScript + "\n")


def _fnInstallExternalDiff(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "externalDiff", "plain")
    fnAppendText(sConfig, "[diff]\n\texternal = " + sScript + "\n")


def _fnInstallPager(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "pager", "passthrough")
    fnAppendText(sConfig, "[core]\n\tpager = " + sScript + "\n"
                 "[pager]\n\tdiff = " + sScript + "\n\tstatus = " + sScript
                 + "\n\tlog = " + sScript + "\n\tshow = " + sScript + "\n")


def _fnInstallHook(sRepo, sConfig, dictPaths, sMechanism, sHookName):
    sScript = _fsScript(dictPaths, sMechanism, "plain")
    sHooks = os.path.join(sRepo, ".git", "hooks")
    os.makedirs(sHooks, exist_ok=True)
    shutil.copy(sScript, os.path.join(sHooks, sHookName))


def _fnInstallHookPreCommit(sRepo, sConfig, dictPaths):
    _fnInstallHook(sRepo, sConfig, dictPaths, "hookPreCommit", "pre-commit")


def _fnInstallHookPostIndexChange(sRepo, sConfig, dictPaths):
    _fnInstallHook(sRepo, sConfig, dictPaths, "hookPostIndexChange",
                   "post-index-change")


def _fnInstallHookPostMerge(sRepo, sConfig, dictPaths):
    _fnInstallHook(sRepo, sConfig, dictPaths, "hookPostMerge", "post-merge")


def _fnInstallHooksPathConfig(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "hooksPathConfig", "plain")
    sHooks = os.path.join(dictPaths["sScripts"], "redirectedHooks")
    os.makedirs(sHooks, exist_ok=True)
    for sHookName in ("pre-commit", "post-index-change", "post-merge"):
        shutil.copy(sScript, os.path.join(sHooks, sHookName))
    fnAppendText(sConfig, "[core]\n\thooksPath = " + sHooks + "\n")


def _fnInstallIncludePath(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "includePath", "plain")
    sIncluded = os.path.join(sRepo, ".git", "included.cfg")
    fnAppendText(sIncluded, "[core]\n\tfsmonitor = " + sScript + "\n")
    fnAppendText(sConfig, "[include]\n\tpath = included.cfg\n")


def _fnInstallIncludeIfGitdir(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "includeIfGitdir", "plain")
    sIncluded = os.path.join(sRepo, ".git", "included.cfg")
    fnAppendText(sIncluded, "[core]\n\tfsmonitor = " + sScript + "\n")
    fnAppendText(sConfig,
                 '[includeIf "gitdir:/"]\n\tpath = included.cfg\n')


def _fnInstallGpgProgram(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "gpgProgram", "fail")
    fnAppendText(sConfig, "[gpg]\n\tprogram = " + sScript + "\n"
                 "[commit]\n\tgpgSign = true\n")


def _fnInstallInsteadOfExt(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "insteadOfExt", "fail")
    fnAppendText(sConfig, '[url "ext::' + sScript + ' %S"]\n'
                 "\tinsteadOf = probealias:\n"
                 '[remote "origin"]\n\turl = probealias:repo\n'
                 "\tfetch = +refs/heads/*:refs/remotes/origin/*\n")


def _fnInstallSshCommand(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "sshCommand", "fail")
    fnAppendText(sConfig, "[core]\n\tsshCommand = " + sScript + "\n"
                 '[remote "origin"]\n\turl = ssh://probe.invalid/repo.git\n'
                 "\tfetch = +refs/heads/*:refs/remotes/origin/*\n")


def _fnInstallCredentialHelper(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "credentialHelper", "plain")
    fnAppendText(sConfig, "[credential]\n\thelper = " + sScript + "\n"
                 '[remote "origin"]\n\turl = '
                 + dictPaths["sHttpUrl"] + "\n"
                 "\tfetch = +refs/heads/*:refs/remotes/origin/*\n")


def _fnInstallGitProxy(sRepo, sConfig, dictPaths):
    sScript = _fsScript(dictPaths, "gitProxy", "fail")
    fnAppendText(sConfig, "[core]\n\tgitProxy = " + sScript + "\n"
                 '[remote "origin"]\n\turl = git://probe.invalid/repo.git\n'
                 "\tfetch = +refs/heads/*:refs/remotes/origin/*\n")


DICT_INSTALLERS = {
    "fsmonitor": _fnInstallFsmonitor,
    "filterClean": _fnInstallFilterClean,
    "filterSmudge": _fnInstallFilterSmudge,
    "filterProcess": _fnInstallFilterProcess,
    "filterCleanViaInfoAttributes": _fnInstallFilterViaInfo,
    "filterCleanViaAttributesFile": _fnInstallFilterViaAttributesFile,
    "textconv": _fnInstallTextconv,
    "externalDiff": _fnInstallExternalDiff,
    "pager": _fnInstallPager,
    "hookPreCommit": _fnInstallHookPreCommit,
    "hookPostIndexChange": _fnInstallHookPostIndexChange,
    "hookPostMerge": _fnInstallHookPostMerge,
    "hooksPathConfig": _fnInstallHooksPathConfig,
    "includePath": _fnInstallIncludePath,
    "includeIfGitdir": _fnInstallIncludeIfGitdir,
    "gpgProgram": _fnInstallGpgProgram,
    "insteadOfExt": _fnInstallInsteadOfExt,
    "sshCommand": _fnInstallSshCommand,
    "credentialHelper": _fnInstallCredentialHelper,
    "gitProxy": _fnInstallGitProxy,
}


def fsBuildProbeRepository(sRepo, sMechanism, dictPaths, dictEnvironment):
    """Create a committed repository, then install ONE mechanism into it.

    The commit happens first, with nothing installed, so building the
    repository can never run the mechanism under test. A tracked file,
    a manifest file and a ``.gitattributes`` are committed; the
    mechanism's config, hooks or attributes arrive afterwards, the way
    a downloaded ``.git`` directory or an in-container agent would
    leave them.
    """
    os.makedirs(sRepo)
    fnRunSetupGit(["init", "-q", "-b", "main"], sRepo, dictEnvironment)
    for sName in (S_TRACKED_FILE, S_MANIFEST_FILE):
        with open(os.path.join(sRepo, sName), "w") as fileTracked:
            fileTracked.write("alpha\n")
    fnAppendText(os.path.join(sRepo, ".gitattributes"), "# placeholder\n")
    fnRunSetupGit(["add", "-A"], sRepo, dictEnvironment)
    fnRunSetupGit(["commit", "-q", "-m", "initial"], sRepo, dictEnvironment)
    fnRecordUpstreamAhead(sRepo, dictEnvironment)
    if sMechanism not in T_NETWORK_MECHANISMS:
        fnRunSetupGit(["remote", "add", "origin", "/nonexistent/repo.git"],
                      sRepo, dictEnvironment)
    fnInstallMechanism(sRepo, sMechanism, dictPaths)
    fnCommitAttributesLikeADownloadedRepository(sRepo, dictEnvironment)
    return sRepo


def fnCommitAttributesLikeADownloadedRepository(sRepo, dictEnvironment):
    """Commit the attributes file, as a published repository would carry it.

    A repository fetched from elsewhere ships ``.gitattributes`` in its
    history, so merge and checkout machinery that reads attributes from
    the index sees the filter. The commit is allowed to fail (a failing
    filter or signing program can refuse it); the probes read markers
    only after clearing the ones this step wrote.
    """
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c",
         "commit.gpgSign=false", "commit", "-q", "-a", "-m", "attributes"],
        cwd=sRepo, env=dictEnvironment, capture_output=True,
    )


def fnRecordUpstreamAhead(sRepo, dictEnvironment):
    """Leave a remote-tracking ref one commit ahead of main, with no network.

    The merge and pull code paths need an upstream that is ahead; a
    local ref plus the branch's tracking config supplies one without
    contacting anything.
    """
    fnRunSetupGit(["checkout", "-q", "-b", "ahead"], sRepo, dictEnvironment)
    with open(os.path.join(sRepo, S_TRACKED_FILE), "w") as fileTracked:
        fileTracked.write("gamma\n")
    fnRunSetupGit(["commit", "-q", "-a", "-m", "ahead"], sRepo,
                  dictEnvironment)
    sAheadCommit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=sRepo, env=dictEnvironment,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    fnRunSetupGit(["update-ref", "refs/remotes/origin/main", sAheadCommit],
                  sRepo, dictEnvironment)
    fnRunSetupGit(["checkout", "-q", "main"], sRepo, dictEnvironment)
    fnRunSetupGit(["branch", "-q", "-D", "ahead"], sRepo, dictEnvironment)
    fnRunSetupGit(["config", "branch.main.remote", "origin"], sRepo,
                  dictEnvironment)
    fnRunSetupGit(["config", "branch.main.merge", "refs/heads/main"], sRepo,
                  dictEnvironment)


def fnMakeWorktreeStatDirty(sRepo):
    """Change the stat data of tracked files so git must re-read them."""
    for sName in (S_TRACKED_FILE, S_MANIFEST_FILE):
        sPath = os.path.join(sRepo, sName)
        os.utime(sPath, (1_000_000_000, 1_000_000_000))


def fnModifyTrackedFile(sRepo):
    """Give the tracked file new content so it differs from HEAD."""
    with open(os.path.join(sRepo, S_TRACKED_FILE), "w") as fileTracked:
        fileTracked.write("beta\n")


def fnClearMarkers(sMarkerDirectory):
    """Remove every marker so the next run starts from silence."""
    for sName in os.listdir(sMarkerDirectory):
        os.remove(os.path.join(sMarkerDirectory, sName))


def flistReadTriggeredMechanisms(sMarkerDirectory):
    """Return the sorted names of the mechanisms that wrote a marker."""
    return sorted(os.listdir(sMarkerDirectory))


class RejectingHttpServer:
    """A loopback-only HTTP server that answers 401 to every request.

    git asks its credential helpers for an answer after a 401, and
    that is the only way to make a credential helper run without
    leaving the machine. It binds 127.0.0.1 on an ephemeral port.
    """

    def __init__(self):
        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="probe"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = do_GET

            def log_message(self, *listIgnored):
                return

        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True,
        )

    def fsUrl(self):
        """Return the base URL of a repository this server rejects."""
        return "http://127.0.0.1:%d/repo.git" % self.server.server_port

    def fnStart(self):
        """Begin serving."""
        self.thread.start()

    def fnStop(self):
        """Stop serving and release the port."""
        self.server.shutdown()
        self.server.server_close()


@contextlib.contextmanager
def contextRejectingServer():
    """Yield a started RejectingHttpServer and stop it afterwards."""
    serverRejecting = RejectingHttpServer()
    serverRejecting.fnStart()
    try:
        yield serverRejecting
    finally:
        serverRejecting.fnStop()
