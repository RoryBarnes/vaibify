# QuickStart

In this guide you will first reproduce an existing result within 15 minutes. These results, however, will not be byte-identical to those that were published because you will reproduce the results locally on a computer that is slightly different than the original author's. Thus, you will then recreate the author's computing environment inside your computer and verify that you reproduce the results exactly. This latter step requires that you have [Docker installed](install.md) on your computer.

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
pip install -r .vaibify/requirements.txt                   # Install the project's dependencies
```

This repository contains the data to generate two figures from the `vaibify` paper (Barnes 2026): estimates for how long it will be before the waste heat from AI
data centers becomes large enough for Earth to begin losing water permanently to space. The Project consists of two
computational steps, a figure from each, three tiers of tests, and a
declaration of which AI models helped write it. It is published at [PROOF
Level 3](proofLadder.md), which means its author committed a manifest of every artifact's
SHA-256 hash along with the pinned environment that produced them.

## 3. View the example project in vaibify

Back in the hub, click the **+** symbol below the `vaibify` motto "Vibe boldly. Verify everything." This launches a wizard to add a project to the list of environments that `vaibify` can access. The wizard prompts you to choose the type of environment, a *container*, *this machine*, or *a reproduction*. Choose **This machine** and then on the next screen choose **Add
Existing**. Navigate the directory tree to your aigreenhouse clone (it will have a blue checkmark next to it) and select it. You will then return to the Environments Hub and see a registered environment: `aigreenhouse`. Select it and you will see a window that warns you that you are working in an uncontained environment. Press "I understand, continue".

You will now move to the **Projects Hub**. An environment is a *place* projects run, and it can hold more than one, so
this hub offers you one or more Projects to work on. You will see two options: A **Blank Project** is an empty
workspace (a "sandbox") in which you can experiment in the environment. All `vaibify` environments include a "Blank Project". The second option is **AI Greenhouse**, the project you are reproducing in this guide. Select it. 

After a few seconds, you will land in the dashboard for this project. Take a look around. Along the top are some fundamental facts about the Project as well as menus for interacting with the Project. The left side shows the steps of the project and information about the project as a whole. `vaibify` divides the work of climbing the [PROOF Ladder](proofLadder.md) into **Steps**, the components of your pipeline,  and the **Project**, which are features that apply to all Steps. These sections give you up-to-date information on the status of the Project. The top right consists of two "viewing windows" that display images and ASCII text files for you to examine and compare. The bottom right is one large reproduction of a terminal. You can create multiple tabs (+) and multiple terminals (+pane in the very bottom right). This dashboard is where you will use coding agents to modify files, examine their efforts, lock down verified results, and connect to external resources like Overleaf and Zenodo.

Although the published result is at [PROOF Level 3](proofLadder.md), meaning the author verified they reproduced the results at the bit level, your copy is not at that level. In fact, you're at "Level 0", meaning your results aren't even being labeled as "self-consistent"! This status is because you have not run any of the steps locally or verified the results pass the unit tests (which check that the output files match the author's results to some tolerance that they deemed acceptable for scientific reproduction). Before we start driving your copy up the PROOF Ladder, we're going to explore the repository a bit more.

## 4. Check the archive before touching it

From the **Run** menu, choose **Check Files Against Manifest**. (The manifest is the list of artifacts in the repository and their SHA-256 hashes, which is a summary of the bytes in the file.) This action regenerates the hashes of every file in the repository, compares them to the repository's file `MANIFEST.sha256` that contains the summary of the tracked files, and reports if they match. In this case, they will.

This result *must* hold for a freshly cloned Level 3 project because nothing has changed. You have
confirmed that the files in your clone are byte-for-byte identical to the files the
author committed. Note that this check is *not* a statement about the
accuracy of the science; it is a check on the archive's integrity and nothing more.

## 5. Recreate the data

Next we will rerun the results locally and see if they pass the unit tests. First, we need to get rid of the old outputs and start clean.
From the **Run** menu, choose **Clean Outputs**, and confirm. Every automatic step's data files and figures are deleted and every
verification mark resets to untested. 

Now we're ready to rerun the pipeline. From the **Run** menu, choose **Run All Steps** and confirm. Each step runs its data analysis commands, plot commands, and test commands. The circle to the right 
pulses orange as it runs and turns to solid orange once it finishes. Orange means some part of the step is verified, but not everything. You'll also see the terminal output of the command in the left viewing window. You will also see notifications that the Steps' tests have passed. Finally, you will be prompted about the AI Declaration because it is interactive. Select **Skip** for this step because that is the author's statement and should not be deleted or modified until you make changes that you plan on publishing. You will be notified that the pipeline has completed.

Take a closer look at Step A01 by clicking on it, which expands the step to show you everything about it. There are multiple sections here that break down the progress toward [PROOF Level 3](proofLadder.md), including input data, scripts, plots, connections to remote resources, and verification. Files change colors based on their status and you can hover over them to learn about what issues they may have. In this case, the input and output data as well as the plot file are orange, meaning they have not reached PROOF Level 1. Click on the plot file and you will see the figure appear in the top right viewing window. Compare it to Figure 2 in Barnes (2026), resizing if necessary. It looks the same, the unit tests pass, but yet the file is colored orange, indicating something is wrong. The issue is that *you* have not yet declared that the results meet your own standard of scientific rigor. 

When you're using `vaibify` for your own research, you'll want to be very careful about marking a step as "Passed" because that means you are declaring the results to be accurate. While the declaration can of course be undone, waiting until you are sure is the best approach because it affects later steps. But for this example, go ahead and click the researcher badge for Step A01 and it will change to "Passed". After a few seconds, the left-most orange marker in the Step's banner for Step A01 will turn to a blue check, indicating this step has reached PROOF Level 1 (the delay is due to the `vaibify`'s polling). 

Next, do the same for Step A02. This time, when the dashboard detects the change, not only will the orange marker in the Step's banner flip to a check, the entire dashboard theme color will change to purple and you will now see a single check to the right of "AI Greenhouse" along the top bar. The number of checks next to the project name indicates the current PROOF Level of the project, Level 1 in this case.

### Doing the same thing with an agent

Using the dashboard to click through tasks is soooo 2025. Now we ask AI agents to perform tasks like "Run all steps and their unit tests." `vaibify` includes an [agent action catalog](forAgents.md#agent-actions) that provides deterministic commands that agents can pass to the host machine and run in your container. While this functionality might appear to expose your host machine to the container's agents, the commands are tightly monitored to only direct `vaibify` to perform operations inside a container. In this example, you are in host mode, though, so these operations would be performed on your local machine. For this guide, *don't type anything to an agent*, just know that you can use either the dashboard or a command-line agent to perform `vaibify` tasks.

## 6. Check the bytes again

So are the new files identical to the author's manifest? From the **Run** menu, choose **Check Files Against Manifest** again.

You will see an alert in the top right that tells you that some files did not match. Here's what happened:

- **The fitted values differ in their last digits.** A least-squares fit
  is a sequence of floating-point operations, and different environments'
  build orders and vectorization schemes are not always the same. The science is identical
  to twelve significant figures (the quantitative unit tests pass), which is close enough for most scientific reproducibility expectations. The bytes, however, are not identical.
- **The figures' bytes differ.** Matplotlib stamps its own version into
  every PNG it writes, so a different matplotlib version guarantees
  different bytes before the figure is ever generated, which also
  differs by platform.

Your run passed every *scientific test* (the unit tests) the project defines, but did
not reproduce byte-for-byte because your computing environment is slightly different from the one the author used. These differences arise because the rules that govern computing standards (defined by the IEEE) allow changes in the last digits of some functions, like transcendentals. Thus, progress from here requires rerunning the work in the *exact same environment* that the author used.

This next section, which describes how to generate byte-identical results, is technically optional, but encouraged. While `vaibify`'s behavior is the same in both "host mode" and "container mode", the agents in host mode have access to all of your computer (including secrets, environment variables, keychains, etc.) and byte-reproducibility cannot be guaranteed without a static environment. But at this point you can just use `vaibify` on your machine and trust the agents. Plenty of great science can be done on a project reaching PROOF Level 1 in host mode!

## 7. Containerize the same project

Before we begin this step, note the following:

- **The project must not be open anywhere else.** If it is open in the
  tab you are clicking from, `vaibify` closes it for you. A session in
  another browser or on another machine refuses the conversion instead,
  because the conversion renames the key that the project's lock,
  lease, and journal all hang from.
- **Docker or Colima must be installed.** This step requires Docker containers, which are run as `colima` on macOS. See the [install guide](install.md#docker-on-macos) to install. *Note that this process can require over 60 minutes.*
- **A failed build is left unfinished.** If the process fails for any reason, 
  a registered, but unusable, container remains partially built. Fix the cause
  and build again (try working with an AI agent or pressing **Run a diagnosis** in the build-failure window); you have not lost the host project's work, because there was never a copy to lose. Look for an orange warning glyph above the list of Environments.
- **This step may take over an hour.** The containerization requires downloading a 733 MB file from Zenodo.org. Generally the download will complete in <15 minutes, but depending on your connection and the traffic into Zenodo.org, it may take considerably longer.

Once you're ready, then from the dashboard, press the Admin pulldown menu in the top right corner and select Environments to return to the original landing page. Open the kebab menu (**⋮**) on the aigreenhouse environment's tile, and choose **Containerize Environment**. This launches a wizard that steps you through the process. Follow the instructions in the following table:


| # | Page | What to enter here |
|---|------|--------------------|
| 1 | **Project Name** | Set the **Project Name** to "AI Greenhouse" and note that the container's name changes to include a dash. Container names cannot have spaces or uppercase letters. Click "Next". |
| 2 | **Environment** | Leave **Use the author's pinned image** selected. The table beneath it names the image, the kind of processor it was built for (`linux/amd64`), and the archived copy on Zenodo. If your computer has a different processor ("This daemon"), such as `arm64`, check the **Allow emulation** box. (About 0.2% of emulation runs fail. If you see a "killed by signal 11" message while running with an emulator, just run again and it will very likely work.) Below that, under "How the author's image may run on this machine", choose **Run it as its author built it**, so the environment remains exactly as the author saved it. Click "Next". |
| 3 | **Features & Authentication** | Select one of the available coding agents. LaTeX and the other base features are grayed out because the author's image fixes them. For the remaining boxes, leave as their defaults unless you are sure. Under *Resource limits*, leave *CPU cores* at **all cores -1**, which leaves 1 reserved for the host (you can still use all of them outside the container); and *Memory (GB)* at **5**. (You can adjust this later if you choose to keep the container.) Click "Next".|
| 4 | **Files to Copy** | Leave everything at their defaults. Click "Next". |
| 5 | **Summary** | Read it and confirm it matches the above instructions. Click **Convert and obtain**. |

You will then see a dialog box that shows the log of the download of the 733 MB image from Zenodo. Depending on your connection, *this download may take an hour or more.* If the connection drops, `vaibify` resumes the download. If it keeps failing, `vaibify` pauses with the partial download kept and presents a "Try again" button. Note that if you cancel a download, the partial file is deleted.

When the conversion finishes, you return to the Environments Hub and the ai-greenhouse environment will look the same except the `host-mode` badges become `contained`. The steps still do the
same things, but now they do them in the pinned environment, and the
PROOF tab's Level 3 row stops saying the project has no image and instead reports on whether the reproducibility rules have been answered, and
whether a rebuild from that image reproduces the outputs.

This process does not create a second project. Your clone stays on your host machine — the same directory, the same git history, the same outputs you
just produced; only the runtime settings in its `vaibify.yml` change. The difference is that there is now an exact duplicate in a new Docker container whose environment is identical to the one the author used. Furthermore, your `vaibify` interactions will now take place inside this container leaving your own computer safe.

At this point, the environment is downloaded, but its container has not yet been started. You will be asked if you want to **Start container** and you should press that button. If at a later time you want to start a stopped container, you can click the kebab menu (**⋮**) and select **Start**. A dark red circle next to the environment's name means the container is stopped (not using host machine resources);  a blue check means the container is active. Once it starts, you will see a notice that confirms your files were copied into the container. Now go ahead and select the `ai-greenhouse` environment and then the `AI Greenhouse` Project to return to its dashboard.

### Confirm the reproduction

The container now holds the files your own computer made in
section 5. Let's replace them with files made in the author's
environment.

1. From the **Run** menu, choose **Check Files Against Manifest**. The
   data files and figures still differ, because they are still the
   bytes your own computer produced.
2. From the **Run** menu, choose **Run All Steps**. This time every step runs inside the author's environment,
   and the run log notes that it used the date the author recorded, so
   any figure with a date inside it comes out the same. Each step's Researcher marker now reads stale because the plot's bytes changed. Click it once and the badge reads "Passed".
3. Choose **Check Files Against Manifest** again. Every file now
   matches: in the author's environment, your run produced the
   author's bytes.

Finally, from the **Run** menu choose **Verify Level 3
Reproducibility**. It makes a throwaway copy of the container from
the author's image, reruns every step there with no network, compares
every file, and then deletes the copy. It leaves your files alone, and
it only starts once your files match the manifest, which is why it
comes after step 3. While running, you will see an orange **Reproducing** badge pulse. When it finishes, a **Reproduced** badge will appear along the top of the window and both the original MANIFEST.sha256 file and the just-reproduced version will appear in the viewing windows. (In cases where reproduction is unsuccessful, the badge does not persist and you instead see an error message.) The result is saved in `.vaibify/reproductions/` in your aigreenhouse clone. 

Your copy stays at Level 1. Levels 2 and 3 record the *author's*
publication (their GitHub push, their Zenodo deposit, their AI
declaration), so they are the author's to earn. Bringing a project to
Level 3 is the author's job; confirming it is yours, and the Reproduced badge is that confirmation.

Anyone can run the same check starting from nothing but the project's
URL: in the hub, press **+** and choose **Reproduce a published
project**. A reader without `vaibify` can run `./reproduce.sh` inside
the clone. Both obtain the author's image, rerun every step inside it,
and compare the bytes.


## 8. When something is wrong

A diagnosis is offered where a problem is reported: an error message
in the dashboard ends with **Click to run a diagnosis**, and the window
that reports a failed build or a failed download has a **Run a diagnosis** button. Click either and a report opens. It checks this
machine and gives you the exact command
to run to fix the issue.

When nothing is reporting an error but the dashboard reports
something you did not expect, you can run the same checks from a terminal on the host machine:

```bash
vaibify doctor
```

That report covers everything the dashboard reports, plus additional information about the container and vaibify's own record of the project; see the
[CLI Reference](cli.md).

Three things about the report are worth knowing before you read one.

**The diagnosis changes nothing.** It never starts, stops, removes or restarts
anything. You can run it against a container that has just died without
destroying the evidence of what happened.

**"Not checked" is not "ok".** A check that could not run is not labeled as working correctly. Instead, the doctor explains the reason for the issue. 

**The doctor tells you the command to fix an issue.** Where vaibify can
apply the fix itself, that command is `vaibify repair`. Where it
cannot — a network that is down, a daemon that needs more memory — the
finding says so plainly rather than sending you to a tab.

## 9. Where to next

- **[The three templates: sandbox, toolkit, workflow](environmentsAndProjects.md#templates)** —
  Start your own project rather than reproduce someone else's work.
- **[Environments and Projects](environmentsAndProjects.md#the-dashboard)** — Learn more about the dashboard: status colors, symbols, and how steps are verified.
- **[The PROOF Ladder](proofLadder.md)** — Get familiar with what Levels 1 through 3
  certify, what they don't do, and how to go beyond Level 3 (`vaibify`'s ceiling).
- **[Security model](security.md)** — Explore how `vaibify` protects your data and secrets, as well as how to ensure you don't dismantle the safeguards. Worth reading before you let an agent
  write code anywhere.
- **[The System Prompt](systemPrompt.md)** — Read the instructions that agents launched in `vaibify` are given so you know what to expect, what you don't need to repeat, and how to tailor your own prompt to cover what you think is missing. 
