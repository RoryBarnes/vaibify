"""The agent chats a researcher can return to, found in the CLIs' own transcripts.

"Choose chats" lists the conversations launched inside this project, and
"Latest" confirms that each saved chat still has a transcript. Both come
from one program run where the transcripts live -- in the container, or
on the host for a host project -- so that only a short summary crosses
back, never a transcript:

- the transcripts are found and attributed to a launch directory by the
  SAME rules Prompt Record uses
  (``promptRecordManager.S_TRANSCRIPT_LAUNCH_RULES_PROGRAM``), so the two
  can never disagree about which sessions belong to a project;
- for each transcript of this project, or named by a saved chat, the
  program reads the head for the session id and the first prompt
  (truncated) and the last 256 KiB for the permission mode the
  transcript last recorded (Claude's ``permissionMode``; Codex's
  approval policy and sandbox);
- it reads the permission DEFAULTS the providers' config files set:
  Claude's ``permissions.defaultMode`` and Codex's top-level
  ``approval_policy`` and ``sandbox_mode``. Gemini's settings cannot
  enable yolo, so nothing is read for it.

The program is constant text. Its only inputs are arguments the hub
validated: the project repository (a normalized absolute path) and
``provider:uuid`` pairs. It runs through the one place vaibify runs a
program over the transcripts
(``promptRecordManager.ftRunTranscriptProgram``), under a mode-b
carrier like the Prompt Record capture.

Everything here was written by the researcher's uid, which an
in-container agent shares, so a listed chat is DISCOVERED conversation
data, never verified: an agent can fabricate a transcript and its first
prompt. The modal says so, and confirming there is the mitigation.
"""

__all__ = [
    "I_MAXIMUM_CATALOG_ENTRIES",
    "fdictListAgentChats",
    "flistCatalogArguments",
]

import json
import posixpath

from . import promptRecordManager
from .agentChatLifecycle import SET_AGENT_PROVIDERS, fbIsSessionId
from .promptRecordManager import S_TRANSCRIPT_LAUNCH_RULES_PROGRAM

I_MAXIMUM_CATALOG_ENTRIES = 200
I_MAXIMUM_REQUESTED_CHATS = 64

