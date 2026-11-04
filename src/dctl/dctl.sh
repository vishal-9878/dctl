#!/usr/bin/env bash
# dctl - agentless multi-server Docker helper.
# Runs on YOUR machine. Servers need nothing new: only ssh + the docker they already have.
#
#   dctl                  interactive picker (all containers on all servers)
#   dctl ls [text]        list containers on all servers (optionally filtered)
#   dctl where <name>     show server + compose file path of a container
#   dctl edit <name>      edit its compose file (download -> $EDITOR -> diff -> validate -> backup -> upload)
#   dctl up <name>        docker compose up -d for its project
#   dctl logs <name>      tail + follow logs
#   dctl sh <name>        shell inside the container
#   dctl cd <name>        ssh into the server, inside the compose directory
#   dctl inspect <name>   docker inspect
#   dctl restart|stop|start <name>
#   dctl ps <name>        docker compose ps for its project
#   dctl servers          show configured servers
#   dctl web              open the browser UI (dctl-ui)
#
# <name> can be partial ("api"), or "server:name" to be exact.
set -o pipefail

SELF=$(readlink -f "$0" 2>/dev/null || echo "$0")
CONF_DIR="${DCTL_CONF:-$HOME/.config/dctl}"
SERVERS_FILE="$CONF_DIR/servers"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/dctl"
CACHE="$CACHE_DIR/containers.tsv"
mkdir -p "$CACHE_DIR"
SSH_BASE=(-o ConnectTimeout=6 -o ServerAliveInterval=15
          -o ControlMaster=auto -o "ControlPath=$CACHE_DIR/cm-%C" -o ControlPersist=180)
US=$'\x1f'

