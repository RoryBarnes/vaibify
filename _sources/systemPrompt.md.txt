# System Prompt

Every coding agent working inside a vaibify container reads the same
set of standing instructions, called a *system prompt*, before it reads
anything you type. The prompt explains what vaibify is, the rules of
the container, and the PROOF Ladder, and it tells the agent when to use
one of vaibify's deterministic actions instead of improvising its own
solution. It describes how to work inside vaibify, not how to do any
particular science.

Vaibify writes the prompt to `CLAUDE.md` at the root of the container's
workspace every time the container starts, and links `AGENTS.md` and
`GEMINI.md` to it, so every installed agent reads the same text.
Because it is rewritten at every start, editing it inside the container
has no lasting effect. Your own instructions belong in your project's
context file instead, which vaibify never overwrites; see
[For Agents](forAgents.md), which also describes the skills and actions
the prompt refers to.

The text below is exactly what vaibify writes. It is read from
vaibify's container startup script whenever this documentation is
built, so it always matches the version of vaibify described here.

```{include} ../vaibify/containerImage/entrypoint.sh
:start-after: << 'CLAUDEMD'
:end-before: CLAUDEMD
:heading-offset: 1
```