_S_CATALOG_PROGRAM = S_TRANSCRIPT_LAUNCH_RULES_PROGRAM + r'''
import re
sRepo = os.path.normpath(sys.argv[1])
setRequested = set(sys.argv[2:])
reUuid = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
I_PROMPT = 240
def fbInRepo(sDirectory):
    if not sDirectory:
        return False
    sDirectory = os.path.normpath(sDirectory)
    return sDirectory == sRepo or sDirectory.startswith(sRepo.rstrip('/') + '/')
def fdictLine(baLine):
    try:
        dictLine = json.loads(baLine.decode('utf-8', 'replace'))
    except ValueError:
        return None
    return dictLine if isinstance(dictLine, dict) else None
def fsText(valueContent):
    if isinstance(valueContent, str):
        return valueContent
    if isinstance(valueContent, list):
        for dictPart in valueContent:
            if isinstance(dictPart, dict) and isinstance(dictPart.get('text'), str):
                return dictPart['text']
    return ''
def fsClaudePrompt(dictLine):
    if dictLine.get('type') != 'user' or dictLine.get('isMeta'):
        return ''
    dictMessage = dictLine.get('message')
    sText = fsText(dictMessage.get('content')) if isinstance(dictMessage, dict) else ''
    return '' if sText.lstrip().startswith('<') else sText
def fsCodexPrompt(dictLine):
    dictPayload = dictLine.get('payload')
    if (dictLine.get('type') == 'event_msg' and isinstance(dictPayload, dict)
            and dictPayload.get('type') == 'user_message'):
        return str(dictPayload.get('message') or '')
    return ''
def fsGeminiPrompt(dictLine):
    if dictLine.get('type') != 'user':
        return ''
    sText = fsText(dictLine.get('content'))
    return '' if sText.lstrip().startswith('<session_context>') else sText
DICT_PROMPT = {'claude': fsClaudePrompt, 'codex': fsCodexPrompt, 'gemini': fsGeminiPrompt}
def ftReadHead(sProvider, sPath):
    sSessionId, sPrompt = '', ''
    if sProvider == 'claude':
        sSessionId = os.path.basename(sPath)[:-len('.jsonl')]
    elif sProvider == 'codex':
        listIds = reUuid.findall(os.path.basename(sPath))
        sSessionId = listIds[-1] if listIds else ''
    with open(sPath, 'rb') as fileTranscript:
        for iLine, baLine in enumerate(fileTranscript):
            if iLine >= 400 or (sSessionId and sPrompt):
                break
            dictLine = fdictLine(baLine)
            if dictLine is None:
                continue
            if sProvider == 'gemini' and not sSessionId:
                sSessionId = str(dictLine.get('sessionId') or '')
            if not sPrompt:
                sPrompt = DICT_PROMPT[sProvider](dictLine)
    return sSessionId.lower(), ' '.join(sPrompt.split())[:I_PROMPT]
def fsReadTailMode(sProvider, sPath, iSize):
    if sProvider == 'gemini':
        return ''
    with open(sPath, 'rb') as fileTranscript:
        fileTranscript.seek(max(0, iSize - 262144))
        listLines = fileTranscript.read().splitlines()
    for baLine in reversed(listLines):
        dictLine = fdictLine(baLine)
        if dictLine is None:
            continue
        if sProvider == 'claude' and isinstance(dictLine.get('permissionMode'), str):
            return dictLine['permissionMode']
        dictPayload = dictLine.get('payload')
        if (sProvider == 'codex' and dictLine.get('type') == 'turn_context'
                and isinstance(dictPayload, dict)):
            dictSandbox = dictPayload.get('sandbox_policy')
            sSandbox = dictSandbox.get('type') if isinstance(dictSandbox, dict) else ''
            return str(dictPayload.get('approval_policy') or '') + ':' + str(sSandbox or '')
    return ''
def fdictReadConfig():
    dictConfig = {'sClaudeDefaultMode': '', 'sCodexApprovalPolicy': '', 'sCodexSandboxMode': ''}
    try:
        with open(os.path.expanduser('~/.claude/settings.json')) as fileSettings:
            dictPermissions = json.load(fileSettings).get('permissions') or {}
        dictConfig['sClaudeDefaultMode'] = str(dictPermissions.get('defaultMode') or '')
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with open(os.path.expanduser('~/.codex/config.toml')) as fileConfig:
            for sLine in fileConfig:
                if sLine.lstrip().startswith('['):
                    break
                matchKey = re.match(r'\s*(approval_policy|sandbox_mode)\s*=\s*"([A-Za-z-]+)"', sLine)
                if matchKey:
                    sKey = 'sCodexApprovalPolicy' if matchKey.group(1) == 'approval_policy' else 'sCodexSandboxMode'
                    dictConfig[sKey] = matchKey.group(2)
    except OSError:
        pass
    return dictConfig
listChats = []
for dictSource in listSources:
    sRoot = os.path.expanduser(dictSource['sRoot'])
    sProvider = dictSource['sProvider']
    for sPath in glob.glob(os.path.join(sRoot, dictSource['sPattern']), recursive=True):
        try:
            statFile = os.stat(sPath)
            sLaunchDirectory = dictRules[dictSource['sLaunchRule']](sPath)
            bInRepo = fbInRepo(sLaunchDirectory)
            if not bInRepo and not setRequested:
                continue
            sSessionId, sPrompt = ftReadHead(sProvider, sPath)
            bRequested = (sProvider + ':' + sSessionId) in setRequested
            if not (bInRepo or bRequested):
                continue
            listChats.append({
                'sProvider': sProvider, 'sSessionId': sSessionId,
                'sLaunchDirectory': sLaunchDirectory, 'bInProject': bInRepo,
                'bRequested': bRequested, 'iModifiedEpoch': int(statFile.st_mtime),
                'sFirstPrompt': sPrompt,
                'sRecordedMode': fsReadTailMode(sProvider, sPath, statFile.st_size),
            })
        except OSError:
            continue
listChats.sort(key=lambda d: d['iModifiedEpoch'], reverse=True)
sys.stdout.write(json.dumps({'listChats': listChats[:''' + str(I_MAXIMUM_CATALOG_ENTRIES) + r'''],
                             'dictConfig': fdictReadConfig()}))
'''


