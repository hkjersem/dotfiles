# Copilot CLI and Claude Code sessions: resume them in terminals that VS Code
# or iTerm2 restores, and start them in the cplt sandbox with all permissions.
#
# Each interactive `copilot`, `claude` or `cplt` session gets a record that a
# normal exit removes. While it runs, a background loop follows the session
# the agent has open, so switching with /new, /clear or /resume is tracked
# too. A terminal closed with the session still running leaves the record
# behind. The first prompt of the next terminal opened in the same directory
# claims the oldest such record whose shell is gone and resumes that session,
# so several terminals in one repository each get their own session back.
#
# cplt has no config key for agent flags, and the agents' own permission
# settings would also apply outside the sandbox, so the cplt wrapper adds
# --allow-all for Copilot and --dangerously-skip-permissions for Claude Code.
# `command cplt`, `command copilot` or `command claude` skips the wrappers;
# CPLT_NO_RESUME=1 turns off resuming.

_agent_sessions_dir=${XDG_STATE_HOME:-$HOME/.local/state}/agent-sessions
_agent_sessions_copilot=${COPILOT_HOME:-$HOME/.copilot}/session-state
_agent_sessions_logs=${COPILOT_HOME:-$HOME/.copilot}/logs
_agent_sessions_claude=${CLAUDE_CONFIG_DIR:-$HOME/.claude}

# Prints when a process started, which tells a live shell apart from a later
# process that reuses its PID.
_agent_session_started() {
  emulate -L zsh
  print -r -- ${=$(ps -o lstart= -p $1 2>/dev/null)}
}

