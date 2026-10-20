---
name: graceful-exits
description: Find places where a project's scripts could fail silently or with an unhelpful error, and fix them one at a time with the researcher reviewing each change. Use when asked to harden scripts, make errors clearer, or check how a step fails.
---

# Making scripts fail clearly

Run the checker first; do not guess where failures hide:

    python3 /usr/share/vaibify/skills/graceful-exits/checkGracefulExits.py <paths>

It finds KNOWN patterns. It cannot show that a script exits gracefully
on every failure, and a language it reports as NOT CHECKED was not
checked. Say both when you report.

For each finding, one at a time:

1. Make the failure stop the script with a message that names the
   failing resource (the file, the command, the parameter) and what was
   expected. Exit non-zero.
2. Validate inputs once, at the boundary (on load, before the loop).
   Never add checks inside a hot numerical loop.
3. Never change a computed result to make a check pass, and never
   catch an error just to continue.
4. Show the researcher the diff before moving on.

Rerun the checker after the fixes and report what remains.
