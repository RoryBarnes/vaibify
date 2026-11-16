# Returning to Your Work

vaibify remembers how you arranged the dashboard, and can reopen the
agent conversations you had in its terminals. When you click a
Project, it asks how you want to start.

## QuickStart

1. Open an environment and click a Project on its Project Hub.
2. A dialog asks how terminals and agent chats should start:
   - **Default**: the Project's first view, with no agent chat
     resumed.
   - **Latest**: the panes and tabs exactly as you left them, each
     agent chat resumed, the Agent Council where it was, and this
     Project's own view (selected step, open sections, viewers, the
     Files folder and the panel sizes).
   - **Choose chats**: pick past conversations of this Project and
     place each in a pane.
3. Click **Continue**.

**Latest** is selected for you whenever something has been saved for
this environment. There is no "don't ask again": the question is
cheap, and resuming an agent should be a choice.

The dialog is skipped in one case only. If you go to **Admin >
Projects** and click a Project in the same browser window, your
terminals are still running, so vaibify keeps them exactly as they are
and opens the Project with its own saved view. After a reload, after
leaving to the environments page, or after **Quit**, the dialog
appears again.

## Example Usage

You have two Claude Code chats running in two panes of an environment,
one of them in bypass mode, and you click **Quit** at the end of the
day. The next morning you open the environment and click the same
Project:

1. The dialog opens with **Latest** selected. It lists both chats: the
   provider, when each was last active, and its first prompt.
2. The bypassed chat holds **Continue** until you answer it: either
   resume it as it was, acknowledging that the agent will act without
   asking, or resume it with standard permissions.
3. **Continue** recreates both panes. Each tab starts a fresh shell and
   types the resume command for its chat, for example
   `cd -- /workspace/myRepo && claude --resume 1c37be47-...`, so the
   conversation picks up where it stopped.

To bring back an older conversation instead, choose **Choose chats**,
tick it, and set **Pane 1** to **Pane 5** for it. Chats you place in
the same pane become tabs of that pane. **Continue** stays disabled,
and its tooltip says how many chats are still unplaced, until every
chosen chat has a pane.

## Input Options

| Choice | Terminals | The Project's view |
|---|---|---|
| Default | one fresh pane, no chat resumed | first view |
| Latest | the environment's saved panes and tabs, chats resumed | saved view |
| Choose chats | the chats you picked, in the panes you chose | first view |
| (skipped: Admin > Projects) | untouched, still running | saved view |

**Which conversation a tab holds.** Each terminal's shell defines
`claude`, `codex` and `gemini` wrappers that record which chat the tab
is running. How sure vaibify can be depends on the agent:

| Agent | What vaibify knows | In Latest |
|---|---|---|
| Claude Code | the conversation on screen, including after `/resume`, `/clear` or `--continue` | resumed |
| Gemini | the conversation the tab started with; a switch inside Gemini cannot be detected | resumed, with that caveat shown |
| Codex, started with `codex resume <id>` | the conversation the tab started with | resumed, with that caveat shown |
| Codex, started plainly | not which conversation it was | opens as a plain shell; you may pick the conversation in the dialog |

A wrapper adds a flag only when the agent's own `--help` offers it,
and never replaces settings you pass yourself: Claude Code's
`--settings` disables vaibify's switch tracking for that chat rather
than overriding yours. An alias that points an agent's name at another
program bypasses the wrapper; that tab then reopens as a plain shell.

**Permissions.** Resuming replays the chat's permission and model
flags. Any other argument, such as the first prompt, is not replayed,
and the dialog lists what was left out. A chat counts as bypassed if
its flags, its agent's configured default (Claude Code's
`permissions.defaultMode`, or Codex's `approval_policy` together with
`sandbox_mode`), or the mode its own conversation record last noted
bypassed permission checks. Gemini's settings cannot turn on yolo
mode, so only its flags count. "Standard permissions" removes the
bypass flags and adds the agent's explicit standard ones
(`--permission-mode default`, `--approval-mode default`, or
`-a on-request -s workspace-write`), because each agent obeys a bypass
flag over a standard one.

**What the dialog can and cannot tell you.** The chats are found in
the agents' own conversation files. Anything that runs as you can
write those files, including an agent running in the container, so
vaibify shows each chat as found, not as verified: check the provider,
date and first prompt before resuming. What a forged record can do is
limited: the resume command comes from a fixed table with every
argument quoted, so it can choose which conversation an agent opens,
and nothing more.

## Output

**The saved layout** is kept on your computer, never inside the
environment:

- `~/.vaibify/dashboardState/<environment>/environment.json`: the
  panes, their tabs and widths, the chat each tab held, and the Agent
  Council window.
- `~/.vaibify/dashboardState/<environment>/projects/<digest>.json`:
  one file per Project file, so two Projects in one repository keep
  separate views.

Files are readable only by you (mode 600, in 700 directories). They
hold layout only: no step status, verification state or badge is ever
saved, because the dashboard always shows those as they are now.
Renaming an environment moves its records; deleting it removes them.
Agents cannot read or change these records.

Saving happens about a second after you click, drag or press Enter,
and before you leave a Project, leave to the environments page, or
quit. A window that closes without leaving sends its layout one last
time, on a best-effort basis. If two windows change the same layout,
the later change is kept, and the other window says "Layout saved
from another window; this window's last change was not saved."

**Terminals are not kept running** while you are away. Leaving ends
every shell, which is what lets vaibify say honestly whether a Project
is quiet. A resumed chat is a new shell running the agent's own resume
command.

**Agent Council conversations** with the chairbot are kept on disk
under `~/.vaibify/agentCouncils/<campaign>/chats/`. Quitting vaibify
or releasing a Project keeps a conversation open; only **Close
conversation** ends it, and a closed conversation stays readable under
**Past conversations**. A question that was being answered when
vaibify stopped is shown as "interrupted by shutdown — not re-asked",
with an **Ask again** button: paid work never repeats without your
click. The council's event log is kept too, so the Agent tabs reopen
after a restart, and **Show earlier events** pages back through it.
A campaign that holds a conversation is never removed by automatic
retention.
