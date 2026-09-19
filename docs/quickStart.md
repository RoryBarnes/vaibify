# QuickStart

In this guide you will first reproduce an existing result within 15 minutes. These results, however, will not be byte-identical to those that were published because you will reproduce the results locally on a computer that is slightly different than the original author's. Thus, you will then recreate the author's computing environment inside your computer and verify that you reproduce the results exactly. This latter step can take over an hour if you do not already have Docker installed on your computer.

## 1. Install

You need Python 3.9 or later to run `vaibify`.

```bash
pip install vaibify
vaibify
```

Run `vaibify` with no arguments in any directory to start the **Environments Hub** —
a local web server on `http://127.0.0.1:8050` — in your browser. You
should see the `vaibify` logo, the motto, and an empty **Environments**
list.

## 2. Download the example project

Return to the terminal and run

```bash
git clone https://github.com/RoryBarnes/aigreenhouse.git
cd aigreenhouse
pip install -r .vaibify/requirements.txt
```

This repository contains the data to generate two figures from the `vaibify` paper (Barnes 2026) a real, finished analysis: estimates for how long it will be before the waste heat from AI
data centers becomes large enough fo Earth to begin losing water permanently to space. The Project consists of two
computational steps, a figure from each, three tiers of tests, and a
declaration of which AI models helped write it. It is published at PROOF
Level 3, which means its author committed a manifest of every artifact's
SHA-256 hash along with the pinned environment that produced them.

## 3. View the example project in vaibify

Back in the hub, click the **+** next to *Environments* to add this project to the list of environments that `vaibify` can access. The wizard asks
two things: where the project runs — choose **This machine** rather than
*Container* — and whether the project already exists — choose **Add
Existing** and point it at your clone. Now you will return to the Environment Hub and see a registered environment: `aigreenhouse`. Select it.

You will now move to the **Projects Hub**. An environment is a *place* projects run, and it can hold more than one, so
this hub offers you one or more Projects to work on. You will see two options: A **Blank Project** is an empty
workspace (a "sandbox") in which you can experiment in the environment. All environments include a "Blank Project". The second option is **AI Greenhouse**, the project you are reproducing in this guide. Select it. 

You are now in the dashboard for this project. Take a look around. Along the top are some fundamental facts about the Project as well as menus for interacting with the Project. The left side shows the steps of the project and information about the project as a whole. The top right consists of two "viewing windows" that displays images and ASCII text files for you to examine and compare. The bottom left is one large reproduction of a terminal. You can create multiple tabs and multiple terminals. This dashboard is where you will use coding agents to modify files, examine their efforts, lock down verified results, and connect to external resources like Overleaf and Zenodo.

Although the published result is at PROOF Level 3, meaning the author verified they reproduced the results at the bit level, your copy is not at that level. In fact, you're at Level 0, meaning your results aren't even being labeled as "self-consistent"! This status is because you have not run any of the steps locally and verified the results pass the unit tests, which check that the output files match the author's results to some tolerance that they deemed accepatable for scientific reproduction, which is in general a lower bar than byte-reproducibility. Before we start driving your copy up the PROOF Ladder, we're going to explore the repository a bit more.

## 4. Check the archive before touching it

From the **Run** menu, choose **Check Files Against Manifest**. (The manifest is the list of artifacts in the repository and their SHA-256 hashes, which is a summary of the bytes in the file.) This action regenerates the hashes of every file in the repository and reports that all
of them match. 

This result has to hold for a freshly cloned Level 3 project because nothing has changed. You have
confirmed that the files in your clone are byte-for-byte identical to the files the
author committed. Note that this check is *not* a statement about the
accuracy of the science; it is a check on the archive's integrity, and nothing more.

## 5. Recreate the data

Next we will rerun the results locally and see if they pass the unit tests. First, we need to get rid of the old outputs and start clean.
From the **Run** menu, choose **Clean Outputs**, and confirm. Every automatic step's data files and figures are deleted and every
verification mark resets to untested. You will be prompted about the AI Declaration because it is interactive. Select **Skip** for this step because that is the author's statement and should not be deleted or modified until you make changes that you plan on publishing..

Now we're ready to rerun the pipeline. From the **Run** menu, choose **Run All Steps**. Each step runs its data commands, then its plots, and
pulses orange as it runs and turns to solid orange once it finishes. Orange means some part of the step is verified, but not everything. You'll also see the terminal output of the command in the top left viewing window. 