def _fnValidateRepository(sProjectRepoPath):
    if (not isinstance(sProjectRepoPath, str)
            or not sProjectRepoPath.startswith("/")
            or posixpath.normpath(sProjectRepoPath) != sProjectRepoPath
            or "\x00" in sProjectRepoPath):
        raise ValueError("the project repository is not a normalized path")


def _flistRequestedKeys(listRequested):
    listKeys = []
    for dictChat in listRequested[:I_MAXIMUM_REQUESTED_CHATS]:
        sProvider = dictChat.get("sProvider")
        sSessionId = dictChat.get("sSessionId")
        if sProvider in SET_AGENT_PROVIDERS and fbIsSessionId(sSessionId):
            listKeys.append(sProvider + ":" + sSessionId)
    return sorted(set(listKeys))


def flistCatalogArguments(sProjectRepoPath, listRequested):
    """Return the program's arguments, each validated; raise ValueError."""
    _fnValidateRepository(sProjectRepoPath)
    return [sProjectRepoPath] + _flistRequestedKeys(listRequested)


def _fdictValidatedEntry(dictEntry):
    """Keep a listed chat only when every field has the shape it claims."""
    if not isinstance(dictEntry, dict):
        return None
    if dictEntry.get("sProvider") not in SET_AGENT_PROVIDERS:
        return None
    if not fbIsSessionId(dictEntry.get("sSessionId")):
        return None
    sDirectory = dictEntry.get("sLaunchDirectory")
    if (not isinstance(sDirectory, str) or not sDirectory.startswith("/")
            or posixpath.normpath(sDirectory) != sDirectory):
        return None
    return {
        "sProvider": dictEntry["sProvider"],
        "sSessionId": dictEntry["sSessionId"],
        "sCwd": sDirectory,
        "bInProject": dictEntry.get("bInProject") is True,
        "bRequested": dictEntry.get("bRequested") is True,
        "iModifiedEpoch": int(dictEntry.get("iModifiedEpoch") or 0),
        "sFirstPrompt": str(dictEntry.get("sFirstPrompt") or "")[:240],
        "sRecordedMode": str(dictEntry.get("sRecordedMode") or "")[:64],
    }


def fdictListAgentChats(connection, sResourceId, sProjectRepoPath,
                        listRequested):
    """Run the catalog program; return ``{listChats, dictConfig}``.

    Must run inside a carrier admission: it is an exec. Raises
    ``RuntimeError`` naming the failure when the program fails.
    """
    tResult = promptRecordManager.ftRunTranscriptProgram(
        connection, sResourceId, _S_CATALOG_PROGRAM,
        flistCatalogArguments(sProjectRepoPath, listRequested),
    )
    if tResult.iExitCode != 0:
        raise RuntimeError(
            "The agent chats could not be listed: " + (tResult.sStderr or "")[-400:])
    dictAnswer = json.loads(tResult.sStdout or "{}")
    listChats = [dictChat for dictChat in (
        _fdictValidatedEntry(d) for d in dictAnswer.get("listChats") or [])
        if dictChat is not None]
    dictConfig = {sKey: str((dictAnswer.get("dictConfig") or {}).get(sKey) or "")[:64]
                  for sKey in ("sClaudeDefaultMode", "sCodexApprovalPolicy",
                               "sCodexSandboxMode")}
    return {"listChats": listChats, "dictConfig": dictConfig}