# Prints the process IDs below the given one.
_agent_session_descendants() {
  emulate -L zsh
  local pid ppid
  local -a queue
  local -A children
  ps -A -o pid= -o ppid= | while read -r pid ppid; do
    children[$ppid]+=" $pid"
  done
  queue=($1)
  while (( $#queue )); do
    pid=$queue[1]
    shift queue
    for ppid in ${=children[$pid]}; do
      print -r -- $ppid
      queue+=($ppid)
    done
  done
}

# Claude Code keeps sessions/<pid>.json with the session a process has open.
# Copilot logs "Registering foreground session: <id>" to
# logs/process-<time>-<pid>.log whenever a process starts, resumes or
# switches to a session, so the last such line names the current session.
# Older versions without that line are followed through inuse.<pid>.lock,
# which Copilot keeps in the folder of each session a process creates or
# writes to; of those, the one changed last is current. Writes the ID to
# <marker>.current.
_agent_session_follow() {
  emulate -L zsh -o extended_glob
  zmodload -F zsh/stat b:zstat
  local marker=$1 shell=$2 agent=$3 pid lock file current newest
  local -a pids locks logs mtime match
  while [[ -f $marker ]] && kill -0 $shell 2>/dev/null; do
    sleep ${AGENT_SESSION_FOLLOW_INTERVAL:-5}
    pids=(${(f)"$(_agent_session_descendants $shell)"})
    (( $#pids )) || continue
    if [[ $agent == claude ]]; then
      current=
      for pid in $pids; do
        file=$_agent_sessions_claude/sessions/$pid.json
        [[ -f $file && "$(<$file)" =~ '"sessionId": *"([0-9a-f-]+)"' ]] && current=$match[1]
      done
      [[ -n $current && -f $marker ]] && print -r -- $current > "$marker.current"
      continue
    fi
    # A log last written before this launch belongs to an earlier process
    # that had the same PID.
    logs=("$_agent_sessions_logs"/process-*-(${~${(j:|:)pids}}).log(N.om))
    current=
    [[ -n $logs[1] && ! $logs[1] -ot $marker ]] &&
      current=${${"$(grep -h 'Registering foreground session: ' $logs[1] | tail -n 1)"}##*: }
    if [[ $current == [0-9a-f](#c8)-* ]]; then
      [[ -f $marker ]] && print -r -- $current > "$marker.current"
      continue
    fi
    locks=("$_agent_sessions_copilot"/*/inuse.(${~${(j:|:)pids}}).lock(N))
    current= newest=0
    for lock in $locks; do
      for file in $lock ${lock:h}/events.jsonl(N); do
        zstat -A mtime +mtime -- $file 2>/dev/null || continue
        (( mtime[1] >= newest )) || continue
        newest=$mtime[1]
        current=${${lock:h}:t}
      done
    done
    [[ -n $current && -f $marker ]] && print -r -- $current > "$marker.current"
  done
}

_agent_session_is_uuid() {
  emulate -L zsh -o extended_glob
  [[ $1 == [0-9a-f](#c8)-[0-9a-f](#c4)-[0-9a-f](#c4)-[0-9a-f](#c4)-[0-9a-f](#c12) ]]
}

# Sets REPLY to the session ID in an agent's arguments. Returns 0 for an
# existing session, 2 for a new one that needs --session-id, 3 for a session
# only the follower can name, and 1 when the run is not an interactive
# session that can be resumed by ID.
_agent_session_claude_id() {
  emulate -L zsh
  local arg prev value continued
  REPLY=
  for arg in "$@"; do
    case $prev in
      -r|--resume|--session-id) _agent_session_is_uuid $arg && value=$arg ;;
    esac
    case $arg in
      -p|--print|--bg|--background|--desktop|--cloud|--cloud=*) return 1 ;;
      -c|--continue|-r|--resume|--from-pr|--from-pr=*|--fork-session) continued=1 ;;
      --resume=*|--session-id=*) _agent_session_is_uuid ${arg#*=} && value=${arg#*=} ;;
    esac
    prev=$arg
  done
  if [[ -n $value ]]; then
    REPLY=$value
    return 0
  fi
  [[ -n $continued ]] && return 3
  REPLY=${(L)$(uuidgen)}
  return 2
}

_agent_session_copilot_id() {
  emulate -L zsh
  local arg value prev
  REPLY=
  for arg in "$@"; do
    case $prev in
      -r|--resume|--session-id)
        [[ $arg == -* ]] && return 1
        value=$arg
        ;;
    esac
    case $arg in
      -p|--prompt|--prompt=*|--continue|--connect|--connect=*|--acp|--server) return 1 ;;
      --resume=*|--session-id=*) value=${arg#*=} ;;
    esac
    prev=$arg
  done
  [[ $prev == -r || $prev == --resume || $prev == --session-id ]] && return 1
  if [[ -n $value ]]; then
    _agent_session_is_uuid $value || return 1
    REPLY=$value
    return 0
  fi
  REPLY=${(L)$(uuidgen)}
  return 2
}

# Usage: _agent_session_hung_up <fd>
# Succeeds when the terminal on fd has been closed, as it then rejects
# writes. Open terminals ignore the NUL written.
_agent_session_hung_up() {
  emulate -L zsh
  zmodload zsh/system 2>/dev/null || return 1
  ! syswrite -o $1 $'\0' 2>/dev/null
}

# Usage: _agent_session_run <command> <agent> <command args> -- <agent args>
# Runs the agent itself, or puts `--` and the permission flag before the
# agent's arguments for cplt.
_agent_session_run() {
  emulate -L zsh
  local launcher=$1 agent=$2 sep id tracked marker follower ret tty fd
  shift 2
  sep=${@[(i)--]}
  local -a launcher_args=("${@[1,sep-1]}") agent_args=("${@[sep+1,-1]}")

  _agent_session_${agent}_id "${agent_args[@]}"
  case $? in
    0) id=$REPLY tracked=1 ;;
    2) id=$REPLY tracked=1; agent_args=(--session-id "$id" "${agent_args[@]}") ;;
    3) tracked=1 ;;
  esac
  if [[ -n $tracked ]] && mkdir -p "$_agent_sessions_dir"; then
    # Named per launch, so a claimed record is never recreated under the
    # name another restored terminal may be about to claim.
    marker=$_agent_sessions_dir/${(L)$(uuidgen)}
    print -r -- "${PWD:A}"$'\n'"$launcher"$'\n'"${(j: :)${(q)launcher_args[@]}}"$'\n'$$$'\n'"$(_agent_session_started $$)"$'\n'"$id"$'\n'"$agent" > "$marker"
    _agent_session_follow "$marker" $$ $agent </dev/null >/dev/null 2>&1 &!
    follower=$!
    for fd in 1 2 0; do
      [[ -t $fd ]] && { tty=$fd; break }
    done
  fi
  # The always block also runs when Ctrl-C ends the agent and the function.
  # When the terminal closes, it hangs up the agent, which may exit normally
  # before zsh is hung up, for example when iTerm2 runs zsh under login.
  {
    if [[ $launcher != cplt ]]; then
      command $agent "${agent_args[@]}"
    elif [[ $agent == claude ]]; then
      command cplt "${launcher_args[@]}" -- --dangerously-skip-permissions "${agent_args[@]}"
    else
      command cplt "${launcher_args[@]}" -- --allow-all "${agent_args[@]}"
    fi
    ret=$?
  } always {
    [[ -n $follower ]] && kill $follower 2>/dev/null
    if [[ -n $marker ]] && ! { [[ -n $tty ]] && _agent_session_hung_up $tty }; then
      rm -f -- "$marker" "$marker.current"
    fi
  }
  return ret
}

if (( $+commands[copilot] )); then
  copilot() {
    emulate -L zsh
    case $1 in
      app|login|help|init|config|update|version|workflow|sessions|memories|plugin|mcp|skill|instruction|lsp|sandbox|completion|-h|--help|-v|--version)
        command copilot "$@"
        ;;
      *) _agent_session_run copilot copilot -- "$@" ;;
    esac
  }
fi

if (( $+commands[claude] )); then
  claude() {
    emulate -L zsh
    case $1 in
      agents|attach|auth|auto-mode|config|doctor|gateway|import|install|logs|mcp|migrate-installer|plugin|plugins|purge|respawn|rm|setup-token|stop|kill|ultrareview|update|upgrade|-h|--help|-v|--version)
        command claude "$@"
        ;;
      *) _agent_session_run claude claude -- "$@" ;;
    esac
  }
fi

if (( $+commands[cplt] )); then
  # Agent-specific arguments only go to Copilot and Claude Code: the agent is
  # --agent, else sandbox.agent from cplt's config, else Copilot when it is on
  # PATH, as cplt auto-detects it first. Any word before -- that is not an
  # option value is a cplt subcommand, which runs unchanged.
  cplt() {
    emulate -L zsh
    local arg agent prev sep
    sep=${@[(i)--]}
    for arg in "${@[1,sep-1]}"; do
      case $prev in
        --agent) agent=$arg ;;
        --preset|-d|--project-dir|--repo-dir|--proxy-port|--blocked-domains|--allowed-domains|--observe-domains-out|--proxy-log|--proxy-log-level|--proxy-timeout|--proxy-upstream|--proxy-upstream-no-proxy|--allow-private-domain|--allow-read|--allow-write|--allow-exec|--allow-socket|--deny-path|--allow-port|--allow-localhost|--pass-env|--allow-cache-exec|--name) ;;
        *)
          case $arg in
            -h|--help|-V|--version|[^-]*)
              command cplt "$@"
              return
              ;;
            --agent=*) agent=${arg#--agent=} ;;
          esac
          ;;
      esac
      prev=$arg
    done
    if [[ -z $agent ]]; then
      agent=$(command cplt config get sandbox.agent 2>/dev/null)
      [[ -z $agent ]] && (( $+commands[copilot] )) && agent=copilot
    fi
    if [[ $agent == (copilot|claude) ]]; then
      _agent_session_run cplt $agent "${@[1,sep-1]}" -- "${@[sep+1,-1]}"
    else
      command cplt "$@"
    fi
  }
fi

# VS Code can revive terminals it never shows. Those keep its unsized default
# of 80x30, so leave the session for a terminal that gets a real size.
_agent_session_displayed() {
  emulate -L zsh
  [[ $TERM_PROGRAM == vscode ]] || return 0
  local i
  for i in {1..20}; do
    [[ $(command stty size 2>/dev/null) == '30 80' ]] || return 0
    sleep 0.1
  done
  return 1
}

_agent_session_resume() {
  emulate -L zsh -o extended_glob
  add-zsh-hook -d precmd _agent_session_resume
  [[ -t 0 && -z $CPLT_NO_RESUME && -z $__CPLT_WRAPPED && -d $_agent_sessions_dir ]] || return
  local f cwd launcher line pid started claim id agent
  local -a launcher_args resume
  rm -f -- "$_agent_sessions_dir"/*(N.m+7)
  for f in "$_agent_sessions_dir"/^*.*(N.Om); do
    cwd= launcher= line= pid= started= id= agent=
    { IFS= read -r cwd; IFS= read -r launcher; IFS= read -r line; IFS= read -r pid
      IFS= read -r started; IFS= read -r id; IFS= read -r agent } < "$f"
    [[ $cwd == "${PWD:A}" ]] || continue
    [[ $pid == <-> ]] && kill -0 $pid 2>/dev/null &&
      [[ -z $started || $started == "$(_agent_session_started $pid)" ]] && continue
    [[ $launcher == (copilot|claude|cplt) && $agent == (copilot|claude) ]] &&
      (( $+functions[$launcher] )) || continue
    _agent_session_displayed || return
    claim=$f.$$
    command mv -- "$f" "$claim" 2>/dev/null || continue
    [[ -f $f.current ]] && IFS= read -r id < "$f.current"
    rm -f -- "$claim" "$f.current"
    _agent_session_is_uuid "$id" || continue
    if [[ $agent == claude ]]; then
      [[ -n $(print -r -- "$_agent_sessions_claude"/projects/*/$id.jsonl(N[1])) ]] || continue
      print -P "%F{cyan}Resuming Claude Code session $id%f"
      resume=(--resume "$id")
    else
      [[ -f $_agent_sessions_copilot/$id/events.jsonl ]] || continue
      print -P "%F{cyan}Resuming Copilot session $id%f"
      resume=(--resume="$id")
    fi
    if [[ $launcher == cplt ]]; then
      [[ -n $line ]] && launcher_args=("${(Q@)${(z)line}}")
      # Keep the recorded agent even if cplt's default has changed since.
      (( ${launcher_args[(I)--agent|--agent=*]} )) || launcher_args=(--agent $agent "${launcher_args[@]}")
      cplt "${launcher_args[@]}" -- "${resume[@]}"
    else
      $launcher "${resume[@]}"
    fi
    return
  done
}

if [[ -o interactive ]]; then
  autoload -Uz add-zsh-hook
  add-zsh-hook precmd _agent_session_resume
fi