Take a closer look at Step A01 by clicking on it, which expands the step to show you everything about it. There are multiple sections here about input data, scripts, plots, connections to remote resources, and verification. Files change colors based on their status and you can hover over them to learn about what issues they may have. In this case, the input and output data as well as the plot file are orange. Click on the plot file and you will see the figure appear in the top right viewing window and resize if necessary. Compare it to Figure 2 in Barnes (2026). It looks the same, but yet is orange. That's because you have not yet verified the results.

So let's run them: From the Run
menu's select **Run All Unit Tests** and you will see messages in the top right that state that they pass. Now look at the Step A01 block under verification and you can see that the Unit Tests marker has changed to "Pass". If you expand the Unit Tests, you can see the three types of unit tests - Qualitative, Quantitative, and Integrity - all pass, too. 

Note, however, that the research field still says "Untested". You have not declared the results to meet your own standard of scientific rigor. When you're using `vaibify` for your own research, you'll want to be very careful about passing the researcher test because that means you are declaring the results to be accurate. While the process can of course be undone, waiting until you are sure is the best approach with `vaibify`. But for this example, go ahead and click the researcher marker for Step A01 to "Passed". After a couple seconds or so, the left-most orange marker in the banner for Step A01 will turn to a blue check, indicating this step has reached PROOF Level 1. Then do the same for Step A02. This time, when the dashboard detects the change, not only will the main orange marker flip to a check, the entire dashboard theme color will change to purple and you will now see a single check to the right of "AI Greenhouse" along the top bar. The number of checks next to the project name indicated the current PROOF Level of the project, Level 1 in this case.

### Doing the same thing with an agent

Using the dashboard to click through tasks is sooo 2025. Now we generally asks AI agents to perform tasks like "Run all steps and their unit tests." `vaibify` includes an [agent action catalog](dashboard.md#agent-actions) that provides deterministic commands that agents can pass to the host machine and run in your container. While this functionality might appear to expose your host machine to the container, the commands are tighly monitored to only direct the `vaibify` to perform operations inside a container. In this example, you are in host mode, though, so these operations would be performed on your local machine.

## 6. Check the bytes again

So are the new files identical to the author's manifest? From the **Run** menu, choose **Check Files Against Manifest** again.

They will fail. (Unless you are running on an Ubuntu v24.04 machine shortly after September 2026).

Not all of it, but the numbers you just regenerated are not identical
to the numbers the author published, and neither are the figures. Here's why not:

- **The fitted values differ in their last digits.** A least-squares fit
  is a sequence of floating-point operations, and different environment's
  build orders and vectorization schemes are different. The science is identical
  to twelve significant figures, which is close enough for most scientific reproducibility expectations. The bytes, however, are not identical.
- **The figures differ significantly.** Matplotlib stamps its own version into
  every PNG it writes, so a different matplotlib version guarantees
  different bytes before you even reach font rendering, which also
  differs by platform.

Your run passed every *scientific test* the project defines, but did
not reproduce byte-for-byte because your computing environment is slightly different from the one the author used. These differences arise because the standard rules that govern computing standards (the IEEE) allow changes in the last digits of some functions, like transcendentals. Thus, progress from here requires reruning the work in the exact same environment that the author used, which is the topic of the next section.

This next section is technically optional, but discouraged. While the `vaibify` dashboard offers a complete view of your computer and the `vaibify` Projects, you computer is not secure and the results cannot be fully reproduced. We bring this up because the next step can take an hour or more for Mac and some Linux users who need to install Docker.

## 7. Containerize the same project

Before we begin this step, note the following:

- **The project must not be open anywhere else.** If it is open in the
  tab you are clicking from, `vaibify` closes it for you. A session in
  another browser or on another machine refuses the conversion instead,
  because the conversion renames the key that the project's lock,
  lease, and journal all hang from.
- **Install Docker or Colima if you don't have them yet.** This step requires Docker containers, which are run as `colima` on macOS. See the [install guide](install.md#docker-on-macos)) to install. *Note that this process can require over 60 minutes.*
- **A failed build does not put you back where you started.** It leaves
  a registered container that has not been built yet. Fix the cause
  and build again (try working with an AI agent); you have not lost the host project's work, because
  there was never a copy to lose.


If you're ready, then from the dashboard, press the Admin pulldown menu in the top right corner and select Environments [Note to claude: It currently still says Containers] to return to the original landing page. Open the kebab menu (**⋮**) on this environment's tile, and choose **Containerize Environment**.

A seven-page wizard opens. It asks about the *environment* — nothing
it collects moves, renames, or rewrites your files. Every page has a
**?** that explains the choice in more detail; what follows is what to
enter to finish this walkthrough. Where a page says "leave it", the
default is the right answer and you can press **Next**.

[Notes to Claude: 1) The "What to Enter" field does not wrap when rendered; can they wrap? Or at least have a horizontal scroll bar? 2) There does not seeem to be the proper instructions for installing an existing environment!]
| # | Page | What to enter here |
|---|------|--------------------|
| 1 | **Name** | The container, image, and registry name. It is pre-filled with a Docker-safe version of your directory name — lowercase, hyphens, no spaces. Accept it unless it collides with another environment on this machine. |
| 2 | **Python version** | Leave it at **3.12** unless your code is tested against a specific release. |
| 3 | **Repositories** | Leave it **empty**. This clones *additional* git repositories into the container; the project you are converting is already accounted for. |
| 4 | **Features** | Tick **at least one coding agent** — several are offered and none is ticked for you; leaving the page with none selected raises a confirmation, because a container with no agent is usually a slip. LaTeX is on by default. Leave GitHub authentication on. Under *Resource limits*, blank means "all cores − 1" and unlimited memory; **1** CPU and **1** GB are enough for a small pipeline and keep the build off the rest of your machine. |
| 5 | **Copy into the container** | A container does not share your folder, so tick what should be copied in. For this walkthrough tick **everything**; your originals stay where they are. |
| 6 | **Packages** | Leave both boxes **empty**. The dependencies come from the repository's own `requirements.lock`. |
| 7 | **Summary** | Read it back, then press **Convert**. |

