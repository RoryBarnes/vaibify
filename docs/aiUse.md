# Declaring AI Use

A reader of an agent-assisted paper will reasonably ask which AI models
helped, what they did, and how you checked their work. Vaibify has you
answer those questions inside the project, as you go, so the answers
are published with your data instead of reconstructed from memory at
submission time. A declaration of AI use is one of the Level 2
requirements of the [PROOF Ladder](proofLadder.md), and the optional
records at the end of this page collect the evidence behind the
"Replayable" property of [Vibe Coding Scientific Software](vibeCoding.md).

| What | Where on the dashboard | Required for Level 2? |
|---|---|---|
| An **AI Declaration** in your own words | A step in the step list | Yes, signed off |
| The **AI models** you used | The Project block's **AI** section | Yes |
| Your **Personal AI Configuration** | The Project block's **AI** section | Yes, any answer |
| The **Prompt Record** and **Supervised mode** | The Project block's **AI** section | No, never blocks a level |

Vaibify checks only that each question has an answer. Whether the
answer satisfies a journal or a referee is between you and them.

## The AI Declaration step

The declaration is a short Markdown document, written by you, that
explains how AI assisted the work. It is a *step* rather than a setting
because it is a publication artifact, like your figures: it is
committed, pushed, archived, and pinned in the manifest along with them.

**Adding the step.** A project without one shows a row at the foot of
the step list: *AI Declaration step — required for Level 2, not yet
added*. Click **Add AI declaration step**. The new step is named **AI
Declaration** and points at `AI_USAGE.md` at the root of your project
repository. A project holds only one.

It is an *interactive* step, so it gets an I label, and it runs no
commands, so its run-status light is a dash. It has no Level 1
requirements and never holds your project below Level 1. Everything
about it lives in the step's expanded Level 2 section.

**Choosing or writing the file.** The **AI Usage Declaration** block
asks your repository whether the declaration file exists. If it does
not, the block offers **Generate template (AI_USAGE.md)**, which writes
vaibify's starter template with four headings: **Models used**; **How
AI assisted each step** (code generation, debugging, plot tweaks,
documentation); **Review policy** (how a human reviewed AI-generated
output before it landed); and **Anything else researchers should
know**. It also offers **Choose existing file**, which opens a file
picker inside your project repository. Once the file exists, the block
shows its name and first few lines, and **Choose different file**
points the step at another file. Pointing the step at a different file
withdraws your sign-off, because you signed a different document. If
vaibify cannot reach the repository, for example because the container
is stopped, the block says it could not check and offers a diagnosis
instead.

Write the declaration however you like. If your agent drafts the text,
read it carefully: the declaration is your statement, not the agent's.
**Open in viewer** shows the whole file in a viewing window, where the
pencil (✎) button lets you edit and save it.

**Committing it.** **Commit to repo…** checks the repository and offers
to commit just this file. Committing is not publishing: push from the
Repos panel, as for any other file. Once git tracks the file, **Remove
from repo…** takes it out of git; the file stays on disk but no longer
counts as published.

**Signing it off.** Below the preview is a sign-off row labeled with
your name. Click its marker to move it from *Untested* to *Passed*; a
further click marks it *Failed*, and another returns it to *Untested*.
The time of your last change appears beneath it. Sign off when the text
is final. Until you do, the step's L2 cell reads *AI declaration not yet
attested — open the step and verify it*. The section's requirement rows
are **AI declaration signed off** plus the publication rows every step
carries, such as **Published files match the GitHub mirror**, which here
compare `AI_USAGE.md` with its published copies.

**When the work changes after you sign.** Your sign-off covers the
project as it stood when you gave it. At that moment vaibify records a
SHA-256 hash of every other step's scripts, outputs (data and plots),
and declared input data. If any of those files later changes, appears,
or disappears, the sign-off becomes stale: the marker reads *Stale*,
the step's ⚠ names the steps whose files changed, and Level 2 is
blocked until you review the declaration and sign off again. Signing
again takes one click and records new hashes. Changing a file back does
not undo the warning, and editing the declaration file itself never
causes one. If vaibify cannot read one of the files, the step says it
could not check, and Level 2 stays blocked until it can. A declaration
signed before vaibify recorded these hashes stays signed, with a note
that changes are tracked from your next sign-off.

Vaibify compares the files with the recorded hashes each time it
checks, so a change that is made and completely undone between two
checks cannot be detected.

## Declaring the AI models

Every model that touched the project must be named, one model per
entry, because a provenance record names models individually. In the
Project block's **AI** section, expand the **AI models** row. With
nothing declared it offers **Declare Model**; afterward, **View
Declaration** lists your entries and **Edit Declaration** changes them.
Each entry asks for:

- **Vendor**, the company or group that offers the model;
- **Model ID**, one exact model identifier, not a product family;
- **Used from** and **Used until**, the dates you used it.

If the model's weights are public, tick **Open weights** and also give
the **Weights source** (where the weights are published) and the
**Weights revision hash**, so others can load exactly the weights you
used. **Declare** saves a new entry and **Save** an edited one; **Add
another model** adds a card, and **Delete** removes an entry after you
confirm.

The row passes when at least one model is declared and every entry is
complete. Undeclared is the only failing state; a closed-weights model
passes by declaration alone. The PROOF tab calls this requirement **AI
model declared**.