if [ -t 1 ]; then R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' B=$'\e[1m' D0=$'\e[2m' N=$'\e[0m'; else R= G= Y= B= D0= N=; fi
die()  { echo "${R}dctl: $*${N}" >&2; exit 1; }
info() { echo "${Y}» $*${N}" >&2; }

# Remote prelude (POSIX sh): pick docker / sudo docker, and compose v2 / v1.
PRELUDE_BATCH='D=docker; docker ps -q >/dev/null 2>&1 || D="sudo -n docker"; if $D compose version >/dev/null 2>&1; then C="$D compose"; elif command -v docker-compose >/dev/null 2>&1; then C="${D%docker}docker-compose"; else C="$D compose"; fi'
PRELUDE_TTY='D=docker; docker ps -q >/dev/null 2>&1 || D="sudo docker"; if $D compose version >/dev/null 2>&1; then C="$D compose"; elif command -v docker-compose >/dev/null 2>&1; then C="${D%docker}docker-compose"; else C="$D compose"; fi'

q() { printf "'%s'" "${1//\'/\'\\\'\'}"; }   # POSIX single-quote

rbatch() { ssh "${SSH_BASE[@]}" -o BatchMode=yes "$R_SERVER" "sh -c $(q "$PRELUDE_BATCH; $1")"; }
rtty()   { ssh "${SSH_BASE[@]}" -t "$R_SERVER" "sh -c $(q "$PRELUDE_TTY; $1")"; }

servers() {
  if [ -n "$DCTL_SERVERS" ]; then tr ', ' '\n\n' <<<"$DCTL_SERVERS" | grep .
  elif [ -s "$SERVERS_FILE" ]; then grep -vE '^[[:space:]]*(#|$)' "$SERVERS_FILE" | awk '{print $1}'
  else awk 'tolower($1)=="host"{for(i=2;i<=NF;i++) if($i !~ /[*?!]/) print $i}' ~/.ssh/config 2>/dev/null
  fi
}

# ---------- inventory ----------
FORMAT='{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}\t{{.Label "com.docker.compose.project.working_dir"}}\t{{.Label "com.docker.compose.project.config_files"}}\t{{.Ports}}'

fetch_one() {   # $1 server -> cache lines on stdout
  local s=$1 err
  err=$( { printf '%s\n' "$PRELUDE_BATCH" "\$D ps -a --format '$FORMAT'"; } |
         ssh "${SSH_BASE[@]}" -o BatchMode=yes "$s" sh -s 2>&1 >"$CACHE_DIR/.out.$s" ) || {
    echo "${R}✗ $s: $(tail -n1 <<<"$err")${N}" >&2; rm -f "$CACHE_DIR/.out.$s"; return 1; }
  awk -v s="$s" 'BEGIN{FS=OFS="\t"} NF>=7 {print s,$0}' "$CACHE_DIR/.out.$s"
  rm -f "$CACHE_DIR/.out.$s"
}

refresh() {
  local list; list=$(servers)
  [ -z "$list" ] && die "no servers. Put ssh host names (one per line) in $SERVERS_FILE"
  local tmpd; tmpd=$(mktemp -d)
  [ "$1" = quiet ] || info "scanning $(wc -l <<<"$list") server(s)..."
  local s; while read -r s; do fetch_one "$s" >"$tmpd/$s.tsv" & done <<<"$list"
  wait
  cat "$tmpd"/*.tsv > "$CACHE.new" 2>/dev/null; mv "$CACHE.new" "$CACHE"; rm -rf "$tmpd"
}

# raw lines -> "pretty<US>raw"
fmt_lines() {
  local raw; raw=$(cat); [ -z "$raw" ] && return
  paste -d "$US" \
    <(awk -F'\t' -v g=$'\e[32m' -v r=$'\e[31m' -v n=$'\e[0m' 'BEGIN{OFS="\t"}{
        st = ($4 ~ /^Up/) ? g "●" n : r "○" n
        print $1, st " " $2, ($5==""?"-":$5), substr($4,1,22), $3 }' <<<"$raw" | column -t -s $'\t') \
    <(cat <<<"$raw")
}

set_r() {   # raw line -> R_* globals
  IFS="$US" read -r R_SERVER R_NAME R_IMAGE R_STATUS R_PROJECT R_SERVICE R_WORKDIR R_FILES R_PORTS <<<"${1//$'\t'/$US}"
  R_ABS=()
  local f; IFS=',' read -ra _fs <<<"$R_FILES"
  for f in "${_fs[@]}"; do [ -z "$f" ] && continue; [[ $f == /* ]] || f="$R_WORKDIR/$f"; R_ABS+=("$f"); done
}

pick() {   # stdin raw lines -> chosen raw line
  fmt_lines | fzf --ansi --delimiter="$US" --with-nth=1 --height=60% --reverse \
      --header="SERVER  NAME  PROJECT  STATUS  IMAGE   (type to filter)" | cut -d"$US" -f2
}

match() {
  local s=$1 n=$2 m
  m=$(awk -F'\t' -v s="$s" -v n="$n" '(s=="" || $1==s) && $2==n' "$CACHE")
  [ -n "$m" ] && { echo "$m"; return; }
  awk -F'\t' -v s="$s" -v n="${n,,}" '(s=="" || $1==s) && index(tolower($2),n)' "$CACHE"
}

resolve() {
  local qy=$1 srv="" nm=$1 m
  [ -z "$qy" ] && die "container name required"
  [[ $qy == *:* ]] && { srv=${qy%%:*}; nm=${qy#*:}; }
  [ -s "$CACHE" ] || refresh
  m=$(match "$srv" "$nm"); [ -z "$m" ] && { refresh; m=$(match "$srv" "$nm"); }
  [ -z "$m" ] && die "no container matching '$qy'"
  if [ "$(wc -l <<<"$m")" -gt 1 ]; then m=$(pick <<<"$m"); [ -z "$m" ] && exit 1; fi
  set_r "$m"
}

need_compose() { [ ${#R_ABS[@]} -gt 0 ] || die "$R_NAME on $R_SERVER is not a docker-compose container (no compose labels)"; }
fflags() { local f o=""; for f in "$@"; do o+=" -f $(q "$f")"; done; echo "$o"; }

# ---------- commands ----------
cmd_ls() {
  refresh
  local data; data=$(if [ -n "$1" ]; then grep -i -- "$1" "$CACHE"; else cat "$CACHE"; fi)
  [ -z "$data" ] && { echo "nothing found"; return; }
  { printf 'SERVER\tNAME\tSTATUS\tPROJECT\tCOMPOSE FILE\tIMAGE\n'
    while IFS= read -r l; do set_r "$l"
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$R_SERVER" "$R_NAME" "${R_STATUS:0:22}" "${R_PROJECT:--}" "${R_ABS[0]:--}" "$R_IMAGE"
    done <<<"$data"; } | column -t -s $'\t' |
    sed -E "1s/.*/${B}&${N}/; s/(Up [^ ]+( [a-z]+)?)/${G}\1${N}/; s/(Exited \([0-9]+\))/${R}\1${N}/"
}

cmd_where() {
  echo "${B}container${N}  $R_NAME   ${D0}($R_STATUS)${N}"
  echo "${B}server${N}     $R_SERVER"
  echo "${B}image${N}      $R_IMAGE"
  [ -n "$R_PORTS" ] && echo "${B}ports${N}      $R_PORTS"
  if [ ${#R_ABS[@]} -gt 0 ]; then
    echo "${B}project${N}    $R_PROJECT  (service: $R_SERVICE)"
    echo "${B}dir${N}        $R_WORKDIR"
    local f; for f in "${R_ABS[@]}"; do echo "${B}compose${N}    $R_SERVER:$f"; done
  else echo "${B}compose${N}    - (started with plain docker run)"; fi
}

cmd_edit() {
  need_compose
  local f=${R_ABS[0]}
  [ ${#R_ABS[@]} -gt 1 ] && { f=$(printf '%s\n' "${R_ABS[@]}" | fzf --height=10 --header="which compose file?") || return 1; }
  local tmpd; tmpd=$(mktemp -d); trap 'rm -rf "$tmpd"' RETURN
  local loc="$tmpd/$(basename "$f")" orig="$tmpd/.orig"
  rbatch "f=$(q "$f"); S=; [ -r \"\$f\" ] || S='sudo -n'; \$S cat \"\$f\"" >"$orig" || die "cannot read $f on $R_SERVER"
  cp "$orig" "$loc"
  local ed=${VISUAL:-${EDITOR:-$(command -v vim || command -v nano || echo vi)}}

  while :; do
    $ed "$loc"
    cmp -s "$orig" "$loc" && { info "no changes"; return 0; }
    echo; diff -u $(diff --color=always /dev/null /dev/null >/dev/null 2>&1 && echo --color=always) --label "$R_SERVER:$f (current)" --label "edited" "$orig" "$loc"; echo

    # upload as <file>.dctl-new and validate it with compose on the server
    rbatch "f=$(q "$f"); S=; [ -w \"\$(dirname \"\$f\")\" ] || S='sudo -n'; \$S tee \"\$f.dctl-new\" >/dev/null" <"$loc" \
      || die "upload failed (no write permission and no passwordless sudo?)"
    local newset=() x; for x in "${R_ABS[@]}"; do [ "$x" = "$f" ] && newset+=("$f.dctl-new") || newset+=("$x"); done
    local verr
    if verr=$(rbatch "cd $(q "$R_WORKDIR") && \$C -p $(q "$R_PROJECT")$(fflags "${newset[@]}") config -q" 2>&1); then
      echo "${G}✓ compose file is valid${N}"
      read -rp "Save to $R_SERVER:$f ? (backup is kept) [y/N/e=edit again] " a
    else
      echo "${R}✗ validation failed:${N}"; echo "$verr"
      read -rp "[e]dit again / save [a]nyway / [N]o: " a; [ "$a" = a ] && a=y
    fi
    case $a in
      e|E) continue ;;
      y|Y) break ;;
      *) rbatch "rm -f $(q "$f.dctl-new") 2>/dev/null || sudo -n rm -f $(q "$f.dctl-new")"; info "aborted, nothing changed"; return 1 ;;
    esac
  done

  local bak="$f.bak.$(date +%Y%m%d-%H%M%S)"
  rbatch "f=$(q "$f"); S=; [ -w \"\$f\" ] && [ -w \"\$(dirname \"\$f\")\" ] || S='sudo -n'; \$S cp -p \"\$f\" $(q "$bak") && \$S cp \"\$f.dctl-new\" \"\$f\" && \$S rm -f \"\$f.dctl-new\"" \
    || die "save failed"
  echo "${G}✓ saved${N}  ${D0}backup: $R_SERVER:$bak${N}"
  read -rp "Apply now (docker compose up -d)? [y/N] " a
  [[ $a == [yY] ]] && cmd_up
}

cmd_up()      { need_compose; rtty "cd $(q "$R_WORKDIR") && \$C -p $(q "$R_PROJECT")$(fflags "${R_ABS[@]}") up -d"; }
cmd_ps()      { need_compose; rtty "cd $(q "$R_WORKDIR") && \$C -p $(q "$R_PROJECT")$(fflags "${R_ABS[@]}") ps"; }
cmd_logs()    { rtty "\$D logs --tail ${DCTL_TAIL:-200} -f $(q "$R_NAME")"; }
cmd_sh()      { rtty "\$D exec -it $(q "$R_NAME") sh -c 'command -v bash >/dev/null && exec bash || exec sh'"; }
cmd_inspect() { rbatch "\$D inspect $(q "$R_NAME")" | { command -v jq >/dev/null && jq -C . || cat; } | less -R; }
cmd_cd()      { local d=${R_WORKDIR:-~}; ssh "${SSH_BASE[@]}" -t "$R_SERVER" "cd $(q "$d") 2>/dev/null; exec \${SHELL:-sh} -l"; }
cmd_simple()  { rtty "\$D $1 $(q "$R_NAME")"; }

cmd_preview() {   # used by fzf preview; $1 = raw line
  set_r "$1"; B= N= D0= cmd_where
}

ui() {
  command -v fzf >/dev/null || die "fzf not found locally (only needed for the interactive mode)"
  refresh
  while :; do
    local sel
    sel=$(fmt_lines <"$CACHE" | fzf --ansi --delimiter="$US" --with-nth=1 --reverse \
          --header="enter=actions  ctrl-r=rescan  esc=quit" \
          --preview="bash $(q "$SELF") _preview {2}" --preview-window=down,9,wrap \
          --bind="ctrl-r:reload(bash $(q "$SELF") _reload)") || return 0
    set_r "${sel#*$US}"
    local act
    act=$(printf '%s\n' \
      "edit      edit compose file" \
      "up        compose up -d (apply changes)" \
      "logs      tail + follow logs" \
      "sh        shell inside container" \
      "cd        ssh to server in compose dir" \
      "where     show paths / info" \
      "inspect   docker inspect" \
      "ps        compose ps" \
      "restart   docker restart" \
      "stop      docker stop" \
      "start     docker start" \
      "back" | fzf --reverse --height=16 --header="$R_SERVER : $R_NAME" | awk '{print $1}')
    case $act in
      ""|back) continue ;;
      restart|stop|start) cmd_simple "$act" ;;
      where) cmd_where ;;
      *) "cmd_$act" ;;
    esac
    [[ $act == restart || $act == stop || $act == start || $act == up ]] && refresh quiet
    read -rp "${D0}-- enter to go back --${N}" _
  done
}

usage() { sed -n '2,22p' "$SELF" | sed 's/^# \{0,1\}//'; }

cmd=${1:-ui}; shift 2>/dev/null
case $cmd in
  ui) ui ;;
  web|gui) if [ -n "${DCTL_PY:-}" ]; then exec "$DCTL_PY" -m dctl.ui
           elif command -v dctl-ui >/dev/null 2>&1; then exec dctl-ui
           else exec python3 -m dctl.ui; fi ;;
  ls|list) cmd_ls "$1" ;;
  servers) servers ;;
  refresh) refresh ;;
  _reload) refresh quiet 2>/dev/null; fmt_lines <"$CACHE" ;;
  _preview) cmd_preview "$1" ;;
  where|edit|up|ps|logs|sh|cd|inspect) resolve "$1"; "cmd_$cmd" ;;
  restart|stop|start) resolve "$1"; cmd_simple "$cmd" ;;
  -h|--help|help) usage ;;
  *) usage; exit 1 ;;
esac