Then the build runs. This process does not create a second project. Your clone stays exactly where
it is — the same directory, the same git history, the same outputs you
just produced. The difference is that now when you interact with the project via `vaibify` you are inside a container that cannot modify your own computer and whose environment is identical to that of the author of the project.

When the build finishes, open the project again. It will look the same except the `host-mode` badges become `contained`. The steps still do the
same things, but now they do them in the pinned environment, and the
PROOF tab's Level 3 row stops saying the project has no image and instead reports on whether the reproducibility rules have been answered, and
whether a rebuild from that image reproduces the outputs.


[Note to Claude: From here, there should be simple instructions on how to use the GUI to confirm the reproduction. So much of this is excessive.]

### Two claims, checked two different ways

The obvious next move is to run the pipeline again and re-check the
manifest. **Don't** — that is not a byte test, and it cannot come out
clean. And note that you did not need to containerize anything to
check the bytes: the command below the two claims works from the
published URL alone, in a shadow container built from the image the
author pinned, on any machine with Docker.

Every figure vaibify renders is dated and salted from the project
repository's HEAD commit. That is what makes two runs of the same
source produce identical bytes: without it, a PDF carries the wall
clock in its `CreationDate` and an SVG gets fresh element ids from a
new `uuid4` on every process. The manifest pins figures dated from
HEAD *as it was when the manifest was written*. Any commit since —
and this walkthrough makes several — changes the epoch, so a hand
rerun re-renders every PDF, EPS, PS and SVG against a different one.
The manifest check then reports them as differing whether or not
anything real changed, which tells you nothing at all.

So the two claims get checked separately, and neither substitutes for
the other:

**The science — checked on your own machine.** That is what section 5
did. The quantitative tests confirm each recomputed number lands inside
the author's recorded tolerance, so they answer "is this the same
result?" without asking anything about your libraries. They passed, and
they passed before you had a container at all — which is the point of
section 6: the science agreed while the bytes did not.

**The bytes — checked in the pinned environment.** Three ways, and
they check the same thing.

As a stranger, from the URL alone:

```bash
vaibify reproduce --from https://github.com/RoryBarnes/aigreenhouse.git --rerun
```

It clones the project in full, validates the snapshot, obtains the
image the author pinned (the registry first, then the archived copy on
Zenodo, then a copy already on your machine, saying which one served),
runs every step in a fresh *shadow* container built from that image
with no network and no credentials, compares the bytes inside it, and
writes a **reproduction report** under your own home. The verdict is
*reproduced* — or *reproduced under emulation* if the pinned build is
not your machine's architecture and you passed `--allow-emulation`,
or *diverged*, or *no verdict* with the reason. The report is yours,
not the author's: nothing is written into the project, and it is not
an attestation.

