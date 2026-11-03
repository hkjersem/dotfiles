# agent-awake

`agent-awake` keeps a MacBook awake, also with the lid closed and on battery,
while Copilot CLI, Claude Code or Codex is working on a turn. When the work is
done it lets the Mac sleep again, and if the lid is already closed it puts the
Mac to sleep right away. Open sessions that are waiting for you don't keep the
Mac awake.

It is opt-in: nothing installs it automatically, so run it only on Macs that
need it.

```sh
agent-awake --install              # root helper and agent hooks; asks for sudo
agent-awake --install --battery 30 # let the Mac sleep on battery below 30% (default 20)
agent-awake                        # status: helper, power, hooks, holds, wake requests, log
agent-awake --uninstall            # remove everything and allow sleep again
```

After installing, open `/hooks` in the Codex CLI once to trust the new hooks,
and restart running agent sessions so they load them.

## How it decides

Each agent gets hooks that run `scripts/agent-awake.py --hook`:

| Agent | Starts a hold | Marks it busy | Refreshes it | Releases it | Sub-agents |
|-------|---------------|---------------|--------------|-------------|------------|
| Copilot CLI (also in `cplt`) | `userPromptSubmitted` | `preToolUse` | `postToolUse` | `agentStop`, `errorOccurred` when not recoverable, permission prompts; `sessionEnd` releases all | `subagentStart`, `subagentStop` |
| Claude Code (also in `cplt`) | `UserPromptSubmit` | `PreToolUse` | `PostToolUse`, `PostToolUseFailure` | `Stop`, permission and idle prompts; `SessionEnd` releases all | `SubagentStart`, `SubagentStop` |
| Codex CLI and app | `UserPromptSubmit` | `PreToolUse` | `PostToolUse` | `Stop`, `PermissionRequest`; `Interrupt` and `SessionEnd` release all | `SubagentStart`, `SubagentStop` |

Copilot's hooks are in `~/.copilot/hooks/agent-awake.json`; Claude's are merged
into `~/.claude/settings.json` and Codex's into `~/.codex/hooks.json`, leaving
other hooks alone.

For terminal sessions launched through the dotfiles wrappers, the prompt hook
also publishes the session ID to `~/.cache/agent-sessions`. Terminal restoration
can use that observation even after the awake hold is released, without the
root helper. It does not keep an idle session awake. The wrappers still follow
agent logs when hooks are absent or a session was selected without a prompt.
The cplt wrapper forwards the two session-tag environment variables explicitly;
it does not disable environment filtering.
This only supplements a launch already tagged by the wrappers; submitting a
prompt cannot adopt an untracked or older untagged launch. `agent-sessions`
shows restoration records and titles, separately from awake holds.

A hold is a file in `~/.cache/agent-awake/holds` holding the agent's process
ID. A root LaunchDaemon, `com.hkjersem.agent-awake`, looks at them whenever
they change and every 30 seconds. A hold counts until:

- its agent process exits,
- its turn is released, plus 45 seconds so a turn that continues right away
  doesn't let the Mac sleep in between, or
- no tool call has refreshed it for 20 minutes, as after a crash or a turn
  stuck waiting, or for two hours while a tool call is running, such as a
  long build or test suite.

Each sub-agent start adds a sub-agent hold, named after the sub-agent's ID, and
its stop releases it, so background sub-agents keep the Mac awake after the
main turn ends. Copilot runs a sub-agent's tool hooks under the sub-agent's ID,
which refreshes its hold, and its stop or failure under that ID releases it;
the parent's tool calls don't refresh it, so a sub-agent that dies without a
stop event expires with the lease. Claude Code runs them under the parent's session,
which refreshes all of that session's holds and brings back a session hold that
was already released. The status shows each sub-agent as one row, "sub-agent
of" its parent session. Where a start event has no ID, sub-agents are counted
and each stop releases the oldest.

