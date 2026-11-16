# Orchestration per tool

Checked against the vendors' documentation in November 2026. Tool and agent
names change between versions; check the tools you actually have before
relying on a row below. Sources:
[Claude Code subagents](https://code.claude.com/docs/en/sub-agents),
[Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[Copilot CLI custom agents](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli).

| Step | Copilot CLI | Claude Code | Codex |
|---|---|---|---|
| Spawn a subagent | `task` tool with an agent type | `Agent` tool (formerly `Task`) with a subagent type | Explicitly spawn an agent; Codex delegates only when told to |
| Read-only scout | `explore` agent type | `Explore` subagent | `explorer` agent; subagents inherit your sandbox, so read-only is an instruction only |
| Worker | `task` or `general-purpose` | `general-purpose` | `worker` |
| Independent review | `rubber-duck` (another model family) or `code-review` | A reviewer subagent on another model | A spawned reviewer on another model or effort; `/review` is user-run |
| Model per dispatch | `model` parameter | `model` parameter | Name the model and reasoning effort in the spawn request |
| Parallel | Several background tasks in one turn | Several `Agent` calls in one turn; they run in the background by default | Spawn several agents and say whether to wait for all |
| Collect results | Wait for the completion notice, then read once | Completion notice; only the final message returns | Codex waits and consolidates when asked |
| Short-lived orchestration state | Session todos or plan | Todo list | Plan tool |

Notes:

- **Copilot CLI:** custom agents do not load the repository's custom
  instructions unless configured to, so put every rule a subagent must follow
  in the brief. The user may set model preferences per agent type with
  `/subagents`; respect them. `/fleet` and
  `/delegate` are user commands; `/delegate` hands work to the cloud agent and
  opens a pull request, so do not suggest it without the user's request.
- **Claude Code:** a forked subagent starts with the whole conversation, though
  its own tool output stays out of yours. Prefer a fresh subagent with a brief,
  and always one for independent review. Subagents can nest, so tell every
  subagent not to spawn its own. The `model` parameter takes aliases such as
  `haiku`, `sonnet` and `opus`.
- **Codex:** say "spawn", how to split the work, whether to wait, and what each
  agent returns. Custom agent files cannot narrow the sandbox; set permissions
  on the parent session.
- **Other tools:** map the steps to whatever the tool offers. Without a
  subagent tool, work inline.

## Open questions

Not confirmed in the documentation; when it matters, ask the user or say what
was assumed.

- Copilot CLI: whether a `model` given per dispatch overrides the `/subagents`
  preferences, and whether built-in agents load custom instructions.
- Codex: how deep spawned agents can nest.
- Claude Code: whether it reads skills from `.agents/skills` as well as
  `.claude/skills`.