Or, from the hub instead of the terminal: the **+** button's third
kind card, **Reproduce a published project**, takes the same URL,
shows you what a run would use before anything is pulled, and shows
the same verdict and report when it settles. No tile is added; the
shadow is destroyed when the comparison is made.

As a stranger without vaibify, the artifact the project publishes:

```bash
./reproduce.sh
```

It reads the image digest, the pinned platform and the recorded epoch
out of `.vaibify/environment.json`, pulls that exact image for that
platform, runs every step inside it, and finishes with

```
sha256sum -c MANIFEST.sha256
```

**Zero mismatches is the payoff.** Not "a small delta" — zero. It is
what lets a reader who has never met you re-derive your figures byte
for byte, and it is the whole reason the environment is pinned.

As the author, the dashboard equivalent is **Verify Level 3
Reproducibility**, in the **Run** menu (it also has a button on the
PROOF tab's Level 3 row). It creates a shadow container from the image
digest your project pins, copies the repository into it, runs the
whole pipeline there, and compares the results against your files —
then destroys the shadow. It does not touch your outputs, and it is
the one of the three that writes an attestation, because it is your
claim about your own project. All three re-run against the epoch the
envelope recorded rather than against today's HEAD, which is exactly
why they can come out at zero and a hand rerun cannot.

It is the difference between believing your work reproduces and having
watched it happen.

[Note to Claude: The 1-2 paragraphs that describe how to actually reproduce the results should end here.]

## 8. When something is wrong

Run `vaibify doctor`, and do what it says.

```bash
vaibify doctor              # every scope
vaibify doctor --container  # only what is inside the running container
```

It is the command for the moment when a build will not build, a
container will not start, an agent inside the container cannot reach
anything, or the dashboard is saying something you did not expect. It
reports on three scopes — this machine, the inside of the running
container, and vaibify's own record of the project — and every finding
that needs action names the exact command to run, correct for the
Docker runtime you are actually using.

Three things about the report are worth knowing before you read one.

**Doctor changes nothing.** It never starts, stops, removes or restarts
anything. You can run it against a container that has just died without
destroying the evidence of why.

**"Not checked" is not "ok".** A check that could not run says so, with
the reason, in its own group, and it is counted in its own column. A
diagnostic that quietly reports success for having run nothing is worse
than no diagnostic.

**A finding you can act on names its own command.** Where vaibify can
apply the fix itself, that command is `vaibify repair`. Where it
cannot — a network that is down, a daemon that needs more memory — the
finding says so plainly rather than sending you to a tab.

### Outputs that were re-run outside the container

If you run a step's script yourself — in a terminal, in your editor, on
a cluster — the outputs it writes are not the outputs vaibify recorded,
and the dashboard will turn those files red. Nothing is broken and
nothing was lost: the badge is telling you the truth, which is that the
file on disk is no longer the one the recorded run produced.

The way forward is to run the step through vaibify (Run Step, or
`vaibify run`), so the run that produced the file is the run vaibify
recorded. Vaibify deliberately does not offer a way to mark a file
"fine as it is": a green badge over a file whose provenance nobody
knows is exactly the claim this tool exists not to make.

## 9. Where to next

- **[The three templates: sandbox, toolkit, workflow](templates.md)** —
  starting your own project rather than driving someone else's.
- **[The dashboard tour](dashboard.md)** — every panel, the status
  colors, and the verification state machine.
- **[The reproducibility ladder](reproducibility.md)** — what Levels 1
  through 3 each certify, and what none of them do.
- **[Security model](security.md)** — what a container protects against
  and what host mode does not. Worth reading before you let an agent
  write code anywhere.
- **[Install guide](install.md)** — Docker and Colima, plus
  platform-specific troubleshooting.
- **[Command line interface](cli.md)** — everything above, scriptable.

A note on what you will see in `git status` afterwards: opening a
project can refresh the small `conftest.py` that vaibify installs in
each step's `tests/` directory, because it records where the project
repository sits on the machine reading it. That file is vaibify's test
harness, not part of the analysis, and it is not pinned in the
manifest.