The status names sessions as the agents do: Copilot's session name from
`workspace.yaml` and a sub-agent's display name from its parent's
`events.jsonl`, Codex's thread name from `~/.codex/session_index.jsonl`, and
Claude Code's custom or generated title from its transcript. It shows the hold's
ID when there is no name. It lists the holds keeping the Mac awake and the
five most recent others; hooks delete holds a day after their last change.

While a hold counts, the helper sets `pmset disablesleep 1`, the only setting
that keeps a closed MacBook awake without an external display; `caffeinate`
and the agents' own keep-awake options only stop idle sleep. When no hold is
left it sets `disablesleep 0` and, if the lid is closed and no external display
is attached, runs `pmset sleepnow`. macOS can take a few seconds to report
that the closed lid causes sleep again, so the helper checks for up to ten
seconds before it leaves sleep to macOS. On battery below the `--battery` charge it
allows sleep even while an agent works.

The helper owns `disablesleep`: it clears a value you set by hand once no hold
is active.

## Scheduled wakes

Other commands can ask the helper to wake the Mac by writing a request to
`~/.cache/agent-awake/wake`. `codex-resume` does this for its scheduled runs.
The helper schedules wakes with `pmset schedule wake` 90, 60 and 30 seconds
before the requested time and at the time itself, also with the lid closed. A
scheduled wake is often only a dark wake of a few seconds, which can end
before launchd starts the helper; the first wake the helper catches keeps the
Mac awake, and the rest change nothing. The Mac stays awake from two
minutes before the time until the request is withdrawn, or at most ten
minutes after it. It cancels the wakes it scheduled that no request still
asks for. `pmset -g sched` lists them as `by 'agent-awake'`.

## Security

The helper runs as root, so the LaunchDaemon runs a root-owned copy in
`/Library/PrivilegedHelperTools`, not the script in this repository, which
agents can edit. `agent-awake --status` shows the helper as up to date while
the copy matches the repository, and as outdated once the script changes, for
example after a pull; run `agent-awake --install` again to update it. The
helper only reads the files in `~/.cache/agent-awake`, skips symlinks, reads at
most 64 bytes of each and uses only the digits; it never writes, deletes or
runs anything in your home folder. Its log is `/var/log/agent-awake.log`.

## Limits

- A closed MacBook in a bag can get warm while an agent works; nothing stops
  it on heat.
- A turn that only thinks for more than 20 minutes without a tool call lets
  the Mac sleep, and so does a single tool call running for over two hours.
- Pressing Esc in Claude Code fires no hook, so the turn's hold lasts until
  the 20-minute or two-hour limit, or until Claude exits.
- With parallel tool calls, the first one to finish ends the busy state while
  the others still run; they then have the 20-minute limit.
- Waiting for a permission releases the hold. If you approve and close the
  lid while that tool call runs, the Mac sleeps after 45 seconds; the next
  prompt or tool call holds it again.
- After a session ends, or a sub-agent stops, late tool calls from it no longer
  hold the Mac; only a new prompt does.
- Whether each agent's sub-agent and permission events fire as documented is
  unverified on this Mac.
- Whether the Codex desktop app runs these hooks, and whether a message
  queued by `codex-resume` counts as a submitted prompt, is unverified; the
  first tool call in the turn starts a hold either way.
- The process check is weak for the Codex app, whose app server keeps running
  between turns; the release hooks and the 20-minute limit still end its
  holds.

## Testing it on this Mac

1. Run `agent-awake --install`, trust the Codex hooks and restart sessions.
2. Give an agent a task that takes a few minutes, then run `agent-awake`: the
   hold shows as working and sleep as disabled.
3. Close the lid on battery. When the turn finishes, the log shows
   "sleeping now" and the Mac sleeps.
4. For wakes, schedule `codex-resume --time` a few minutes ahead, close the
   lid, and check that the message is sent on time.

Offline tests: `python3 -B -m unittest scripts.tests.test_agent_awake`.