## Personal AI Configuration

The models are only part of what governed the AI's work. If you use a
coding agent on your own computer, you may also have a private setup of
your own there: a global instruction file, personal skills, memory, or
hooks. The **Personal AI Configuration** row asks one question about it:
*did your own private, host-side agent setup govern this work, and are
you disclosing it?*

**Answering is the requirement. Disclosure is never required.** All
three answers pass:

- **No personal AI configuration exists**
- **Exists — content withheld**
- **Included in the project repository**

Only an unanswered question fails. "Exists — content withheld," with
nothing further, is a complete answer; you do not need to write, find,
or upload anything.

If you choose "Exists — content withheld," the row also offers an
optional **hash commitment**: give a **Label** and the file's **Path on
this computer**, then click **Add hash commitment**. Vaibify stores only
the label, the file's SHA-256 hash, its size, and the date, never the
path or the content. The commitment reveals nothing, but if you later
release the file, anyone can check that it is the version that governed
the work. Only you can add one; the in-container agent cannot. The
PROOF tab calls this requirement **Personal AI Configuration answered**.

## The Prompt Record

A transcript of your conversations with the agent is the most complete
account of how the code came to be, and it cannot be reconstructed
later. The **Prompt Record** keeps one as you work. It is optional and
never blocks a level.

**Turning it on.** Expand the **Prompt Record** row and click **Set up
recording**, then **Turn on recording**. Recording needs a secret
scanner on the computer running vaibify (not in the container); if it is
missing, the dashboard says so. Install it with
`pip install "vaibify[replay]"` and turn recording on again.

**What it captures.** While recording is on and the dashboard is open,
vaibify checks every 30 seconds for new material and copies the
in-container agent's session transcripts, your prompts and its replies,
into the project repository. It reads the session files that Claude
Code, Codex and Gemini write inside the container. Only sessions
started inside this project's folder are captured; the row counts any
started elsewhere, such as the workspace root or another project, and
leaves them out.

**How secrets are removed.** Your repository is public or will be, so
every capture is scanned *before* it is saved. A visible
`[REDACTED: …]` marker, naming what was removed, replaces the session
secrets vaibify itself issued, well-known credential formats (cloud
keys, service tokens, private keys, and the like), and long
random-looking strings of letters and digits. If the scanner cannot
run, nothing is captured. It cannot recognize prose you consider
private, which is why your review matters.

**Approving the first capture.** The record does not count until you
have read it. When the first capture arrives, the row shows a ⚠ and a
**Review & Approve** button. The viewer lists each session with its
redaction count, and **Show only turns with a redaction** narrows the
view to what the scanner changed. Gemini lets you take turns back and
can replace a message's text, so a Gemini session is shown as it
finally stood: turns that were taken back are folded into one
**Rewound** group where they happened, and a reply whose text changed is
marked *edited*, with its earlier text folded beneath it. Nothing is
removed from the record itself. **Approve first capture** records that
you have read the redacted sessions and are content for them to be
published with the project. The agent can never approve its own
transcript. **View Record** reopens the viewer at any time.

**Where it is stored.** In your project repository, under
`.vaibify/promptRecord/sessions/`, with an index beside it. Each capture
records the hash of the one before it, so editing or deleting a capture
breaks the chain, and the dashboard says so on the row and in the
viewer. The record is *tamper-evident*, not *provably complete*: time
between recorded intervals was not monitored, and the dashboard shows
those gaps rather than hiding them.

## Supervised mode

Some changes never appear in a transcript: an edit you make by hand, a
script that rewrites a file. **Supervised mode** adds a supervision log
on top of the Prompt Record. Every change to a file your project
declares must be attributable to an action vaibify recorded, such as a
step run, an editor save, an agent request, or an open terminal session
(recorded as opening and closing, not keystroke by keystroke). A change
with no recorded cause, or one made while vaibify was not watching, is
flagged permanently. The log lives under
`.vaibify/promptRecord/attribution/`.

The **Supervised mode** row offers **Turn on Supervised mode** once the
Prompt Record is on and its first capture approved. It is unavailable
for a project that runs directly on your computer rather than in a
container, where vaibify cannot see every path to your files. Flags
remain on the row after you turn supervision off, and the Prompt Record
cannot be turned off while Supervised mode is on. It is optional and
never blocks a level.

## How it shows on the dashboard

The Project block's **AI** section holds five rows: **AI models** and
**Personal AI Configuration**, which count toward Level 2, and three
optional ones, **Project instructions** (the agent's standing
instructions in `.vaibify/AGENTS.md`), **Prompt Record**, and
**Supervised mode**. An optional row is never red: it shows as attained
when it is on and in good order, a neutral dash otherwise, and a ⚠
beside its title when it needs you. The AI Declaration's sign-off
appears only on its own step. The PROOF tab's Level 2 section lists the
same requirements, each with a link to its row.

Together these rows place the project on the Replay axis described on
the [PROOF Ladder](proofLadder.md) page: *declared* once the models and
the Personal AI Configuration are answered, *recorded* (and so
"Replayable") once the Prompt Record is on and approved, and
*supervised* once Supervised mode is on as well. The dashboard does not
print the state's name; you read it from the rows.
