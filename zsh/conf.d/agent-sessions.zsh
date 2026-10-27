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
_agent_sessions_observed=$HOME/.cache/agent-sessions

# Prints when a process started, which tells a live shell apart from a later
# process that reuses its PID.
_agent_session_started() {
  emulate -L zsh
  print -r -- ${=$(ps -o lstart= -p $1 2>/dev/null)}
}

# Prints descendant process IDs and their start times, in epoch seconds.
_agent_session_descendants() {
  emulate -L zsh
  zmodload zsh/datetime
  local -x LC_ALL=C
  local pid ppid started epoch
  local -a queue
  local -A children starts
  ps -A -o pid= -o ppid= -o lstart= | while read -r pid ppid started; do
    children[$ppid]+=" $pid"
    starts[$pid]=$started
  done
  queue=($1)
  while (( $#queue )); do
    pid=$queue[1]
    shift queue
    for ppid in ${=children[$pid]}; do
      strftime -r -s epoch '%a %b %e %T %Y' "$starts[$ppid]" || continue
      print -r -- "$ppid $epoch"
      queue+=($ppid)
    done
  done
}

_agent_session_process_matches() {
  emulate -L zsh
  zmodload zsh/datetime
  local -x LC_ALL=C
  local started=$(_agent_session_started "$1") epoch
  [[ -n $started ]] || return 1
  strftime -r -s epoch '%a %b %e %T %Y' "$started" || return 1
  (( epoch == $2 ))
}

# Claude Code keeps sessions/<pid>.json with the session a process has open.
# Copilot logs "Registering foreground session: <id>" to
# logs/process-<time>-<pid>.log whenever a process starts, resumes or
# switches to a session, so the last such line names the current session.
# Older versions without that line are followed through inuse.<pid>.lock,
# which Copilot keeps in the folder of each session a process creates or
# writes to; of those, the one changed last is current. Writes the ID to
# <marker>.current. A hook observation also works before the first poll.
_agent_session_capture() {
  emulate -L zsh -o extended_glob
  zmodload -F zsh/stat b:zstat
  local marker=$1 agent=$2 mode=${3:-live} pid started sampled=0 stamp lock file current
  local activity newest=0
  local -a locks logs mtime match fallback
  local -A starts
  [[ -f $marker ]] || return 1
  if [[ -f $marker.pids ]]; then
    {
      IFS= read -r sampled
      if [[ $sampled =~ '^[0-9]+([.][0-9]+)?$' ]]; then
        while read -r pid started; do
          [[ $pid == <-> && $started == <-> ]] && starts[$pid]=$started
        done
      fi
    } < "$marker.pids"
  fi
  if [[ $agent == claude ]]; then
    for pid in ${(k)starts}; do
      file=$_agent_sessions_claude/sessions/$pid.json
      [[ -f $file ]] || continue
      [[ $mode == live ]] || _agent_session_process_matches "$pid" "$starts[$pid]" || continue
      zstat -A mtime +mtime -- "$file" || continue
      (( mtime[1] >= starts[$pid] && mtime[1] <= sampled )) || continue
      [[ "$(<$file)" =~ '"sessionId": *"([0-9a-f-]+)"' ]] && current=$match[1]
    done
  elif (( $#starts )); then
    # The log's creation timestamp must belong to the sampled process
    # generation. A later process reusing its PID has a different prefix.
    logs=("$_agent_sessions_logs"/process-*-(${~${(j:|:)${(k)starts}}})(|.<->).log(N.om))
    for file in $logs; do
      [[ ${file:t} =~ '^process-([0-9]+)-([0-9]+)(\.[0-9]+)?\.log$' ]] || continue
      stamp=$match[1] pid=$match[2]
      (( stamp / 1000.0 >= starts[$pid] && stamp / 1000.0 <= sampled )) || continue
      current=${${"$(grep -h 'Registering foreground session: ' "$file" | tail -n 1)"}##*: }
      _agent_session_is_uuid "$current" && break
    done
    if ! _agent_session_is_uuid "$current"; then
      current=
      locks=("$_agent_sessions_copilot"/*/inuse.(${~${(j:|:)${(k)starts}}}).lock(N))
      for lock in $locks; do
        pid=${${lock:t}#inuse.}
        pid=${pid%.lock}
        [[ $mode == live ]] || _agent_session_process_matches "$pid" "$starts[$pid]" || continue
        zstat -A mtime +mtime -- "$lock" || continue
        (( mtime[1] >= starts[$pid] && mtime[1] <= sampled )) || continue
        for file in $lock ${lock:h}/events.jsonl(N); do
          zstat -A mtime +mtime -- $file 2>/dev/null || continue
          (( mtime[1] >= newest )) || continue
          newest=$mtime[1]
          current=${${lock:h}:t}
        done
      done
    fi
  fi
  file=$_agent_sessions_observed/${marker:t}
  fallback=("$marker.current" "$file")
  [[ -f $file && $file -nt $marker.current ]] && fallback=("$file" "$marker.current")
  for file in "${fallback[@]}"; do
    _agent_session_is_uuid "$current" && break
    [[ -f $file ]] && IFS= read -r current < "$file"
  done
  _agent_session_is_uuid "$current" && [[ -f $marker ]] || return 1
  activity=$marker
  for file in "$marker.current" "$marker.pids" "$_agent_sessions_observed/${marker:t}"; do
    [[ -f $file && $file -nt $activity ]] && activity=$file
  done
  print -r -- "$current" > "$marker.current.tmp" || return 1
  [[ $mode == live ]] || touch -r "$activity" "$marker.current.tmp" || return 1
  command mv -- "$marker.current.tmp" "$marker.current" || return 1
  # Observing the selected session hands ownership to the new record.
  [[ -z $_agent_session_resume_claim ]] || rm -f -- "$_agent_session_resume_claim"
  return 0
}

_agent_session_follow() {
  emulate -L zsh
  zmodload zsh/datetime
  local marker=$1 shell=$2 agent=$3 interval attempts=0
  local -a pids
  while [[ -f $marker ]] && kill -0 $shell 2>/dev/null; do
    pids=(${(f)"$(_agent_session_descendants $shell)"})
    print -rl -- "$EPOCHREALTIME" "${pids[@]}" > "$marker.pids.tmp" &&
      command mv -- "$marker.pids.tmp" "$marker.pids"
    interval=${AGENT_SESSION_FOLLOW_INTERVAL:-5}
    if ! _agent_session_capture "$marker" "$agent" && (( attempts++ < 10 )); then
      interval=0.1
    fi
    sleep "$interval"
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
  local arg value prev selected
  REPLY=
  for arg in "$@"; do
    case $prev in
      -r|--resume|--session-id)
        [[ $arg != -* ]] && value=$arg
        ;;
    esac
    case $arg in
      -p|--prompt|--prompt=*|--connect|--connect=*|--acp|--server) return 1 ;;
      -r|--resume|--continue|--session-id) selected=1 ;;
      --resume=*|--session-id=*) selected=1; value=${arg#*=} ;;
    esac
    prev=$arg
  done
  if [[ -n $value ]] && _agent_session_is_uuid $value; then
    REPLY=$value
    return 0
  fi
  [[ -n $selected ]] && return 3
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

_agent_session_forget() {
  emulate -L zsh
  local marker=$1
  rm -f -- "$marker" "$marker.current" "$marker.current.tmp" "$marker.pids" "$marker.pids.tmp" \
    "$_agent_sessions_observed/${marker:t}"
}

_agent_session_expired() {
  emulate -L zsh
  zmodload zsh/datetime
  zmodload -F zsh/stat b:zstat
  local -a mtime
  zstat -A mtime +mtime -- "$1" 2>/dev/null || return 1
  (( EPOCHSECONDS - mtime[1] >= 7 * 86400 ))
}

# Usage: _agent_session_run <command> <agent> <command args> -- <agent args>
# Runs the agent itself, or puts `--` and the permission flag before the
# agent's arguments for cplt. REPLY names the new record if a hangup kept it.
_agent_session_run() {
  emulate -L zsh
  local launcher=$1 agent=$2 sep id tracked marker follower ret tty fd
  shift 2
  sep=${@[(i)--]}
  local -a launcher_args=("${@[1,sep-1]}") agent_args=("${@[sep+1,-1]}")
  local -a context_args
  local -x DOTFILES_AGENT_SESSION= DOTFILES_AGENT_KIND=$agent

  if [[ $launcher == cplt && $agent == copilot ]]; then
    _agent_session_copilot_id "${launcher_args[@]}" "${agent_args[@]}"
  else
    _agent_session_${agent}_id "${agent_args[@]}"
  fi
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
    DOTFILES_AGENT_SESSION=${marker:t}
    context_args=(--pass-env DOTFILES_AGENT_SESSION --pass-env DOTFILES_AGENT_KIND)
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
      command cplt "${launcher_args[@]}" "${context_args[@]}" -- --dangerously-skip-permissions "${agent_args[@]}"
    else
      command cplt "${launcher_args[@]}" "${context_args[@]}" -- --allow-all "${agent_args[@]}"
    fi
    ret=$?
  } always {
    [[ -n $follower ]] && kill $follower 2>/dev/null
    REPLY=
    if [[ -n $marker ]]; then
      _agent_session_capture "$marker" "$agent" restore
      if [[ -n $tty ]] && _agent_session_hung_up $tty; then
        REPLY=$marker
      else
        _agent_session_forget "$marker"
      fi
      rm -f -- "$marker.pids" "$marker.pids.tmp"
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
    local arg agent prev sep help line
    local -a match
    local -A value_flags
    sep=${@[(i)--]}
    help=$(command cplt --help) || {
      print -u2 -- "Error: could not read cplt options."
      return 1
    }
    for line in ${(f)help}; do
      if [[ $line =~ '^[[:space:]]*((-[[:alnum:]]), )?(--[a-z0-9-]+) <' ]]; then
        value_flags[$match[3]]=1
        [[ -n $match[2] ]] && value_flags[$match[2]]=1
      fi
    done
    [[ -n $value_flags[--agent] ]] || {
      print -u2 -- "Error: could not identify value-taking cplt options."
      return 1
    }
    for arg in "${@[1,sep-1]}"; do
      if [[ -n $prev ]]; then
        [[ $prev == --agent ]] && agent=$arg
        prev=
        continue
      fi
      case $arg in
        -h|--help|-V|--version|[^-]*)
          command cplt "$@"
          return
          ;;
        --agent=*) agent=${arg#--agent=} ;;
      esac
      [[ -n $value_flags[$arg] ]] && prev=$arg
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

# VS Code's hidden restored terminals keep its unsized default of 80x30.
_agent_session_displayed() {
  emulate -L zsh
  [[ $TERM_PROGRAM == vscode ]] || return 0
  [[ $LINES != 30 || $COLUMNS != 80 ]]
}

_agent_session_stop_waiting() {
  emulate -L zsh
  unset _agent_session_waiting
  local i
  if (( $+parameters[zsh_scheduled_events] )); then
    for (( i=$#zsh_scheduled_events; i > 0; --i )); do
      [[ $zsh_scheduled_events[i] == *:_agent_session_retry ]] && sched -$i
    done
  fi
  (( $+functions[add-zle-hook-widget] )) || return 0
  add-zle-hook-widget -d line-pre-redraw _agent_session_resume_on_redraw
  add-zle-hook-widget -d line-finish _agent_session_stop_waiting
}

_agent_session_retry() {
  emulate -L zsh
  zle && zle _agent_session_resume_on_redraw
  [[ -n $_agent_session_waiting ]] && sched +1 _agent_session_retry
  return 0
}

_agent_session_resume_on_redraw() {
  emulate -L zsh
  [[ -z $BUFFER ]] && _agent_session_displayed || return 0
  zle -I
  _agent_session_resume </dev/tty
}

_agent_session_resume() {
  emulate -L zsh -o extended_glob
  add-zsh-hook -d precmd _agent_session_resume
  _agent_session_stop_waiting
  [[ -t 0 && -z $CPLT_NO_RESUME && -z $__CPLT_WRAPPED && -d $_agent_sessions_dir ]] || return
  local f cwd launcher line pid started claim id agent activity file owner ret=0
  local _agent_session_resume_claim
  local -a launcher_args resume claims
  for file in "$_agent_sessions_dir"/*.*(N.m+6); do
    f=${file:h}/${${file:t}%%.*}
    _agent_session_is_uuid "${f:t}" || continue
    [[ -f $f ]] && continue
    claims=("$f".<->(N.))
    owner=
    for claim in $claims; do
      kill -0 ${claim:e} 2>/dev/null && owner=1
    done
    [[ -n $owner ]] && continue
    case ${${file:t}#*.} in
      current|current.tmp|pids|pids.tmp|<->)
        rm -f -- "$file" "$_agent_sessions_observed/${f:t}"
        ;;
    esac
  done
  for f in "$_agent_sessions_dir"/^*.*(N.Om); do
    cwd= launcher= line= pid= started= id= agent=
    { IFS= read -r cwd; IFS= read -r launcher; IFS= read -r line; IFS= read -r pid
      IFS= read -r started; IFS= read -r id; IFS= read -r agent } < "$f"
    [[ $pid == <-> ]] && kill -0 $pid 2>/dev/null &&
      [[ -z $started || $started == "$(_agent_session_started $pid)" ]] && continue
    activity=$f
    for file in "$f.current" "$_agent_sessions_observed/${f:t}"; do
      [[ -f $file && $file -nt $activity ]] && activity=$file
    done
    if _agent_session_expired "$activity"; then
      _agent_session_forget "$f"
      continue
    fi
    [[ $cwd == "${PWD:A}" ]] || continue
    [[ $launcher == (copilot|claude|cplt) && $agent == (copilot|claude) ]] &&
      (( $+functions[$launcher] )) || continue
    if ! _agent_session_displayed; then
      zmodload zsh/zle
      autoload -Uz add-zle-hook-widget
      add-zle-hook-widget line-pre-redraw _agent_session_resume_on_redraw
      add-zle-hook-widget line-finish _agent_session_stop_waiting
      zmodload -F zsh/sched b:sched
      _agent_session_waiting=1
      sched +1 _agent_session_retry
      return
    fi
    # SIGHUP can end zsh before its always block runs. The saved process
    # IDs still identify the final session in logs left by the dead agent.
    if [[ -f $f.pids ]]; then
      _agent_session_capture "$f" "$agent" restore
    fi
    claim=$f.$$
    command mv -- "$f" "$claim" 2>/dev/null || continue
    [[ -f $f.current ]] && IFS= read -r id < "$f.current"
    file=$_agent_sessions_observed/${f:t}
    if [[ -f $file && ( ! -f $f.current || $file -nt $f.current ) ]]; then
      IFS= read -r id < "$file"
    fi
    if ! _agent_session_is_uuid "$id"; then
      rm -f -- "$claim"
      _agent_session_forget "$f"
      continue
    fi
    if [[ $agent == claude ]]; then
      if [[ -z $(print -r -- "$_agent_sessions_claude"/projects/*/$id.jsonl(N[1])) ]]; then
        rm -f -- "$claim"
        _agent_session_forget "$f"
        continue
      fi
      print -P "%F{cyan}Resuming Claude Code session $id%f"
      resume=(--resume "$id")
    else
      if [[ ! -f $_agent_sessions_copilot/$id/events.jsonl ]]; then
        rm -f -- "$claim"
        _agent_session_forget "$f"
        continue
      fi
      print -P "%F{cyan}Resuming Copilot session $id%f"
      resume=(--resume="$id")
    fi
    _agent_session_resume_claim=$claim
    REPLY=
    {
      if [[ $launcher == cplt ]]; then
        [[ -n $line ]] && launcher_args=("${(Q@)${(z)line}}")
        # Resume the followed session, not the original picker or selection.
        launcher_args=("${(@)launcher_args:#(--resume|--resume=*|--continue)}")
        # Keep the recorded agent even if cplt's default has changed since.
        (( ${launcher_args[(I)--agent|--agent=*]} )) || launcher_args=(--agent $agent "${launcher_args[@]}")
        cplt "${launcher_args[@]}" -- "${resume[@]}"
      else
        $launcher "${resume[@]}"
      fi
      ret=$?
    } always {
      if [[ -f $claim ]] && (( ret && ret != 130 )) && [[ -z $REPLY || ! -f $REPLY ]]; then
        if command mv -- "$claim" "$f"; then
          print -u2 -- "Error: could not resume $agent session $id (exit $ret); its record was kept."
        else
          print -u2 -- "Error: could not restore the resume record; it remains at $claim."
        fi
      else
        rm -f -- "$claim"
        _agent_session_forget "$f"
      fi
    }
    return ret
  done
}

if [[ -o interactive ]]; then
  autoload -Uz add-zsh-hook
  add-zsh-hook precmd _agent_session_resume
fi
