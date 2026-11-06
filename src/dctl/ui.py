"""dctl-ui - local web UI to manage Docker and PM2 on many servers over SSH.

Runs on YOUR machine only. Servers need nothing new (just ssh + docker).
Handles servers where docker / compose files need sudo (with or without password).
sudo passwords are kept only in this process's memory, never written to disk.

    dctl-ui              -> opens dctl in its own window
    dctl-ui --browser    -> use your web browser instead
    dctl-ui --server     -> no window; open http://127.0.0.1:8765 yourself (or set DCTL_NO_BROWSER=1)
    DCTL_PORT=9000 dctl-ui
Servers: ~/.config/dctl/servers (one ssh host per line), else all Host entries of ~/.ssh/config.
"""
import base64, time, difflib, hashlib, json, os, re, secrets, select, shlex, signal, struct, subprocess, threading, webbrowser
IS_WIN = os.name == "nt"
if not IS_WIN:                     # web terminal needs a Unix pseudo-terminal
    import fcntl, pty, termios
import urllib.parse
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get("DCTL_PORT", 8765))
HOME = os.path.expanduser("~")
CONF = os.environ.get("DCTL_CONF", f"{HOME}/.config/dctl")
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", f"{HOME}/.cache"), "dctl")
os.makedirs(CACHE_DIR, exist_ok=True)
SSH = ["ssh", "-o", "ConnectTimeout=6", "-o", "ServerAliveInterval=15", "-o", "BatchMode=yes"]
if not IS_WIN:                     # Windows OpenSSH has no connection sharing
    SSH += ["-o", "ControlMaster=auto", "-o", f"ControlPath={CACHE_DIR}/cm-%C", "-o", "ControlPersist=300"]
PRELUDE = ('D=docker; if $D compose version >/dev/null 2>&1; then C="$D compose"; '
           'elif command -v docker-compose >/dev/null 2>&1; then C=docker-compose; else C="$D compose"; fi; ')
FORMAT = (r'{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Label "com.docker.compose.project"}}\t'
          r'{{.Label "com.docker.compose.service"}}\t{{.Label "com.docker.compose.project.working_dir"}}\t'
          r'{{.Label "com.docker.compose.project.config_files"}}\t{{.Ports}}')
TOKEN = secrets.token_urlsafe(24)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

SUDO_PW = {}      # server -> password (memory only)
INFO = {}         # server -> {"docker": bool, "sudo": nopass|pass|none, "state": str, "error": str}
CONTAINERS = {}   # (server, name) -> dict
LOCK = threading.Lock()


class NeedSudo(Exception): pass
class RemoteError(Exception): pass

class NoDocker(Exception):
    pass



def q(s): return shlex.quote(str(s))


SERVERS_JSON = os.path.join(CONF, "servers.json")   # servers added from the UI


def added_servers():
    try:
        return json.load(open(SERVERS_JSON))
    except (OSError, ValueError):
        return []


def save_added(lst):
    os.makedirs(CONF, exist_ok=True)
    tmp = SERVERS_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lst, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SERVERS_JSON)


PROTECT_JSON = os.path.join(CONF, "protected.json")   # servers that need the name typed before any change


def protected():
    try:
        return set(json.load(open(PROTECT_JSON)))
    except (OSError, ValueError, TypeError):
        return set()


def save_protected(names):
    os.makedirs(CONF, exist_ok=True)
    tmp = PROTECT_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(names), f, indent=2)
    os.replace(tmp, PROTECT_JSON)


class Protected(Exception):
    def __init__(self, server, what):
        super().__init__(f"{server} is a protected server")
        self.server, self.what = server, what


def guard(d, server, what):
    """Protected servers: the UI must send the server name typed by the user (confirm_server) for any change."""
    if server in protected() and server not in (d.get("confirm_server") or "").split(","):
        raise Protected(server, what)


def servers():
    env = os.environ.get("DCTL_SERVERS")
    if env:
        return [s for s in re.split(r"[,\s]+", env) if s]
    out = []
    f = os.path.join(CONF, "servers")
    if os.path.exists(f) and os.path.getsize(f):
        out = [l.split()[0] for l in open(f) if l.strip() and not l.strip().startswith("#")]
    else:
        try:
            for l in open(f"{HOME}/.ssh/config"):
                p = l.split()
                if p and p[0].lower() == "host":
                    out += [h for h in p[1:] if not re.search(r"[*?!]", h) and h not in out]
        except OSError:
            pass
    for a in added_servers():
        if a["name"] not in out:
            out.append(a["name"])
    return out


JUMP_RE = re.compile(r"(?:([A-Za-z0-9._@\\-]+)@)?([A-Za-z0-9._-]+)(?::(\d{1,5}))?")


def _jump_opts(jump, depth=0):
    """ssh options to reach a server through a jump host (bastion). `jump` is a server added in dctl,
    a Host from ~/.ssh/config, or [user@]host[:port]. Always non-interactive (BatchMode), so a password
    typed for the target is never offered to the jump host."""
    if not jump:
        return []
    if depth > 3:
        raise RemoteError("jump host chain is too long (max 3)")
    a = next((x for x in added_servers() if x["name"] == jump), None)
    hop = target_for(a, depth + 1) if a else None
    if hop is None:
        m = JUMP_RE.fullmatch(jump)
        if not m:
            raise RemoteError(f"bad jump host: {jump}")
        hop = (["-l", m[1]] if m[1] else []) + (["-p", m[3]] if m[3] else []) + [m[2]]
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-W", "%h:%p"] + [x.replace("%", "%%") for x in hop]
    return ["-o", "ProxyCommand=" + (subprocess.list2cmdline(argv) if IS_WIN else shlex.join(argv))]


def target_for(a, depth=0):
    t = _jump_opts(a.get("jump"), depth) + ["-p", str(a.get("port") or 22), "-o", "StrictHostKeyChecking=accept-new"]
    if a.get("key"):
        t += ["-i", os.path.expanduser(a["key"]), "-o", "IdentitiesOnly=yes"]
    if a.get("user"):
        t += ["-l", a["user"]]
    return t + [a["host"]]


def target(server):
    for a in added_servers():
        if a["name"] == server:
            return target_for(a)
    return [server]


def host_of(server):
    for a in added_servers():
        if a["name"] == server:
            return (f'{a.get("user") or ""}@{a["host"]}' + ("" if int(a.get("port") or 22) == 22 else f':{a["port"]}')
                    + (f' via {a["jump"]}' if a.get("jump") else ""))
    try:
        kv = {}
        for l in subprocess.run(["ssh", "-G", server], capture_output=True, text=True, timeout=5).stdout.splitlines():
            k, _, v = l.partition(" ")
            kv.setdefault(k, v)
        port = kv.get("port", "22")
        return f'{kv.get("user", "")}@{kv.get("hostname", server)}' + ("" if port == "22" else f":{port}")
    except Exception:
        return server


def ssh(server, cmd, stdin=None, timeout=60):
    try:
        p = subprocess.run(SSH + target(server) + [cmd], input=stdin, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RemoteError(f"{server}: timed out after {timeout}s")
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")


def _noshell(server, rc, err):
    if rc != 0 and re.search(r"not allowed to execute '\S*/(ba|da)?sh ", err):
        INFO.setdefault(server, {})["noshell"] = True
        raise RemoteError(f"{server}: sudo here does not allow running a shell (sudoers has !/bin/sh). "
                          "Put sudo inside the command instead, e.g.  sudo docker ps  /  sudo systemctl restart nginx")


def run(server, script, elevate=False, stdin=None, timeout=60):
    """Run a POSIX sh script on server; with elevate=True run it as root via sudo."""
    body = "sh -c " + q(PRELUDE + script)
    if not elevate:
        return ssh(server, body, stdin, timeout)
    mode = INFO.get(server, {}).get("sudo")
    if mode == "nopass":
        rc, out, err = ssh(server, "sudo -n " + body, stdin, timeout)
        _noshell(server, rc, err)
        return rc, out, err
    if mode == "pass":
        pw = SUDO_PW.get(server)
        if pw is None:
            raise NeedSudo(server)
        assert stdin is None, "stdin not supported with password sudo"
        rc, out, err = ssh(server, "sudo -k -S -p '' " + body, (pw + "\n").encode(), timeout)
        if rc != 0 and re.search(r"incorrect password|Sorry, try again|no password was provided", err):
            SUDO_PW.pop(server, None)
            raise NeedSudo(server)
        _noshell(server, rc, err)
        return rc, out, err
    raise RemoteError(f"{server}: permission denied, and your user has no sudo rights here. "
                      "Ask the admin to add you to the 'docker' group or give sudo access.")


def _sudo_pre(server, lazy=False):
    """Shell preamble that sets $S (a sudo prefix, or empty) plus the stdin bytes it needs (the password line)."""
    if INFO.get(server, {}).get("sudo") is None:
        probe(server)
    mode = INFO.get(server, {}).get("sudo")
    pw = SUDO_PW.get(server)
    if mode == "pass" and pw is None:
        if not lazy:
            raise NeedSudo(server)
        mode = None          # sudo not needed for this call; run without it
    if mode == "nopass":
        return 'S="sudo -n"; ', None
    if mode == "pass":
        return ('IFS= read -r P; printf "%s\\n" "$P" | sudo -S -p "" -v 2>/dev/null; unset P; '
                'if sudo -n true 2>/dev/null; then S="sudo -n"; else echo @@SUDOFAIL; S=; fi; '), (pw + "\n").encode()
    return 'S=; ', None


def srun(server, script, timeout=90, lazy=False, data=b""):
    """Run script as the normal user; inside it, $S is a sudo prefix for single commands and $DK is docker
    (with sudo if needed). Works on servers whose sudoers forbid shells (!/bin/sh) - only plain commands get sudo."""
    pre, stdin = _sudo_pre(server, lazy)
    pre += 'if docker info </dev/null >/dev/null 2>&1; then DK=docker; else DK="$S docker"; fi; '
    if data:
        stdin = (stdin or b"") + data
    rc, out, err = ssh(server, "sh -c " + q(PRELUDE + pre + script), stdin, timeout)
    if b"@@SUDOFAIL" in out:
        SUDO_PW.pop(server, None)
        raise NeedSudo(server)
    return rc, out, err


def need_docker_root(server):
    if server not in INFO or "docker" not in INFO[server]:
        probe(server)
    return not INFO[server]["docker"]


DK_PRE = ('D="$DK"; if $D compose version >/dev/null 2>&1; then C="$D compose"; '
          'elif command -v docker-compose >/dev/null 2>&1; then C="$S docker-compose"; else C="$D compose"; fi; ')


def drun(server, script, timeout=60):
    """Run a docker-related script, elevating only if this user can't use docker directly.
    On servers whose sudoers forbid shells, falls back to putting sudo on each docker command."""
    if not need_docker_root(server):
        return run(server, script, timeout=timeout)
    if not INFO.get(server, {}).get("noshell"):
        try:
            return run(server, script, elevate=True, timeout=timeout)
        except RemoteError:
            if not INFO.get(server, {}).get("noshell"):
                raise
    return srun(server, DK_PRE + script, timeout=timeout)


def dk(server, script, timeout=120):
    """srun for docker commands: $DK is docker (with sudo only when this user needs it)."""
    return srun(server, script, timeout=timeout, lazy=not need_docker_root(server))


def text(b): return ANSI.sub("", b.decode("utf-8", "replace"))


PROBE = r'''X=; if docker ps -q >/dev/null 2>&1; then echo DOCKER=1; X=docker; else echo DOCKER=0; sudo -n docker ps -q >/dev/null 2>&1 && X="sudo -n docker"; fi
e=$(sudo -n true 2>&1) && echo SUDO=nopass || { case "$e" in *assword*) echo SUDO=pass;; *) echo SUDO=none;; esac; }
if [ -n "$X" ]; then echo RUN=$($X ps -q | wc -l | tr -d ' ') ALL=$($X ps -aq | wc -l | tr -d ' ') VER=$($X version --format '{{.Server.Version}}' 2>/dev/null); fi
echo LOAD=$(cut -d' ' -f1 /proc/loadavg) NPROC=$(nproc 2>/dev/null || echo 1) UPTIME=$(cut -d. -f1 /proc/uptime)
echo MEM=$(awk '/^MemTotal/{t=$2}/^MemAvailable/{a=$2}END{if(t)printf "%d",(t-a)*100/t}' /proc/meminfo)
echo DISK=$(df -P / | awk 'NR==2{gsub("%","",$5);print $5}')
[ -r /etc/os-release ] && . /etc/os-release && echo OS=${ID}-${VERSION_ID}
command -v docker >/dev/null 2>&1 && echo HASD=1 || echo HASD=0
echo PM2=$(ps -eo args= 2>/dev/null | grep -c '^PM2 v.*God Daemon')
true'''


def _num(v, f=int):
    try:
        return f(v)
    except (TypeError, ValueError):
        return None


def probe(server):
    rc, out, err = ssh(server, "sh -c " + q(PROBE), timeout=20)
    if rc != 0:
        INFO[server] = {"state": "unreachable", "error": (err.strip().splitlines() or ["ssh failed"])[-1]}
        return False
    kv = dict(l.split("=", 1) for l in out.decode("utf-8", "replace").split() if "=" in l)
    dk, su = kv.get("DOCKER") == "1", kv.get("SUDO", "none")
    hasd = kv.get("HASD", "1") == "1"
    st = ("ok" if dk or not hasd else "sudo" if su == "nopass" or (su == "pass" and server in SUDO_PW)
          else "need_password" if su == "pass" else "no_access")
    m = {"load": _num(kv.get("LOAD"), float), "nproc": _num(kv.get("NPROC")), "uptime": _num(kv.get("UPTIME")),
         "mem": _num(kv.get("MEM")), "disk": _num(kv.get("DISK")), "run": _num(kv.get("RUN")),
         "all": _num(kv.get("ALL")), "ver": kv.get("VER") or None, "os": kv.get("OS") or None,
         "pm2": _num(kv.get("PM2")) or 0, "hasdocker": hasd}
    INFO[server] = {"docker": dk, "hasdocker": hasd, "sudo": su, "state": st, "error": "", "m": m}
    return True


def scan_one(server):
    found = []
    try:
        if probe(server):
            i = INFO[server]
            if not i.get("hasdocker", True):
                raise NoDocker()
            rc, out, err = drun(server, f"$D ps -a --format {q(FORMAT)}")
            if rc != 0:
                raise RemoteError(err.strip().splitlines()[-1] if err.strip() else "docker ps failed")
            for line in out.decode("utf-8", "replace").splitlines():
                f = line.split("\t")
                if len(f) < 8:
                    continue
                name, image, status, proj, svc, wd, cfg, ports = f[:8]
                files = [(x if x.startswith("/") else f"{wd}/{x}") for x in cfg.split(",") if x]
                found.append({"server": server, "name": name, "image": image, "status": status,
                              "running": status.startswith("Up"), "project": proj, "service": svc,
                              "workdir": wd, "files": files, "ports": ports})
            i["state"] = "ok" if i["docker"] else "sudo"
    except NoDocker:
        pass
    except NeedSudo:
        INFO[server]["state"] = "need_password"
    except RemoteError as e:
        INFO[server].update(state="no_access" if "sudo" in str(e) else "error", error=str(e))
    with LOCK:
        for k in [k for k in CONTAINERS if k[0] == server]:
            del CONTAINERS[k]
        for c in found:
            CONTAINERS[(server, c["name"])] = c


HOSTS = {}


def server_list():
    names = servers()
    added = {a["name"] for a in added_servers()}
    prot = protected()
    with LOCK:
        counts = {}
        for (s, _), c in CONTAINERS.items():
            counts[s] = counts.get(s, 0) + 1
    out = []
    for s in names:
        if s not in HOSTS:
            HOSTS[s] = host_of(s)
        out.append({"name": s, "host": HOSTS[s], "added": s in added, "count": counts.get(s),
                    "unlocked": s in SUDO_PW, "protected": s in prot, **INFO.get(s, {"state": "unknown"})})
    return out


def snapshot():
    order = servers()
    sv = server_list()
    with LOCK:
        cs = sorted(CONTAINERS.values(), key=lambda c: (order.index(c["server"]) if c["server"] in order else 99,
                                                         c["project"] or "~", c["name"]))
    return {"servers": sv, "containers": cs}


def get_c(d):
    c = CONTAINERS.get((d.get("server"), d.get("name")))
    if not c:
        raise RemoteError("container not found - rescan")
    return c


def compose_prefix(c, files=None, project_dir=False):
    pd = f"--project-directory {q(c['workdir'])} " if project_dir else ""
    return (f"cd {q(c['workdir'])} && $C {pd}-p {q(c['project'])} "
            + " ".join("-f " + q(f) for f in (files or c["files"])))


def check_file(c, path):
    if not c["files"]:
        raise RemoteError("this container was not started by docker compose")
    if path not in c["files"]:
        raise RemoteError("file is not a compose file of this container")


def file_run(server, script):
    """Try as normal user; exit 77 from the script means 'no permission' -> retry with sudo."""
    rc, out, err = run(server, script)
    elevated = False
    if rc == 77:
        rc, out, err = run(server, script, elevate=True)
        elevated = True
    return rc, out, err, elevated


# ---------------- API ----------------
def r_scan(d):
    targets = [d["server"]] if d.get("server") else (d.get("servers") or servers())
    targets = [t for t in targets if t in servers()]
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(scan_one, targets))
    return snapshot()


# ---------------- pages & groups (layout) ----------------
LAYOUT_JSON = os.path.join(CONF, "layout.json")
PAGES = ("docker", "pm2")
LAYOUT_LOCK = threading.Lock()


def load_layout():
    try:
        lay = json.load(open(LAYOUT_JSON))
    except (OSError, ValueError):
        return None
    for pg in PAGES:
        lay.setdefault(pg, {}).setdefault("ungrouped", [])
        lay[pg].setdefault("groups", [])
    dup = [x for x in page_servers(lay, "pm2") if x in page_servers(lay, "docker")]
    if dup:                       # one page per server: it was put on PM2 because PM2 runs there - keep it there
        for x in dup:
            _detach(lay["docker"], x)
        save_layout(lay)
    return lay


def save_layout(lay):
    os.makedirs(CONF, exist_ok=True)
    tmp = LAYOUT_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lay, f, indent=2)
    os.replace(tmp, LAYOUT_JSON)


def page_servers(lay, pg):
    P = lay[pg]
    return P["ungrouped"] + [x for g in P["groups"] for x in g["servers"]]


def ensure_layout():
    """First run: put servers with docker on the Docker page and servers with PM2 on the PM2 page."""
    lay = load_layout()
    if lay is not None:
        return lay
    names = servers()
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(probe, [n for n in names if n not in INFO]))
    lay = {pg: {"ungrouped": [], "groups": []} for pg in PAGES}
    for n in names:
        i = INFO.get(n, {}); m = i.get("m") or {}
        if m.get("pm2") and not m.get("hasdocker", True):
            lay["pm2"]["ungrouped"].append(n)
        else:
            lay["docker"]["ungrouped"].append(n)
    save_layout(lay)
    return lay


def _detach(P, server):
    if server in P["ungrouped"]:
        P["ungrouped"].remove(server)
    for g in P["groups"]:
        if server in g["servers"]:
            g["servers"].remove(server)


def _group(P, name, create=False):
    for g in P["groups"]:
        if g["name"] == name:
            return g
    if not create:
        raise RemoteError(f"group '{name}' not found")
    if not re.fullmatch(r"[^<>\n]{1,40}", name):
        raise RemoteError("group name: 1-40 characters")
    g = {"name": name, "servers": []}
    P["groups"].append(g)
    return g


def place(lay, pg, server, group):
    for other in PAGES:           # a server is on exactly one page
        _detach(lay[other], server)
    P = lay[pg]
    (_group(P, group, create=True)["servers"] if group else P["ungrouped"]).append(server)


def r_layout(d):
    pg, op = d.get("page"), d.get("op")
    if pg not in PAGES:
        raise RemoteError("bad page")
    with LAYOUT_LOCK:
        lay = ensure_layout(); P = lay[pg]
        srv, grp = d.get("server", ""), (d.get("group") or "").strip()
        if op in ("add", "move"):
            if srv not in servers():
                raise RemoteError("unknown server")
            place(lay, pg, srv, grp)
        elif op == "remove":
            _detach(P, srv)
        elif op == "group_add":
            if any(g["name"] == grp for g in P["groups"]):
                raise RemoteError("a group with this name already exists")
            _group(P, grp, create=True)
        elif op == "group_rename":
            to = (d.get("to") or "").strip()
            if any(g["name"] == to for g in P["groups"]):
                raise RemoteError("a group with this name already exists")
            if not re.fullmatch(r"[^<>\n]{1,40}", to):
                raise RemoteError("group name: 1-40 characters")
            _group(P, grp)["name"] = to
        elif op == "group_delete":
            g = _group(P, grp)
            P["ungrouped"] += [x for x in g["servers"] if x not in P["ungrouped"]]
            P["groups"].remove(g)
        elif op == "group_up":
            g = _group(P, grp); i = P["groups"].index(g)
            if i:
                P["groups"][i - 1], P["groups"][i] = g, P["groups"][i - 1]
        else:
            raise RemoteError("bad op")
        save_layout(lay)
    return page_payload(pg, probe_new=True)


def page_payload(pg, probe_new=False):
    lay = ensure_layout()
    known = servers()
    for x in [x for x in page_servers(lay, pg) if x not in known]:   # server deleted from ssh config
        _detach(lay[pg], x)
    names = page_servers(lay, pg)
    if probe_new:
        todo = [n for n in names if n not in INFO]
        with ThreadPoolExecutor(16) as ex:
            list(ex.map(probe, todo))
    for n in known:
        if n not in HOSTS:
            HOSTS[n] = host_of(n)
    sl = {x["name"]: x for x in server_list()}
    return {"layout": lay[pg], "servers": [sl[n] for n in names if n in sl],
            "known": [{"name": n, "host": HOSTS.get(n, ""), "pages": [pg2 for pg2 in PAGES if n in page_servers(lay, pg2)]}
                      for n in known]}


def r_servers(d):
    pg = d.get("page") if d.get("page") in PAGES else "docker"
    names = page_servers(ensure_layout(), pg)
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(probe, names))
    return page_payload(pg)


def _pct(v):
    try:
        return float(v.strip().rstrip("%"))
    except (ValueError, AttributeError):
        return 0.0


def r_stats(d):
    s = d["server"]
    rc, out, err = drun(s, "$D stats --no-stream --format "
                        + q(r"{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}"), timeout=45)
    if rc != 0:
        raise RemoteError(err.strip() or "docker stats failed")
    st = {}
    for line in out.decode("utf-8", "replace").splitlines():
        f = line.split("\t")
        if len(f) >= 5:
            st[f[0]] = {"cpu": _pct(f[1]), "mem": f[2].strip(), "memp": _pct(f[3]), "net": f[4].strip()}
    return {"server": s, "stats": st}


HIST_SCRIPT = r"""for f in "$HOME/.bash_history" "$HOME/.zsh_history"; do [ -r "$f" ] && tail -n 3000 "$f"; done 2>/dev/null; true"""


def r_hist(d):
    """Shell history of the login user, used only to suggest commands in the web terminal (kept in memory)."""
    rc, out, err = run(d["server"], HIST_SCRIPT, timeout=20)
    seen, res = set(), []
    for line in reversed(out.decode("utf-8", "replace").splitlines()):
        if line.startswith(": ") and ";" in line[:24]:          # zsh extended history ": 1700000000:0;cmd"
            line = line.split(";", 1)[1]
        line = line.strip()
        if not line or line.startswith("#") or len(line) > 400 or line in seen:
            continue
        seen.add(line); res.append(line)
        if len(res) >= 3000:
            break
    return {"server": d["server"], "hist": res}          # newest first


def ensure_pubkey(key=None):
    if key:
        kp = os.path.expanduser(key)
        if os.path.exists(kp + ".pub"):
            return open(kp + ".pub").read().strip()
        return subprocess.run(["ssh-keygen", "-y", "-f", kp], capture_output=True, text=True, check=True).stdout.strip()
    for n in ("id_ed25519", "id_rsa", "id_ecdsa"):
        p = f"{HOME}/.ssh/{n}.pub"
        if os.path.exists(p) and os.path.exists(p[:-4]):
            return open(p).read().strip()
    kp = f"{HOME}/.ssh/id_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "dctl", "-f", kp], check=True)
    return open(kp + ".pub").read().strip()


def install_key(tgt, pw, pub):
    """Log in once with the password (via SSH_ASKPASS, no extra tools) and add our public key."""
    if IS_WIN:
        raise RemoteError("adding a server with a password is not supported on Windows yet. "
                          "Copy your SSH public key to the server's ~/.ssh/authorized_keys, then add it without a password.")
    askpass = os.path.join(CACHE_DIR, "askpass.sh")
    if not os.path.exists(askpass):
        with open(askpass, "w") as f:
            f.write('#!/bin/sh\nprintf "%s\\n" "$DCTL_SSHPASS"\n')
        os.chmod(askpass, 0o700)
    env = dict(os.environ, SSH_ASKPASS=askpass, SSH_ASKPASS_REQUIRE="force",
               DISPLAY=os.environ.get("DISPLAY", ":0"), DCTL_SSHPASS=pw)
    cmd = ('umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && '
           f'{{ grep -qxF {q(pub)} ~/.ssh/authorized_keys || printf "%s\\n" {q(pub)} >> ~/.ssh/authorized_keys; }}')
    opts = ["-o", "ConnectTimeout=8", "-o", "ControlMaster=no", "-o", "ControlPath=none",
            "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive",
            "-o", "NumberOfPasswordPrompts=1"]
    try:
        p = subprocess.run(["ssh"] + opts + tgt + ["sh -c " + q(cmd)], env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=40, start_new_session=True)
    except subprocess.TimeoutExpired:
        raise RemoteError("password login timed out")
    if p.returncode != 0:
        last = (p.stderr.strip().splitlines() or ["login failed"])[-1]
        raise RemoteError("password login failed: " + ("wrong user or password" if "denied" in last else last))


def _server_entry(d, old=None):
    """Validate the Add/Edit server form. `old` is the current name when editing (it may keep that name)."""
    name, host = d.get("name", "").strip(), d.get("host", "").strip()
    user, key = d.get("user", "").strip(), d.get("key", "").strip()
    try:
        port = int(d.get("port") or 22)
    except ValueError:
        raise RemoteError("port must be a number")
    if not 0 < port < 65536:
        raise RemoteError("port must be 1-65535")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", name):
        raise RemoteError("Name: use letters, numbers, . _ - (no spaces)")
    if name != old and name in servers():
        raise RemoteError(f"a server named '{name}' already exists")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", host):
        raise RemoteError("Host: enter an IP or hostname")
    if user and not re.fullmatch(r"[A-Za-z0-9._@\\-]+", user):
        raise RemoteError("invalid user name")
    if key and not os.path.isfile(os.path.expanduser(key)):
        raise RemoteError(f"key file not found on this computer: {key}")
    jump = d.get("jump", "").strip()
    if jump and not (any(a["name"] == jump for a in added_servers()) or JUMP_RE.fullmatch(jump)):
        raise RemoteError("Jump host: use a server name from dctl or ~/.ssh/config, or user@host:port")
    if jump in (name, old):
        raise RemoteError("a server can't be its own jump host")
    if old and jump:                       # no loops: the jump host must not go through this server
        seen, j = set(), jump
        while j and j not in seen:
            seen.add(j)
            if j == old:
                raise RemoteError(f"{jump} is itself reached through {old} - pick another jump host")
            j = next((a.get("jump") for a in added_servers() if a["name"] == j), None)
    entry = {"name": name, "host": host, "user": user, "port": port, "key": key}
    if jump:
        entry["jump"] = jump
    return entry


def _test_connect(entry, pw):
    """Install our key with the password (if given), then check a password-less login works."""
    tgt = target_for(entry)
    jump = entry.get("jump")
    if pw:
        install_key(tgt, pw, ensure_pubkey(entry.get("key") or None))
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ControlMaster=no",
                        "-o", "ControlPath=none"] + tgt + ["true"], capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        lines = p.stderr.strip().splitlines() or ["ssh failed"]
        last = lines[-1]
        if jump and len(lines) > 1 and re.search(r"UNKNOWN port|kex_exchange_identification|closed by remote host", last):
            last = f"jump host {jump}: {lines[-2]}"          # the error came from the jump connection
        hint = "" if pw else " — if this server uses a password, choose 'Password' so dctl can install your SSH key"
        if jump:
            hint += f" (check that `ssh {jump}` works without a password)"
        raise RemoteError(f"could not connect: {last}{hint}")


def r_server_add(d):
    entry = _server_entry(d)
    name = entry["name"]
    _test_connect(entry, d.get("password", ""))
    lst = added_servers(); lst.append(entry); save_added(lst)
    probe(name)
    if d.get("page") in PAGES:
        with LAYOUT_LOCK:
            lay = ensure_layout(); place(lay, d["page"], name, (d.get("group") or "").strip()); save_layout(lay)
    return {"server": name}


def r_server_get(d):
    a = next((x for x in added_servers() if x["name"] == d.get("name")), None)
    if not a:
        raise RemoteError("only servers added from dctl can be edited here - for others edit ~/.ssh/config")
    return {k: a.get(k, "") for k in ("name", "host", "user", "port", "key", "jump")}


def r_server_edit(d):
    old = d.get("old")
    lst = added_servers()
    i = next((n for n, a in enumerate(lst) if a["name"] == old), None)
    if i is None:
        raise RemoteError("only servers added from dctl can be edited here - for others edit ~/.ssh/config")
    guard(d, old, "edit the connection details")
    entry = _server_entry(d, old)
    name, prev = entry["name"], lst[i]
    conn = lambda a: tuple(a.get(k) or "" for k in ("host", "user", "port", "key", "jump"))
    changed = conn(prev) != conn(entry)
    if changed or d.get("password"):
        _test_connect(entry, d.get("password", ""))
    if changed and not IS_WIN:             # drop the shared connection that still uses the old details
        try:
            subprocess.run(SSH + ["-O", "exit"] + target_for(prev), capture_output=True, timeout=5)
        except Exception:
            pass
    lst = added_servers()
    for n, a in enumerate(lst):
        if a["name"] == old:
            lst[n] = entry
        elif a.get("jump") == old and name != old:
            a["jump"] = name               # servers reached through this one follow the rename
    save_added(lst)
    if name != old:
        with LAYOUT_LOCK:
            lay = load_layout()
            if lay:
                for pg in PAGES:
                    P = lay[pg]
                    for arr in [P["ungrouped"]] + [g["servers"] for g in P["groups"]]:
                        arr[:] = [name if x == old else x for x in arr]
                save_layout(lay)
        p = protected()
        if old in p:
            p.discard(old); p.add(name); save_protected(p)
        if old in SUDO_PW and not changed:
            SUDO_PW[name] = SUDO_PW[old]
    if changed or name != old:
        for m in (INFO, SUDO_PW, HOSTS):
            m.pop(old, None)
        HOSTS.pop(name, None)
        with LOCK:
            for k in [k for k in CONTAINERS if k[0] == old]:
                del CONTAINERS[k]
        probe(name)
    return {"server": name, "renamed": name != old}


def r_server_remove(d):
    name = d.get("name")
    lst = added_servers()
    if not any(a["name"] == name for a in lst):
        raise RemoteError("only servers added from this UI can be removed here (others come from ~/.ssh/config)")
    users = [a["name"] for a in lst if a.get("jump") == name]
    if users:
        raise RemoteError(f"{name} is the jump host for {', '.join(users)} - forget those first")
    save_added([a for a in lst if a["name"] != name])
    with LAYOUT_LOCK:
        lay = load_layout()
        if lay:
            for pg in PAGES:
                _detach(lay[pg], name)
            save_layout(lay)
    p = protected()
    if name in p:
        p.discard(name); save_protected(p)
    INFO.pop(name, None); SUDO_PW.pop(name, None); HOSTS.pop(name, None)
    with LOCK:
        for k in [k for k in CONTAINERS if k[0] == name]:
            del CONTAINERS[k]
    return {}


def r_sudo(d):
    s, pw = d["server"], d.get("password", "")
    rc, _, err = ssh(s, "sudo -k -S -p '' true", (pw + "\n").encode(), timeout=20)
    if rc != 0:
        if "tty" in err:
            raise RemoteError("this server's sudo requires a TTY (requiretty); ask admin to disable it for your user")
        raise RemoteError("wrong password" if re.search(r"incorrect|Sorry", err) else (err.strip() or "sudo failed"))
    SUDO_PW[s] = pw
    INFO.setdefault(s, {})["sudo"] = "pass"
    return {}


def r_file(d):
    c = get_c(d); check_file(c, d["path"])
    rc, out, err, elev = file_run(c["server"], f'f={q(d["path"])}; [ -r "$f" ] || exit 77; cat "$f"')
    if rc != 0:
        raise RemoteError(err.strip() or "cannot read file")
    return {"content": out.decode("utf-8", "replace"), "sha": hashlib.sha256(out).hexdigest(), "elevated": elev}


def r_validate(d):
    c = get_c(d); path = d["path"]; check_file(c, path)
    content = d["content"]
    diff = "".join(difflib.unified_diff(d.get("orig", "").splitlines(True), content.splitlines(True),
                                        f"{c['server']}:{path} (current)", "edited"))
    if not diff:
        return {"diff": "", "valid": True, "output": "", "tmp": None}
    rc, out, err = ssh(c["server"], 'sh -c ' + q('t=$(mktemp /tmp/dctl.XXXXXXXX) && cat > "$t" && echo "$t"'),
                       content.encode())
    if rc != 0:
        raise RemoteError("upload to /tmp failed: " + err)
    tmp = out.decode().strip()
    files = [tmp if f == path else f for f in c["files"]]
    rc, out, err = drun(c["server"], compose_prefix(c, files, project_dir=True) + " config -q 2>&1")
    return {"diff": diff, "valid": rc == 0, "output": text(out) + err, "tmp": tmp}


def r_save(d):
    c = get_c(d); path = d["path"]; check_file(c, path)
    tmp = d["tmp"]
    if not re.fullmatch(r"/tmp/dctl\.[A-Za-z0-9]+", tmp or ""):
        raise RemoteError("bad temp file")
    guard(d, c["server"], f"save {path}")
    script = (f'f={q(path)}; t={q(tmp)}; '
              'if [ "$(id -u)" != 0 ] && { [ ! -w "$f" ] || [ ! -r "$f" ] || [ ! -w "$(dirname "$f")" ]; }; then exit 77; fi; '
              f'[ "$(sha256sum "$f" | cut -d" " -f1)" = {q(d["sha"])} ] || exit 75; '
              'b="$f.bak.$(date +%Y%m%d-%H%M%S)"; cp -p "$f" "$b" && cat "$t" > "$f" && rm -f "$t" && echo "$b" && sha256sum "$f" | cut -d" " -f1')
    rc, out, err, elev = file_run(c["server"], script)
    if rc == 75:
        raise RemoteError("the file was changed on the server by someone else after you opened it. Reload and redo your edit.")
    if rc != 0:
        raise RemoteError(err.strip() or f"save failed (exit {rc})")
    backup, sha = (out.decode().split() + ["", ""])[:2]
    return {"backup": backup, "sha": sha, "elevated": elev}


def r_discard(d):
    tmp = d.get("tmp") or ""
    if re.fullmatch(r"/tmp/dctl\.[A-Za-z0-9]+", tmp):
        ssh(d["server"], "rm -f " + q(tmp), timeout=15)
    return {}


def out_of(rc, out, err):
    return {"rc": rc, "output": (text(out) + ANSI.sub("", err)).strip()}


def r_action(d):
    c = get_c(d); a = d["action"]
    if a not in ("restart", "stop", "start"):
        raise RemoteError("bad action")
    guard(d, c["server"], f"{a} container {c['name']}")
    return out_of(*drun(c["server"], f"$D {a} {q(c['name'])}", timeout=180))


def r_remove(d):
    """docker rm a container. Must type the exact name; running containers need force=True."""
    c = get_c(d)
    if d.get("confirm") != c["name"]:
        raise RemoteError("confirmation name does not match")
    if c["running"] and not d.get("force"):
        raise RemoteError("container is running - stop it first or tick 'force'")
    guard(d, c["server"], f"remove container {c['name']}")
    flags = (" -f" if d.get("force") else "") + (" -v" if d.get("volumes") else "")
    rc, out, err = dk(c["server"], f"$DK rm{flags} {q(c['name'])} 2>&1")
    if rc == 0:
        CONTAINERS.pop((c["server"], c["name"]), None)
    return out_of(rc, out, err)


def r_bulk(d):
    """restart/stop/start/remove many containers; one ssh call per server, servers in parallel."""
    a = d.get("action")
    if a not in ("restart", "stop", "start", "remove"):
        raise RemoteError("bad action")
    items = [get_c(x) for x in (d.get("items") or [])[:300]]
    if not items:
        raise RemoteError("nothing selected")
    for sv in dict.fromkeys(c["server"] for c in items):
        guard(d, sv, f"{a} {sum(c['server'] == sv for c in items)} container(s)")
    if a == "remove":
        if d.get("confirm") != "delete":
            raise RemoteError("type delete to confirm")
        if any(c["running"] for c in items) and not d.get("force"):
            raise RemoteError("some selected containers are running - tick 'force'")
        cmd = "$D rm" + (" -f" if d.get("force") else "") + (" -v" if d.get("volumes") else "")
    else:
        cmd = "$D " + a
    by = {}
    for c in items:
        by.setdefault(c["server"], []).append(c["name"])
    results = []

    def one(sv, names):
        script = ("for n in " + " ".join(q(n) for n in names) + '; do printf "@@C %s\\n" "$n"; '
                  + cmd + ' "$n" 2>&1; echo "@@RC $?"; done')
        try:
            if a == "remove":
                rc, out, err = dk(sv, script.replace("$D ", "$DK "), timeout=300)
            else:
                rc, out, err = drun(sv, script, timeout=300)
        except NeedSudo:
            return [{"server": sv, "name": n, "rc": -1, "output": "sudo password needed - unlock this server first"} for n in names]
        except RemoteError as e:
            return [{"server": sv, "name": n, "rc": -1, "output": str(e)} for n in names]
        res, cur = [], None
        for line in text(out).splitlines():
            if line.startswith("@@C "):
                cur = {"server": sv, "name": line[4:], "rc": -1, "output": ""}
                res.append(cur)
            elif line.startswith("@@RC ") and cur:
                cur["rc"] = int(line[5:] or 1)
            elif cur:
                cur["output"] += line + "\n"
        if not res:
            res = [{"server": sv, "name": n, "rc": rc or 1, "output": err.strip() or "failed"} for n in names]
        return res

    ths, outs = [], {}
    for sv, names in by.items():
        t = threading.Thread(target=lambda sv=sv, names=names: outs.__setitem__(sv, one(sv, names)))
        t.start(); ths.append(t)
    for t in ths:
        t.join()
    for sv in by:
        results += outs.get(sv, [])
    if a == "remove":
        with LOCK:
            for r in results:
                if r["rc"] == 0:
                    CONTAINERS.pop((r["server"], r["name"]), None)
    return {"results": results}


# ---- images / volumes / networks ----
_USED = ('ids=$($DK ps -aq); if [ -n "$ids" ]; then $DK inspect --format '
         + q('{{.Image}}\t{{.Name}}\t{{.State.Running}}\t{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}} {{end}}{{end}}')
         + ' $ids; fi')


def _parse_used(lines):
    used = []
    for l in lines:
        f = l.split("\t")
        if len(f) >= 3:
            used.append({"image": f[0], "name": f[1].lstrip("/"), "running": f[2] == "true",
                         "vols": (f[3] if len(f) > 3 else "").split()})
    return used


def _human(sz):
    m = re.match(r"([\d.]+)\s*([kKMGT]?B)", sz or "")
    if not m:
        return 0
    return int(float(m.group(1)) * {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}.get(m.group(2), 1))


def r_dres(d):
    s, kind = d.get("server"), d.get("kind")
    if s not in servers():
        raise RemoteError("unknown server")
    if kind == "images":
        script = ("$DK images --no-trunc --format " + q("{{.ID}}\t{{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}")
                  + "; echo @@USED; " + _USED)
    elif kind == "volumes":
        script = "$DK volume ls --format " + q("{{.Name}}\t{{.Driver}}") + "; echo @@USED; " + _USED
        if d.get("sizes"):
            script += "; echo @@DF; $DK system df -v 2>/dev/null"
    elif kind == "networks":
        script = ('n=$($DK network ls -q); [ -n "$n" ] && $DK network inspect --format '
                  + q("{{.Name}}\t{{.Driver}}\t{{.Scope}}\t{{range .IPAM.Config}}{{.Subnet}} {{end}}\t{{range .Containers}}{{.Name}} {{end}}")
                  + ' $n')
    else:
        raise RemoteError("bad kind")
    rc, out, err = dk(s, script, timeout=240 if d.get("sizes") else 60)
    secs, cur = {"main": [], "used": [], "df": []}, "main"
    for l in text(out).splitlines():
        if l == "@@USED":
            cur = "used"
        elif l == "@@DF":
            cur = "df"
        else:
            secs[cur].append(l)
    if rc != 0 and not secs["main"]:
        raise RemoteError(err.strip() or f"docker failed (exit {rc})")
    used, items = _parse_used(secs["used"]), []
    if kind == "images":
        for l in secs["main"]:
            f = l.split("\t")
            if len(f) == 5:
                by = [u for u in used if u["image"] == f[0]]
                items.append({"id": f[0], "repo": f[1], "tag": f[2], "size": f[3], "bytes": _human(f[3]), "created": f[4],
                              "used": [u["name"] for u in by], "running": any(u["running"] for u in by)})
    elif kind == "volumes":
        sizes, on = {}, False
        for l in secs["df"]:
            if l.startswith("Local Volumes space usage"):
                on = True
            elif on and l.strip() and not l.startswith("VOLUME NAME"):
                f = l.split()
                if len(f) >= 3:
                    sizes[f[0]] = f[-1]
            elif on and not l.strip() and sizes:
                on = False
        for l in secs["main"]:
            f = l.split("\t")
            if len(f) == 2:
                by = [u for u in used if f[0] in u["vols"]]
                items.append({"name": f[0], "driver": f[1], "used": [u["name"] for u in by], "running": any(u["running"] for u in by),
                              "size": sizes.get(f[0]), "bytes": _human(sizes.get(f[0]))})
    else:
        for l in secs["main"]:
            f = l.split("\t")
            if len(f) == 5:
                items.append({"name": f[0], "driver": f[1], "scope": f[2], "subnet": f[3].strip(), "used": f[4].split(),
                              "builtin": f[0] in ("bridge", "host", "none")})
    return {"items": items}


def r_dres_rm(d):
    s, kind, ident = d.get("server"), d.get("kind"), d.get("id") or ""
    if s not in servers():
        raise RemoteError("unknown server")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/@<>-]{0,300}", ident) or "<none>" in ident:
        raise RemoteError("bad name")
    guard(d, s, f"delete {str(kind).rstrip('s')} {ident}")
    if kind == "images":
        cmd = "$DK rmi " + q(ident)
    elif kind == "volumes":
        if d.get("confirm") != ident:
            raise RemoteError("confirmation name does not match")
        cmd = "$DK volume rm " + q(ident)
    elif kind == "networks":
        if ident in ("bridge", "host", "none"):
            raise RemoteError("built-in network cannot be removed")
        cmd = "$DK network rm " + q(ident)
    else:
        raise RemoteError("bad kind")
    return out_of(*dk(s, cmd + " 2>&1", timeout=180))


def r_dres_bulk(d):
    """delete many images / volumes / networks on one server in one ssh call."""
    s, kind, ids = d.get("server"), d.get("kind"), [str(x) for x in (d.get("ids") or [])][:300]
    if s not in servers():
        raise RemoteError("unknown server")
    if not ids:
        raise RemoteError("nothing selected")
    guard(d, s, f"delete {len(ids)} {kind}")
    if d.get("confirm") != "delete":
        raise RemoteError("type delete to confirm")
    for i in ids:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,300}", i):
            raise RemoteError(f"bad name: {i}")
    if kind == "networks" and any(i in ("bridge", "host", "none") for i in ids):
        raise RemoteError("built-in networks cannot be removed")
    cmd = {"images": "$DK rmi", "volumes": "$DK volume rm", "networks": "$DK network rm"}.get(kind)
    if not cmd:
        raise RemoteError("bad kind")
    script = ("for n in " + " ".join(q(i) for i in ids) + '; do printf "@@C %s\\n" "$n"; '
              + cmd + ' "$n" 2>&1; echo "@@RC $?"; done')
    rc, out, err = dk(s, script, timeout=300)
    res, cur = [], None
    for line in text(out).splitlines():
        if line.startswith("@@C "):
            cur = {"id": line[4:], "rc": -1, "output": ""}
            res.append(cur)
        elif line.startswith("@@RC ") and cur:
            cur["rc"] = int(line[5:] or 1)
        elif cur:
            cur["output"] += line + "\n"
    if not res:
        raise RemoteError(err.strip() or f"failed (exit {rc})")
    return {"results": res}

def r_compose(d):
    c = get_c(d)
    if not c["files"]:
        raise RemoteError("not a docker compose container")
    p = compose_prefix(c)
    cmd = {"up": f"{p} up -d", "pull": f"{p} pull && $C -p {q(c['project'])} " + " ".join("-f " + q(f) for f in c["files"]) + " up -d",
           "ps": f"{p} ps", "restart-svc": f"{p} restart {q(c['service'])}"}.get(d["cmd"])
    if d["cmd"] != "ps":
        guard(d, c["server"], f"docker compose {d['cmd']} ({c['project']})")
    if not cmd:
        raise RemoteError("bad compose command")
    return out_of(*drun(c["server"], cmd + " 2>&1", timeout=900))


def r_logs(d):
    c = get_c(d); tail = max(10, min(int(d.get("tail", 300)), 20000))
    rc, out, err = drun(c["server"], f"$D logs --tail {tail} {q(c['name'])} 2>&1", timeout=60)
    return {"output": text(out) + err}


def r_inspect(d):
    c = get_c(d)
    rc, out, err = drun(c["server"], f"$D inspect {q(c['name'])}")
    if rc != 0:
        raise RemoteError(err)
    return {"output": json.dumps(json.loads(out), indent=2)}


def r_exec(d):
    c = get_c(d)
    guard(d, c["server"], f"run a command in container {c['name']}")
    return out_of(*drun(c["server"], f"$D exec {q(c['name'])} sh -c {q(d['cmd'])} 2>&1", timeout=120))


# ---------------- PM2 ----------------
PM2 = {}   # server -> {"me": user, "daemons": [ {user, home, pid, mine, locked, error, procs} ]}

# Finds node + pm2 for a running daemon (uses the daemon's own node, so nvm installs work) and runs
# pm2 as the daemon's owner. As root: sudo -u <owner>. Secrets never leave the server: jlist is
# filtered on the server with node, env vars are dropped.
PM2_SH = r"""
pm2res() { N=$(readlink /proc/$1/exe 2>/dev/null); [ -x "$N" ] || N=$(command -v node || command -v nodejs)
  [ -n "$N" ] || { echo "node not found on PATH"; return 70; }
  ND=$(dirname "$N"); B=
  for c in "$ND/pm2" "$ND/../lib/node_modules/pm2/bin/pm2" "$(command -v pm2)" /usr/local/bin/pm2 /usr/bin/pm2 \
           /usr/local/lib/node_modules/pm2/bin/pm2 /usr/lib/node_modules/pm2/bin/pm2; do
    [ -n "$c" ] && [ -e "$c" ] && { B=$c; break; }; done
  [ -n "$B" ] || { echo "pm2 binary not found"; return 70; }; }
pm2x() { u=$1 h=$2; shift 2
  if [ "$u" = "$(id -un)" ]; then HOME=$(dirname "$h") PM2_HOME=$h PATH="$ND:$PATH" "$N" "$B" "$@"
  else sudo -n -u "$u" env HOME="$(dirname "$h")" PM2_HOME="$h" PATH="$ND:$PATH" "$N" "$B" "$@"; fi; }
pm2list() { echo "@@PM2 $1 $3"; R=$(pm2res "$2") || { echo "{\"error\":\"$R\"}"; return; }
  pm2res "$2"; pm2x "$1" "$3" jlist 2>&1 | "$N" -e "$F"; }
"""
PM2_FILTER = r"""let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{let a=null;
for(const l of s.split("\n")){if(/^\[(\{|\])/.test(l)){try{a=JSON.parse(l);break}catch(e){}}}
if(!a){console.log(JSON.stringify({error:s.replace(/\x1b\[[0-9;]*m/g,"").trim().slice(-400)||"no output from pm2"}));return}
console.log(JSON.stringify(a.map(p=>{const e=p.pm2_env||{},m=p.monit||{};return{name:p.name,id:p.pm_id,pid:p.pid,
status:e.status,cpu:m.cpu,mem:m.memory,uptime:e.pm_uptime,created:e.created_at,restarts:e.restart_time,unstable:e.unstable_restarts,
mode:e.exec_mode,instances:e.instances,version:e.version,script:e.pm_exec_path,cwd:e.pm_cwd,interp:e.exec_interpreter,
node:e.node_version,args:Array.isArray(e.args)?e.args.join(" "):(e.args||""),out:e.pm_out_log_path,err:e.pm_err_log_path,
watch:!!e.watch,autorestart:e.autorestart!==false,maxmem:e.max_memory_restart||null,cron:e.cron_restart||null}})))})"""
PM2_DISCOVER = r"""echo "@@ME $(id -un)"; ps -eo user:40=,pid=,args= 2>/dev/null | grep 'PM2 v.*God Daemon' | grep -v grep"""


def _pm2_script(body):
    return f"F={q(PM2_FILTER)}\n" + PM2_SH + body


def _pm2_parse(out):
    res, cur = {}, None
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("@@PM2 "):
            _, u, h = line.split(" ", 2)
            cur = (u, h)
        elif cur and line.strip():
            try:
                v = json.loads(line)
            except ValueError:
                continue
            res[cur] = v
            cur = None
    return res


def pm2_scan(server):
    rc, out, err = ssh(server, "sh -c " + q(PM2_DISCOVER), timeout=20)
    if rc not in (0, 1):
        raise RemoteError(err.strip() or "ssh failed")
    me, daemons = "", []
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("@@ME "):
            me = line[5:].strip()
            continue
        m = re.match(r"\s*(\S+)\s+(\d+)\s+PM2 v(\S+): God Daemon \((.+)\)\s*$", line)
        if m:
            daemons.append({"user": m[1], "pid": int(m[2]), "pm2ver": m[3], "home": m[4], "procs": [],
                            "locked": False, "error": ""})
    sel = ((load_layout() or {}).get("pm2users") or {}).get(server)   # None = automatic
    for dm in daemons:
        dm["mine"] = dm["user"] == me
        dm["skip"] = bool(sel) and dm["user"] not in sel   # not selected: don't even read it
    # own daemons without sudo, other users' daemons with sudo (one call each)
    for mine in (True, False):
        grp = [dm for dm in daemons if dm["mine"] == mine and not dm["skip"]]
        if not grp:
            continue
        body = "\n".join(f"pm2list {q(dm['user'])} {dm['pid']} {q(dm['home'])}" for dm in grp)
        try:
            if not mine and INFO.get(server, {}).get("sudo") is None:
                probe(server)
            rc, o, e = run(server, _pm2_script(body), elevate=not mine, timeout=90)
            got = _pm2_parse(o)
            for dm in grp:
                v = got.get((dm["user"], dm["home"]))
                if isinstance(v, list):
                    dm["procs"] = v
                else:
                    dm["error"] = (v or {}).get("error") or (e.strip() or "could not read pm2 list")
        except NeedSudo:
            for dm in grp:
                dm["locked"] = True
        except RemoteError as ex:
            for dm in grp:
                dm["error"] = str(ex)
    PM2[server] = {"me": me, "daemons": daemons, "users": sel or None}
    return PM2[server]


def r_pm2_users(d):
    s, users = d.get("server"), d.get("users")
    if s not in servers():
        raise RemoteError("unknown server")
    if users is not None and (not isinstance(users, list) or not all(isinstance(u, str) and re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", u) for u in users)):
        raise RemoteError("bad user list")
    with LAYOUT_LOCK:
        lay = ensure_layout(); pu = lay.setdefault("pm2users", {})
        if users:
            pu[s] = users
        else:
            pu.pop(s, None)
        save_layout(lay)
    return {"pm2": pm2_scan(s)}


def get_pm2(d):
    s = d.get("server")
    for dm in PM2.get(s, {}).get("daemons", []):
        if dm["user"] == d.get("user") and dm["home"] == d.get("home"):
            for p in dm["procs"]:
                if p.get("id") == d.get("id"):
                    return s, dm, p
            raise RemoteError("process not found - refresh")
    raise RemoteError("pm2 daemon not found - refresh")


def pm2_run(s, dm, args, timeout=120):
    script = _pm2_script(f"R=$(pm2res {dm['pid']}) || {{ echo \"$R\"; exit 70; }}\npm2res {dm['pid']}\n"
                         f"pm2x {q(dm['user'])} {q(dm['home'])} {args} 2>&1")
    return run(s, script, elevate=not dm["mine"], timeout=timeout)


def r_pm2(d):
    return {"pm2": pm2_scan(d["server"]), "servers": server_list()}


def r_pm2_action(d):
    s, dm, p = get_pm2(d)
    a = d.get("action")
    if a not in ("restart", "reload", "stop", "start", "flush"):
        raise RemoteError("bad action")
    guard(d, s, f"pm2 {a} {p.get('name', '')}")
    rc, out, err = pm2_run(s, dm, f"{a} {int(p['id'])}")
    out = "\n".join(l for l in text(out).splitlines() if not re.search("[\u2500-\u257f]", l)).encode()
    if a in ("stop", "start", "restart", "reload"):
        try:
            pm2_scan(s)
        except Exception:
            pass
    return {**out_of(rc, out, err), "pm2": PM2.get(s)}


def r_pm2_save(d):
    s = d.get("server")
    dm = next((x for x in PM2.get(s, {}).get("daemons", []) if x["user"] == d.get("user") and x["home"] == d.get("home")), None)
    if not dm:
        raise RemoteError("pm2 daemon not found - refresh")
    return out_of(*pm2_run(s, dm, "save"))


def r_pm2_logs(d):
    s, dm, p = get_pm2(d)
    n = max(10, min(int(d.get("tail", 300)), 20000))
    parts = []
    for label, path in (("stdout", p.get("out")), ("stderr", p.get("err"))):
        if path and path != "/dev/null":
            parts.append(f"echo '@@LOG {label} '{q(path)}; tail -n {n} {q(path)} 2>&1")
    if not parts:
        return {"logs": []}
    rc, out, err = run(s, "\n".join(parts), elevate=not dm["mine"], timeout=60)
    logs, cur = [], None
    for line in text(out).splitlines(keepends=True):
        if line.startswith("@@LOG "):
            _, lab, path = line.rstrip("\n").split(" ", 2)
            cur = {"name": lab, "path": path, "text": ""}
            logs.append(cur)
        elif cur is not None:
            cur["text"] += line
    return {"logs": logs}


# ---------------- elevation helper ----------------
def can_elevate(server):
    if INFO.get(server, {}).get("sudo") is None:
        probe(server)
    m = INFO.get(server, {}).get("sudo")
    return m == "nopass" or (m == "pass")      # 'pass' raises NeedSudo until unlocked -> UI asks once


# ---------------- disk analysis & cleanup ----------------
DISK_SH = r"""
echo @@DF; df -PB1 / /var/lib/docker /home 2>/dev/null | awk 'NR>1{print $6"\t"$2"\t"$3"\t"$4}' | sort -u
if $DK info >/dev/null 2>&1; then
  echo @@DOCKER; $DK system df --format '{{.Type}}\t{{.TotalCount}}\t{{.Active}}\t{{.Size}}\t{{.Reclaimable}}' 2>/dev/null
  echo @@CNAMES; $DK ps -a --no-trunc --format '{{.ID}}\t{{.Names}}' 2>/dev/null
fi
echo @@CLOGS; $S find /var/lib/docker/containers -name '*-json.log' -printf '%b\t%p\n' 2>/dev/null | awk -F'\t' '{print $1*512"\t"$2}' | sort -rn | head -20
echo @@JOURNAL; $S journalctl --disk-usage 2>/dev/null | grep -o '[0-9.]*[KMGT]\?B\?' | head -1
echo @@VARLOG; $S du -s -B1 /var/log 2>/dev/null | cut -f1
echo @@ROTATED; $S find /var/log -type f \( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' \) -printf '%b\n' 2>/dev/null | awk '{s+=$1*512;n++}END{print s+0"\t"n+0}'
echo @@BIGLOGS; $S find /var/log -type f -size +50M -printf '%b\t%p\n' 2>/dev/null | awk -F'\t' '$1*512>50000000{print $1*512"\t"$2}' | sort -rn | head -10
echo @@PM2; $S du -s -B1 /root/.pm2/logs /home/*/.pm2/logs 2>/dev/null
echo @@TOPDIRS; $S timeout 45 du -x -B1 -d1 --exclude=/var/lib/docker --exclude=/var/lib/containerd /var /var/lib /home /opt /srv /root /tmp /usr/local 2>/dev/null | sort -rn | awk '!seen[$2]++' | head -20
true
"""


def _sz(v):
    m = re.match(r"([\d.]+)\s*([KMGT]?)B?", v or "")
    if not m:
        return 0
    return int(float(m[1]) * {"": 1, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}[m[2]])


def r_disk(d):
    s = d["server"]
    rc, out, err = srun(s, DISK_SH, timeout=150)
    el = INFO.get(s, {}).get("sudo") in ("nopass", "pass")
    sec, cur = {}, None
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("@@"):
            cur = line[2:].strip(); sec[cur] = []
        elif cur and line.strip():
            sec[cur].append(line.split("\t"))
    names = {r[0]: r[1] for r in sec.get("CNAMES", []) if len(r) > 1}
    res = {"elevated": el,
           "df": [{"mount": r[0], "size": int(r[1]), "used": int(r[2]), "free": int(r[3])} for r in sec.get("DF", []) if len(r) == 4],
           "docker": [{"type": r[0], "total": r[1], "active": r[2], "size": _sz(r[3]), "reclaim": _sz(r[4]),
                       "reclaim_txt": r[4]} for r in sec.get("DOCKER", []) if len(r) >= 5],
           "has_docker": "DOCKER" in sec,
           "clogs": [{"size": int(r[0]), "path": r[1], "name": names.get(r[1].split("/")[-2], r[1].split("/")[-2][:12])}
                     for r in sec.get("CLOGS", []) if len(r) == 2 and r[0].isdigit()],
           "journal": _sz((sec.get("JOURNAL") or [[""]])[0][0]),
           "varlog": int((sec.get("VARLOG") or [["0"]])[0][0] or 0),
           "rotated": dict(zip(("size", "count"), map(int, (sec.get("ROTATED") or [["0", "0"]])[0][:2]))) if sec.get("ROTATED") else {"size": 0, "count": 0},
           "biglogs": [{"size": int(r[0]), "path": r[1]} for r in sec.get("BIGLOGS", []) if len(r) == 2 and r[0].isdigit()],
           "pm2": [{"size": int(r[0] or 0), "path": r[1]} for r in sec.get("PM2", []) if len(r) == 2],
           "topdirs": [{"size": int(r[0]), "path": r[1]} for r in sec.get("TOPDIRS", []) if len(r) == 2 and r[0].isdigit()]}
    return res


CLEAN = {
    "docker_dangling": ("$DK image prune -f", ""),
    "docker_images": ("$DK image prune -a -f", ""),
    "docker_builder": ("$DK builder prune -f", ""),
    "docker_containers": ("$DK container prune -f", ""),
    "clogs_big": ("$S find /var/lib/docker/containers -name '*-json.log' -size +100M -exec truncate -s 0 {} + && echo done", ""),
    "journal": ("$S journalctl --vacuum-size=200M", ""),
    "rotated": ("$S find /var/log -type f \\( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' \\) -print -delete | wc -l | sed 's/$/ files deleted/'", ""),
}


def r_cleanup(d):
    s, a, arg = d["server"], d.get("action"), d.get("arg", "")
    guard(d, s, "disk cleanup")
    if a in CLEAN:
        cmd = CLEAN[a][0]
    elif a == "clog" and re.fullmatch(r"/var/lib/docker/containers/[0-9a-f]{64}/[0-9a-f]{64}-json\.log", arg):
        cmd = f"$S truncate -s 0 {q(arg)} && echo emptied {q(arg)}"
    elif a == "pm2logs" and re.fullmatch(r"(/root|/home/[A-Za-z0-9._-]+)/\.pm2/logs", arg):
        cmd = (f"$S find {q(arg)} -type f -name '*__*.log' -delete; "          # pm2-logrotate old files
               f"$S find {q(arg)} -type f -name '*.log' -exec truncate -s 0 {{}} + ; echo emptied PM2 logs in {q(arg)}")
    else:
        raise RemoteError("bad cleanup action")
    rc, out, err = srun(s, f"df -PB1 / | awk 'NR==2{{print \"@@FREE \"$4}}'; {cmd} 2>&1; df -PB1 / | awk 'NR==2{{print \"@@FREE \"$4}}'",
                        timeout=600)
    t = text(out)
    fr = re.findall(r"@@FREE (\d+)", t)
    freed = int(fr[-1]) - int(fr[0]) if len(fr) == 2 else None
    return {"rc": rc, "output": re.sub(r"@@FREE \d+\n?", "", t + err).strip(), "freed": freed}


# ---------------- run a command on many servers ----------------
def r_multirun(d):
    srvs, cmd, sudo = d.get("servers") or [], (d.get("cmd") or "").strip(), bool(d.get("sudo"))
    if not cmd:
        raise RemoteError("enter a command")
    known = servers()
    srvs = [x for x in srvs if x in known][:50]
    for sv in srvs:
        guard(d, sv, "run a command")
    tmo = max(5, min(int(d.get("timeout") or 120), 900))

    def one(sv):
        t0 = time.time()
        try:
            rc, out, err = run(sv, cmd, elevate=sudo, timeout=tmo)
            return {"server": sv, "rc": rc, "output": (text(out) + ANSI.sub("", err)).rstrip()[-200000:], "ms": int((time.time() - t0) * 1000)}
        except NeedSudo:
            return {"server": sv, "rc": None, "need_sudo": True, "output": "sudo password needed - unlock this server first"}
        except Exception as e:
            return {"server": sv, "rc": None, "output": str(e)}
    with ThreadPoolExecutor(16) as ex:
        return {"results": list(ex.map(one, srvs))}


# ---------------- saved commands (runbooks) ----------------
CMDS_JSON = os.path.join(CONF, "commands.json")


def load_cmds():
    try:
        return json.load(open(CMDS_JSON))
    except (OSError, ValueError):
        return [{"id": "disk", "name": "Disk usage", "cmd": "df -h / && echo && du -xsh /var/log /var/lib/docker 2>/dev/null", "sudo": False},
                {"id": "docker-ps", "name": "Docker containers", "cmd": "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'", "sudo": False},
                {"id": "pm2", "name": "PM2 list", "cmd": "pm2 list", "sudo": False},
                {"id": "mem", "name": "Memory & load", "cmd": "uptime && free -h", "sudo": False},
                {"id": "top-cpu", "name": "Top CPU processes", "cmd": "ps -eo pid,user,%cpu,%mem,etime,cmd --sort=-%cpu | head -15", "sudo": False}]


def r_cmds(d):
    op = d.get("op", "list")
    lst = load_cmds()
    if op == "save":
        name, cmd = (d.get("name") or "").strip()[:60], (d.get("cmd") or "").strip()
        if not name or not cmd:
            raise RemoteError("name and command are required")
        cid = d.get("id") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + "-" + secrets.token_hex(2)
        lst = [c for c in lst if c["id"] != cid] + [{"id": cid, "name": name, "cmd": cmd, "sudo": bool(d.get("sudo"))}]
    elif op == "delete":
        lst = [c for c in lst if c["id"] != d.get("id")]
    if op in ("save", "delete"):
        os.makedirs(CONF, exist_ok=True)
        json.dump(lst, open(CMDS_JSON, "w"), indent=2)
    return {"cmds": lst}


# ---------------- global search index ----------------
INDEX = {"t": 0}


def r_index(d):
    if d.get("force") or time.time() - INDEX["t"] > 90:
        lay = ensure_layout()
        dk, pm = page_servers(lay, "docker"), page_servers(lay, "pm2")
        with ThreadPoolExecutor(16) as ex:
            list(ex.map(scan_one, dk))

            def ps(sv):
                try:
                    pm2_scan(sv)
                except Exception:
                    pass
            list(ex.map(ps, pm))
        INDEX["t"] = time.time()
    lay = ensure_layout()
    pm = page_servers(lay, "pm2")
    procs = []
    for sv in pm:
        dd = PM2.get(sv)
        if not dd:
            continue
        for dm in dd["daemons"]:
            procs += [{"server": sv, "user": dm["user"], "home": dm["home"], "id": p.get("id"), "name": p.get("name"),
                       "status": p.get("status"), "script": p.get("script") or "", "cwd": p.get("cwd") or ""} for p in dm["procs"]]
    with LOCK:
        cs = list(CONTAINERS.values())
    return {"containers": cs, "pm2": procs, "pages": {"docker": page_servers(lay, "docker"), "pm2": pm}, "servers": server_list()}


ROUTES = {"/api/servers": r_servers, "/api/server_add": r_server_add, "/api/server_remove": r_server_remove,
          "/api/server_get": r_server_get, "/api/server_edit": r_server_edit,
          "/api/scan": r_scan, "/api/stats": r_stats, "/api/hist": r_hist,"/api/sudo": r_sudo, "/api/file": r_file, "/api/validate": r_validate,
          "/api/save": r_save, "/api/discard": r_discard, "/api/action": r_action, "/api/remove": r_remove, "/api/bulk": r_bulk, "/api/dres": r_dres, "/api/dres_rm": r_dres_rm, "/api/dres_bulk": r_dres_bulk, "/api/compose": r_compose,
          "/api/logs": r_logs, "/api/inspect": r_inspect, "/api/exec": r_exec,
          "/api/pm2": r_pm2, "/api/disk": r_disk, "/api/cleanup": r_cleanup, "/api/multirun": r_multirun, "/api/cmds": r_cmds, "/api/index": r_index, "/api/pm2_users": r_pm2_users, "/api/layout": r_layout, "/api/layout_get": lambda d: {"layout": ensure_layout()[d.get("page") if d.get("page") in PAGES else "docker"]}, "/api/pm2_action": r_pm2_action, "/api/pm2_save": r_pm2_save, "/api/pm2_logs": r_pm2_logs}


# ---------------- web terminal (WebSocket <-> pty running `ssh -tt server`) ----------------
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WS:
    def __init__(self, sock):
        self.sock, self.lock = sock, threading.Lock()

    def _read(self, n):
        b = b""
        while len(b) < n:
            c = self.sock.recv(n - len(b))
            if not c:
                raise EOFError
            b += c
        return b

    def recv(self):
        """Returns (opcode, payload) of a complete message."""
        data, op0 = b"", None
        while True:
            h = self._read(2)
            fin, op, masked, ln = h[0] & 0x80, h[0] & 0x0F, h[1] & 0x80, h[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            if ln > 1 << 22:
                raise EOFError
            mask = self._read(4) if masked else b"\0\0\0\0"
            pl = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(ln)))
            if op >= 8:                      # control frame
                if op == 9:
                    self.send(pl, 0xA)
                    continue
                if op == 0xA:
                    continue
                return op, pl
            if op0 is None:
                op0 = op
            data += pl
            if fin:
                return op0, data

    def send(self, data, op=2):
        n = len(data)
        h = bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536
                                  else bytes([127]) + struct.pack(">Q", n))
        with self.lock:
            self.sock.sendall(h + data)


def terminal(handler, server, container=None):
    req = handler.headers
    key = req.get("Sec-WebSocket-Key", "")
    acc = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    handler.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         f"Sec-WebSocket-Accept: {acc}\r\n\r\n").encode())
    handler.wfile.flush()
    ws = WS(handler.connection)
    pid, fd = pty.fork()
    if pid == 0:                                         # child: the ssh session
        os.environ["TERM"] = "xterm-256color"
        cmd = SSH[:]
        i = cmd.index("BatchMode=yes"); del cmd[i - 1:i + 1]   # allow interactive prompts (passwords, host keys)
        remote = []
        if container:   # interactive shell inside a container (sudo asks its password in the terminal if needed)
            dk = "docker" if INFO.get(server, {}).get("docker", True) else "sudo docker"
            remote = [f"{dk} exec -it {q(container)} sh -c " + q("command -v bash >/dev/null && exec bash || exec sh")]
        try:
            os.execvp("ssh", cmd + ["-tt"] + target(server) + remote)
        finally:
            os._exit(127)
    alive = True

    def pump():                                          # pty -> browser
        nonlocal alive
        try:
            while alive:
                r, _, _ = select.select([fd], [], [], 0.5)
                if r:
                    try:
                        b = os.read(fd, 65536)
                    except OSError:
                        b = b""
                    if not b:
                        break
                    ws.send(b)
        except Exception:
            pass
        alive = False
        try:
            ws.send(b"\r\n\x1b[2m[session closed]\x1b[0m\r\n")
            ws.send(b"", 8)
        except Exception:
            pass

    t = threading.Thread(target=pump, daemon=True); t.start()
    try:
        while alive:
            op, pl = ws.recv()
            if op == 8:
                break
            if not pl:
                continue
            if pl[:1] == b"\x01":                         # resize: \x01{"c":cols,"r":rows}
                try:
                    j = json.loads(pl[1:]); fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(j["r"]), int(j["c"]), 0, 0))
                except (ValueError, KeyError, OSError):
                    pass
            else:                                        # keystrokes
                os.write(fd, pl[1:] if pl[:1] == b"\x00" else pl)
    except (EOFError, OSError, ConnectionError):
        pass
    finally:
        alive = False
        try:
            os.kill(pid, signal.SIGHUP)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        handler.close_connection = True


# ---------------- file browser / download ----------------
def _clean_path(p):
    p = (p or "").strip() or "~"
    if "\0" in p or "\n" in p or len(p) > 1024:
        raise RemoteError("bad path")
    return p


def _rpath(p):
    """shell expression for a remote path; ~ expands to the login user's home"""
    if p == "~" or p.startswith("~/"):
        return '"$HOME"' + (q(p[1:]) if len(p) > 1 else "")
    return q(p)


def r_ls(d):
    s, p = d["server"], _clean_path(d.get("path"))
    P = _rpath(p)
    script = (f'P={P}; if [ ! -e "$P" ] && ! $S test -e "$P" 2>/dev/null; then echo "@@ERR no such file or folder"; exit 0; fi; '
              'if [ -d "$P" ] || $S test -d "$P" 2>/dev/null; then K=dir; else K=file; fi; '
              'cd / ; R=$(readlink -f "$P" 2>/dev/null || $S readlink -f "$P"); echo "@@PATH $R"; echo "@@KIND $K"; '
              'if [ "$K" = dir ]; then if [ -r "$P" ] && [ -x "$P" ]; then F=; else F=$S; echo @@SUDO; fi; '
              '$F find "$P" -mindepth 1 -maxdepth 1 -printf "%y\\t%s\\t%T@\\t%u\\t%f\\n" 2>/dev/null | head -2000; '
              'else if [ -r "$P" ]; then :; else echo @@SUDO; fi; $S stat --printf "f\\t%s\\t%Y\\t%U\\t%n\\n" "$P" 2>/dev/null || stat --printf "f\\t%s\\t%Y\\t%U\\t%n\\n" "$P"; fi')
    rc, out, err = srun(s, script, timeout=40, lazy=True)       # try without sudo first
    if b"@@SUDO" in out and _sudo_locked(s):
        raise NeedSudo(s)                                          # this folder/file needs sudo: ask, then the UI retries
    res = {"path": p, "kind": "dir", "items": [], "sudo": False}
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("@@ERR "):
            raise RemoteError(f"{p}: {line[6:]}")
        if line.startswith("@@PATH "):
            res["path"] = line[7:] or p
        elif line.startswith("@@KIND "):
            res["kind"] = line[7:]
        elif line == "@@SUDO":
            res["sudo"] = True
        else:
            f = line.split("\t")
            if len(f) == 5:
                try:
                    res["items"].append({"type": {"d": "dir", "l": "link"}.get(f[0], "file"), "size": int(f[1]),
                                         "mtime": int(float(f[2])), "owner": f[3], "name": f[4].split("/")[-1]})
                except ValueError:
                    pass
    res["items"].sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return res


ROUTES["/api/ls"] = r_ls

# ---- edit a text file on the server ----
EDIT_MAX = 5 * 1024 * 1024


def r_fread(d):
    s, p = d["server"], _clean_path(d.get("path"))
    script = ('P=' + _rpath(p) + '; '
              'if [ ! -e "$P" ] && ! $S test -e "$P" 2>/dev/null; then echo "@@ERR no such file"; exit 0; fi; '
              'if [ -d "$P" ] || $S test -d "$P" 2>/dev/null; then echo "@@ERR this is a folder"; exit 0; fi; '
              'if [ -r "$P" ]; then F=; elif [ -n "$S" ] && $S test -r "$P"; then F=$S; echo @@SUDO; '
              'else echo "@@ERR you have no permission to read this file"; exit 0; fi; '
              'echo "@@PATH $(readlink -f "$P" 2>/dev/null || echo "$P")"; '
              'echo "@@SIZE $($F stat -c %s "$P")"; '
              'if [ -w "$P" ]; then echo @@W; elif [ -n "$S" ]; then echo @@WSUDO; else echo "@@W?"; fi; '
              f'[ "$($F stat -c %s "$P")" -le {EDIT_MAX} ] || exit 0; '
              'echo @@DATA; $F base64 -w0 "$P"; echo')
    rc, out, err = srun(s, script, timeout=60, lazy=True)       # try without sudo first
    if b"@@ERR you have no permission" in out and _sudo_locked(s):
        raise NeedSudo(s)                                          # ask for the password, then the UI retries
    res, data = {"path": p, "sudo": False, "writable": "no", "size": 0}, None
    for line in out.decode("utf-8", "replace").splitlines():
        if data is not None:
            data += line
        elif line.startswith("@@ERR "):
            raise RemoteError(f"{p}: {line[6:]}")
        elif line.startswith("@@PATH "):
            res["path"] = line[7:]
        elif line.startswith("@@SIZE "):
            res["size"] = int(line[7:] or 0)
        elif line == "@@SUDO":
            res["sudo"] = True
        elif line == "@@W":
            res["writable"] = "yes"
        elif line == "@@WSUDO":
            res["writable"] = "sudo"
        elif line == "@@W?" and _sudo_locked(s):
            res["writable"] = "sudo"                               # sudo not unlocked yet; saving will ask for it
        elif line == "@@DATA":
            data = ""
    if res["size"] > EDIT_MAX:
        raise RemoteError(f"this file is {res['size'] / 1048576:.1f} MB; editing works up to {EDIT_MAX // 1048576} MB. Download it instead.")
    if data is None:
        raise RemoteError((err.strip() or "could not read the file")[-300:])
    raw = base64.b64decode(data)
    if b"\0" in raw[:65536]:
        raise RemoteError("this looks like a binary file, so it can't be edited here. Download it instead.")
    try:
        res["content"] = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise RemoteError("this file is not UTF-8 text, so it can't be edited here. Download it instead.")
    res["sha"] = hashlib.sha256(raw).hexdigest()
    return res


def _sudo_locked(server):
    """sudo needs a password here and it hasn't been entered yet."""
    return INFO.get(server, {}).get("sudo") == "pass" and server not in SUDO_PW


def r_fdiff(d):
    a, b, name = d.get("orig", ""), d.get("content", ""), d.get("name", "file")
    return {"diff": "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"{name} (on server)", f"{name} (edited)"))}


def r_fwrite(d):
    s, p, sha = d["server"], _clean_path(d.get("path")), d.get("sha") or ""
    data = (d.get("content") or "").encode("utf-8")
    if len(data) > EDIT_MAX:
        raise RemoteError("too large to save from the editor")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RemoteError("missing file version - reopen the file")
    guard(d, s, f"save {p}")
    script = ('P=' + _rpath(p) + '; B=' + ("1" if d.get("backup", True) else "0") + '; '
              'T=$(mktemp "${TMPDIR:-/tmp}/dctl.XXXXXX") || exit 70; cat > "$T"; '
              'D=$(dirname "$P"); '
              'if [ -w "$P" ] && { [ "$B" = 0 ] || [ -w "$D" ]; }; then F=; elif [ -n "$S" ]; then F=$S; else rm -f "$T"; exit 77; fi; '
              'cur=$($F sha256sum "$P" | cut -d" " -f1); [ "$cur" = ' + q(sha) + ' ] || { rm -f "$T"; exit 75; }; '
              'if [ "$B" = 1 ]; then BK="$P.bak.$(date +%Y%m%d-%H%M%S)"; $F cp -p "$P" "$BK" || { rm -f "$T"; exit 74; }; echo "@@BAK $BK"; fi; '
              '$F tee "$P" < "$T" > /dev/null; rc=$?; rm -f "$T"; [ $rc = 0 ] || exit 73; '
              '[ -n "$F" ] && echo @@SUDO; echo "@@SHA $($F sha256sum "$P" | cut -d" " -f1)"')
    rc, out, err = srun(s, script, timeout=120, data=data, lazy=True)
    if rc == 77 and _sudo_locked(s):
        raise NeedSudo(s)
    msg = {75: "the file was changed on the server after you opened it. Reload it (copy your edits first) and try again.",
           77: "you have no permission to write this file, and no sudo on this server.",
           74: "could not create the backup copy, so nothing was saved.",
           73: "writing the file failed.", 70: "could not create a temporary file on the server."}
    if rc != 0:
        raise RemoteError(msg.get(rc, "") + (" " + err.strip()[-300:] if err.strip() else "") or f"save failed (exit {rc})")
    res = {"backup": None, "sha": None, "sudo": False}
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("@@BAK "):
            res["backup"] = line[6:]
        elif line.startswith("@@SHA "):
            res["sha"] = line[6:]
        elif line == "@@SUDO":
            res["sudo"] = True
    if res["sha"] != hashlib.sha256(data).hexdigest():
        raise RemoteError("saved, but the file on the server doesn't match what was sent - please reload and check it.")
    return res


ROUTES.update({"/api/fread": r_fread, "/api/fdiff": r_fdiff, "/api/fwrite": r_fwrite})


def r_protect(d):
    s, on = d.get("server"), bool(d.get("on"))
    if s not in servers():
        raise RemoteError("unknown server")
    p = protected()
    if not on and s in p:
        guard(d, s, "turn off protection")
    (p.add if on else p.discard)(s)
    save_protected(p)
    return {"on": on}


# ---- upload files from this computer into a folder on the server ----
UPLOADS = {}      # ticket -> {"server", "dir", "exp"}: issued by /api/upcheck after the permission + protection checks


def _upname(n):
    n = str(n or "")
    if n in ("", ".", "..") or "/" in n or "\0" in n or "\n" in n or len(n.encode()) > 255:
        raise RemoteError(f"bad file name: {n[:60]!r}")
    return n


def r_upcheck(d):
    s, p = d["server"], _clean_path(d.get("dir"))
    names = [_upname(n) for n in (d.get("names") or [])][:500]
    if not names:
        raise RemoteError("no files")
    guard(d, s, f"upload {len(names)} file{'s' if len(names) > 1 else ''} to {p}")
    script = (f'D={_rpath(p)}; if [ -d "$D" ] || $S test -d "$D" 2>/dev/null; then :; else echo "@@ERR not a folder"; exit 0; fi; '
              'cd / ; echo "@@PATH $(readlink -f "$D" 2>/dev/null || $S readlink -f "$D")"; '
              'if [ -w "$D" ] && [ -x "$D" ]; then echo @@W; elif [ -n "$S" ]; then echo @@WSUDO; else echo @@WNO; fi; '
              'while IFS= read -r N; do if [ -e "$D/$N" ] || $S test -e "$D/$N" 2>/dev/null; then '
              'if [ -d "$D/$N" ] || $S test -d "$D/$N" 2>/dev/null; then echo "@@DIR $N"; else echo "@@EX $N"; fi; fi; done')
    rc, out, err = srun(s, script, timeout=40, lazy=True, data=("\n".join(names) + "\n").encode())
    res = {"path": p, "exists": [], "dirs": [], "sudo": False}
    w = None
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("@@ERR "):
            raise RemoteError(f"{p}: {line[6:]}")
        if line.startswith("@@PATH "):
            res["path"] = line[7:] or p
        elif line in ("@@W", "@@WSUDO", "@@WNO"):
            w = line
        elif line.startswith("@@EX "):
            res["exists"].append(line[5:])
        elif line.startswith("@@DIR "):
            res["dirs"].append(line[6:])
    if w is None:
        raise RemoteError(err.strip()[-300:] or f"could not check {p}")
    if w == "@@WNO":
        if _sudo_locked(s):
            raise NeedSudo(s)
        raise RemoteError(f"you have no permission to write in {res['path']}, and no sudo on this server")
    res["sudo"] = w == "@@WSUDO"
    now = time.time()
    for k in [k for k, v in UPLOADS.items() if v["exp"] < now]:
        del UPLOADS[k]
    res["ticket"] = secrets.token_urlsafe(18)
    UPLOADS[res["ticket"]] = {"server": s, "dir": res["path"], "exp": now + 3600}
    return res


def upload(handler, tk, name, overwrite, length):
    """Stream the request body into DIR/.NAME.dctl-up.PID on the server (sudo only if the folder needs it),
    check size + sha256, then move it into place keeping the owner and mode of a replaced file."""
    s, D = tk["server"], tk["dir"]
    pre, stdin = _sudo_pre(s, lazy=True)
    script = (f'D={q(D)}; N={q(name)}; L={int(length)}; O={1 if overwrite else 0}; P="$D/$N"; T="$D/.$N.dctl-up.$$"; '
              'if [ -w "$D" ] && [ -x "$D" ]; then F=; elif [ -n "$S" ]; then F=$S; else exit 77; fi; '
              'if [ -e "$P" ] || $F test -e "$P" 2>/dev/null; then [ "$O" = 1 ] || exit 76; '
              '{ [ -d "$P" ] || $F test -d "$P"; } && exit 78; fi; '
              '$F tee "$T" > /dev/null || { $F rm -f "$T"; exit 73; }; '
              '[ "$($F stat -c %s "$T")" = "$L" ] || { $F rm -f "$T"; exit 72; }; '
              'echo "@@SHA $($F sha256sum "$T" | cut -d" " -f1)"; [ -n "$F" ] && echo @@SUDO; echo "@@TMP $T"')
    proc = subprocess.Popen(SSH + target(s) + ["sh -c " + q(PRELUDE + pre + script)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    h, left, broken = hashlib.sha256(), length, False
    try:
        if stdin:
            proc.stdin.write(stdin)
        while left > 0:
            chunk = handler.rfile.read(min(262144, left))
            if not chunk:
                break
            left -= len(chunk)
            h.update(chunk)
            if not broken:
                try:
                    proc.stdin.write(chunk)
                except (BrokenPipeError, OSError):
                    broken = True          # remote stopped early (keep reading the body so the browser gets our answer)
    except (BrokenPipeError, OSError):
        pass
    try:
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    out, err = proc.stdout.read(), proc.stderr.read()
    proc.wait()
    out, err = out.decode("utf-8", "replace"), err.decode("utf-8", "replace").strip()
    info = dict(l[2:].split(" ", 1) for l in out.splitlines() if l.startswith("@@") and " " in l)
    tmp = info.get("TMP", "")
    if "@@SUDOFAIL" in out:
        SUDO_PW.pop(s, None)
    msg = {77: "you have no permission to write in this folder.", 76: "a file with this name already exists.",
           78: "a folder with this name already exists.", 73: "writing the file on the server failed (disk full?).",
           72: "the upload was incomplete."}
    if left > 0:
        raise RemoteError("the upload was interrupted")
    if proc.returncode != 0 or not tmp:
        raise RemoteError(msg.get(proc.returncode, "") + (" " + err[-300:] if err else "") or f"upload failed (exit {proc.returncode})")
    sudo = "@@SUDO" in out
    finish = (f'T={q(tmp)}; P={q(D + "/" + name)}; F={"$S" if sudo else ""}; '
              'if [ -e "$P" ] || $F test -e "$P" 2>/dev/null; then $F chown --reference="$P" "$T" 2>/dev/null; $F chmod --reference="$P" "$T" 2>/dev/null; '
              'elif [ -n "$F" ]; then $F chown --reference="$(dirname "$P")" "$T" 2>/dev/null; $F chmod 644 "$T"; fi; '
              '$F mv -f "$T" "$P" || { $F rm -f "$T"; exit 71; }')
    abort = f'F={"$S" if sudo else ""}; $F rm -f {q(tmp)}'
    if info.get("SHA") != h.hexdigest():
        srun(s, abort, timeout=30, lazy=True)
        raise RemoteError("the file on the server did not match what was sent, so it was not saved. Try again.")
    try:
        rc, _, err = srun(s, finish, timeout=60, lazy=True)
    except Exception:
        rc, err = -1, ""
    if rc != 0:
        try:
            srun(s, abort, timeout=30, lazy=True)
        except Exception:
            pass
        raise RemoteError("could not move the file into place. " + err.strip()[-300:])
    return {"path": D.rstrip("/") + "/" + name, "size": length, "sudo": sudo}


ROUTES.update({"/api/protect": r_protect, "/api/upcheck": r_upcheck})


# ---- files inside a container (docker exec for listing, docker cp for download) ----
CLS_SH = r"""P="$1"
if [ -d "$P" ]; then cd "$P" || exit 3; echo "@@PATH $(pwd)"; echo "@@KIND dir"
  if command -v stat >/dev/null 2>&1; then stat -c '%F/%s/%Y/%n' -- .* * 2>/dev/null | head -3000
  else ls -1Ap | head -3000 | sed 's|^|?/0/0/|'; fi
elif [ -e "$P" ]; then echo "@@PATH $P"; echo "@@KIND file"; stat -c '%F/%s/%Y/%n' -- "$P" 2>/dev/null || echo "regular file/0/0/$P"
else echo "@@ERR no such file or folder"; fi"""


def _cpath(p):
    p = _clean_path(p or "/")
    if p.startswith("~"):
        p = "/" + p[1:].lstrip("/")
    if not p.startswith("/"):
        p = "/" + p
    return p


def r_cls(d):
    c = get_c(d)
    p = _cpath(d.get("path"))
    res = {"path": p, "kind": "dir", "items": [], "stopped": not c["running"]}
    if not c["running"]:
        return res
    rc, out, err = dk(c["server"], f"$DK exec {q(c['name'])} sh -c {q(CLS_SH)} sh {q(p)} 2>&1", timeout=40)
    o = text(out)
    if rc != 0 and "@@KIND" not in o:
        raise RemoteError((o.strip() or err.strip() or f"exit {rc}")[-400:] +
                          ("" if "executable file not found" not in o else "  (this image has no shell - type a file path and use Download)"))
    for line in o.splitlines():
        if line.startswith("@@ERR "):
            raise RemoteError(f"{p}: {line[6:]}")
        if line.startswith("@@PATH "):
            res["path"] = line[7:] or p
        elif line.startswith("@@KIND "):
            res["kind"] = line[7:]
        else:
            f = line.split("/", 3)
            if len(f) == 4:
                name = f[3].rstrip("/").split("/")[-1] if res["kind"] == "file" else f[3]
                typ = "dir" if f[0] == "directory" or name.endswith("/") else "link" if f[0] == "symbolic link" else "file"
                name = name.rstrip("/")
                if name in (".", "..", "", "*", ".*"):
                    continue
                try:
                    res["items"].append({"type": typ, "size": int(f[1]), "mtime": int(f[2]), "owner": "", "name": name})
                except ValueError:
                    pass
    res["items"].sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return res


def download_c(handler, server, cname, path, kind, inline=False):
    """Stream a file (or a folder as .tar) out of a container with docker cp - works for stopped containers too."""
    if (server, cname) not in CONTAINERS:
        return handler._send(404, "container not found - rescan", "text/plain")
    path = _cpath(path)
    root = need_docker_root(server)
    mode, stdin, pre = INFO.get(server, {}).get("sudo"), None, ""
    if root:
        if mode == "nopass":
            pre = 'S="sudo -n"; '
        elif mode == "pass" and server in SUDO_PW:
            pre = 'IFS= read -r PW; printf "%s\\n" "$PW" | sudo -S -p "" -v 2>/dev/null; unset PW; S="sudo -n"; '
            stdin = (SUDO_PW[server] + "\n").encode()
        else:
            return handler._send(403, "unlock sudo for this server first", "text/plain")
    cp = f'$S docker cp {q(cname + ":" + path)} -'
    body = pre + (f"exec {cp}" if kind == "dir" else f"{cp} | tar -xOf -")
    proc = subprocess.Popen(SSH + target(server) + ["sh -c " + q(body)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if stdin:
        proc.stdin.write(stdin)
    proc.stdin.close()
    first = proc.stdout.read(65536)
    if not first:
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        proc.wait()
        if proc.returncode != 0:
            err = "\n".join(l for l in err.splitlines() if not l.startswith("tar:"))
            return handler._send(404, f"could not read {path}: {err or 'not found'}", "text/plain; charset=utf-8")
    name = (os.path.basename(path.rstrip("/")) or "root") + (".tar" if kind == "dir" else "")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/plain; charset=utf-8" if inline else "application/octet-stream")
    handler.send_header("Content-Disposition", ("inline" if inline else "attachment") + "; filename*=UTF-8''" + urllib.parse.quote(name))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    try:
        while first:
            handler.wfile.write(first)
            first = proc.stdout.read(65536)
    except (BrokenPipeError, ConnectionError):
        proc.kill()
    proc.wait()


# ---- systemd services ----
UNIT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._:\\-]{0,200}\.service")


def r_svc(d):
    s = d.get("server")
    if s not in servers():
        raise RemoteError("unknown server")
    rc, out, err = srun(s, "systemctl list-units --type=service --all --no-legend --no-pager --plain 2>&1; echo @@FILES; "
                           "systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null", timeout=40, lazy=True)
    o = text(out)
    if "@@FILES" not in o or (rc != 0 and not o.split("@@FILES")[0].strip()):
        raise RemoteError("systemctl not available on this server: " + (o + err).strip()[:300])
    units, files = o.split("@@FILES", 1)
    en = {}
    for l in files.splitlines():
        f = l.split()
        if len(f) >= 2:
            en[f[0]] = f[1]
    items, seen = [], set()
    for l in units.splitlines():
        f = l.split(None, 4)
        if len(f) >= 4 and f[0].endswith(".service"):
            if f[1] == "not-found" and f[2] != "failed":
                continue
            seen.add(f[0])
            items.append({"unit": f[0], "load": f[1], "active": f[2], "sub": f[3], "desc": f[4] if len(f) > 4 else "",
                          "enabled": en.get(f[0], "")})
    for u, e in en.items():   # enabled units that are not loaded (never started)
        if u not in seen and "@" not in u and e == "enabled":
            items.append({"unit": u, "load": "loaded", "active": "inactive", "sub": "dead", "desc": "", "enabled": e})
    return {"items": items, "sudo": INFO.get(s, {}).get("sudo")}


def r_svc_action(d):
    s, u, a = d.get("server"), d.get("unit") or "", d.get("action")
    if s not in servers():
        raise RemoteError("unknown server")
    if not UNIT_RE.fullmatch(u):
        raise RemoteError("bad service name")
    if a in ("start", "stop", "restart", "reload"):
        guard(d, s, f"{a} service {u}")
        cmd = f"$S systemctl {a} {q(u)} 2>&1; r=$?; echo \"now: $(systemctl is-active {q(u)})\"; exit $r"
        lazy = False
    elif a == "status":
        cmd = f"SYSTEMD_COLORS=0 systemctl status {q(u)} --no-pager -l -n 0 2>&1; true"
        lazy = True
    elif a == "logs":
        n = max(20, min(int(d.get("tail", 300)), 10000))
        cmd = f"$S journalctl -u {q(u)} -n {n} --no-pager 2>&1"
        lazy = False     # full journal needs sudo; asks for the password if not unlocked yet
    else:
        raise RemoteError("bad action")
    return out_of(*srun(s, cmd, timeout=120, lazy=lazy))


ROUTES.update({"/api/cls": r_cls, "/api/svc": r_svc, "/api/svc_action": r_svc_action})


def download(handler, server, path, inline=False):
    """Stream a remote file to the browser (sudo used automatically if the file isn't readable)."""
    if INFO.get(server, {}).get("sudo") is None:
        probe(server)
    mode, stdin = INFO.get(server, {}).get("sudo"), None
    if mode == "nopass":
        pre = 'S="sudo -n"; '
    elif mode == "pass" and server in SUDO_PW:
        pre = ('IFS= read -r PW; S="sudo -n"; ')
        stdin = (SUDO_PW[server] + "\n").encode()
    else:
        pre = 'S=; '
    P = _rpath(path)
    cat = (f'P={P}; if [ -r "$P" ]; then exec cat -- "$P"; fi; '
           + ('printf "%s\\n" "$PW" | sudo -S -p "" -v 2>/dev/null; unset PW; ' if stdin else '')
           + 'exec $S cat -- "$P"')
    proc = subprocess.Popen(SSH + target(server) + ["sh -c " + q(pre + cat)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if stdin:
        proc.stdin.write(stdin)
    proc.stdin.close()
    first = proc.stdout.read(65536)
    if not first:
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        proc.wait()
        if proc.returncode != 0:
            return handler._send(404, f"could not read {path}: {err or 'error'}", "text/plain; charset=utf-8")
    name = os.path.basename(path.rstrip("/")) or "file"
    handler.send_response(200)
    handler.send_header("Content-Type", "text/plain; charset=utf-8" if inline else "application/octet-stream")
    handler.send_header("Content-Disposition", ("inline" if inline else "attachment") + "; filename*=UTF-8''" + urllib.parse.quote(name))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    try:
        while first:
            handler.wfile.write(first)
            first = proc.stdout.read(65536)
    except (BrokenPipeError, ConnectionError):
        proc.kill()
    proc.wait()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _host_ok(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _send(self, code, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "forbidden", "text/plain")
        if self.path in ("/", "/index.html"):
            return self._send(200, HTML.replace("__TOKEN__", TOKEN).replace("__NOPTY__", "true" if IS_WIN else "false"), "text/html; charset=utf-8")
        u = urlparse(self.path)
        if u.path in ("/icon.png", "/favicon.ico"):
            try:
                data = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png"), "rb").read()
            except OSError:
                return self._send(404, "no icon", "text/plain")
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "max-age=86400"); self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        if u.path == "/dl":
            qs = parse_qs(u.query)
            if qs.get("t", [""])[0] != TOKEN:
                return self._send(403, "forbidden", "text/plain")
            sv = qs.get("server", [""])[0]
            if sv not in servers():
                return self._send(404, "unknown server", "text/plain")
            try:
                if qs.get("c"):
                    return download_c(self, sv, qs["c"][0], qs.get("path", ["/"])[0], qs.get("k", ["file"])[0],
                                      inline=qs.get("inline", ["0"])[0] == "1")
                return download(self, sv, _clean_path(qs.get("path", [""])[0]), inline=qs.get("inline", ["0"])[0] == "1")
            except RemoteError as e:
                return self._send(400, str(e), "text/plain")
        if u.path == "/ws/term":
            if IS_WIN:
                return self._send(501, "the web terminal needs Linux, macOS or WSL", "text/plain")
            qs = parse_qs(u.query)
            origin = self.headers.get("Origin", "")
            if (qs.get("t", [""])[0] != TOKEN or origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")
                    or self.headers.get("Upgrade", "").lower() != "websocket"):
                return self._send(403, "forbidden", "text/plain")
            server = qs.get("server", [""])[0]
            if server not in servers():
                return self._send(404, "unknown server", "text/plain")
            ctr = qs.get("container", [""])[0] or None
            if ctr and (server, ctr) not in CONTAINERS:
                return self._send(404, "unknown container", "text/plain")
            return terminal(self, server, ctr)
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Dctl") != TOKEN:
            return self._send(403, "forbidden", "text/plain")
        u = urlparse(self.path)
        if u.path == "/upload":
            qs = parse_qs(u.query)
            try:
                tk = UPLOADS.get(qs.get("ticket", [""])[0])
                if not tk or tk["exp"] < time.time():
                    raise RemoteError("upload expired - please try again")
                n = int(self.headers.get("Content-Length", -1))
                if n < 0:
                    raise RemoteError("missing file size")
                res = {"ok": True, **upload(self, tk, _upname(qs.get("name", [""])[0]), qs.get("overwrite", ["0"])[0] == "1", n)}
            except NeedSudo as e:
                res = {"ok": False, "need_sudo": str(e)}
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            self.close_connection = True
            return self._send(200, json.dumps(res), "application/json")
        fn = ROUTES.get(self.path)
        if not fn:
            return self._send(404, "not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length", 0))
            d = json.loads(self.rfile.read(n) or b"{}")
            res = {"ok": True, **fn(d)}
        except NeedSudo as e:
            res = {"ok": False, "need_sudo": str(e)}
        except Protected as e:
            res = {"ok": False, "protected": e.server, "what": e.what}
        except Exception as e:
            res = {"ok": False, "error": str(e)}
        self._send(200, json.dumps(res), "application/json")


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>dctl</title>
<link rel="icon" type="image/png" href="/icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/theme/material-darker.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/yaml/yaml.min.js"></script>
<style>
:root{color-scheme:dark;
  --bg:#06070c;--bg2:#0b0d15;--glass:rgba(20,23,36,.62);--glass2:rgba(30,34,52,.55);--line:rgba(255,255,255,.07);--line2:rgba(255,255,255,.12);
  --fg:#eceef6;--mut:#8a8fa8;--dim:#5d627a;
  --a1:#7c5cff;--a2:#00d4ff;--grad:linear-gradient(135deg,#7c5cff 0%,#4f8bff 50%,#00d4ff 100%);
  --ok:#22e3a1;--bad:#ff4d6d;--warn:#ffb020;
  --sans:Inter,system-ui,-apple-system,Segoe UI,sans-serif;--mono:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
  --r:14px;--shadow:0 10px 40px -10px rgba(0,0,0,.6);
}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:13.5px/1.5 var(--sans);overflow:hidden;display:flex;flex-direction:column;-webkit-font-smoothing:antialiased}
::selection{background:rgba(124,92,255,.4)}
::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-thumb{background:rgba(255,255,255,.08);border-radius:10px;border:2px solid transparent;background-clip:padding-box}
::-webkit-scrollbar-thumb:hover{background-color:rgba(255,255,255,.16)}::-webkit-scrollbar-track{background:transparent}

/* animated background */
.bg{position:fixed;inset:0;z-index:-1;overflow:hidden;background:radial-gradient(1200px 600px at 50% -10%,#141833 0%,transparent 60%),var(--bg)}
.bg::after{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);background-size:44px 44px;mask-image:radial-gradient(ellipse at 50% 0%,#000 30%,transparent 75%)}
.bg i{position:absolute;border-radius:50%;filter:blur(90px);opacity:.35;animation:float 22s ease-in-out infinite}
.bg i:nth-child(1){width:520px;height:520px;background:#7c5cff;top:-160px;left:-120px}
.bg i:nth-child(2){width:460px;height:460px;background:#00d4ff;top:20%;right:-160px;animation-delay:-7s;opacity:.22}
.bg i:nth-child(3){width:420px;height:420px;background:#ff3d9a;bottom:-200px;left:30%;animation-delay:-14s;opacity:.14}
@keyframes float{0%,100%{transform:translate(0,0) scale(1)}33%{transform:translate(60px,40px) scale(1.08)}66%{transform:translate(-40px,30px) scale(.95)}}

/* generic */
.hidden{display:none!important}.grow{flex:1}.mut{color:var(--mut)}.dim{color:var(--dim)}.mono{font-family:var(--mono)}.small{font-size:11.5px}
.ic{flex:none;vertical-align:-3px}
button,input,select{font:inherit;color:inherit}
.btn{display:inline-flex;align-items:center;gap:7px;padding:8px 14px;border-radius:10px;border:1px solid var(--line2);background:var(--glass2);cursor:pointer;font-weight:500;transition:.18s;white-space:nowrap}
.btn:hover:not(:disabled){border-color:rgba(124,92,255,.6);background:rgba(124,92,255,.12);transform:translateY(-1px)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn.primary{background:var(--grad);border-color:transparent;color:#fff;box-shadow:0 6px 24px -6px rgba(92,120,255,.7)}
.btn.primary:hover:not(:disabled){filter:brightness(1.1);box-shadow:0 8px 30px -6px rgba(92,120,255,.9)}
.btn.ghost{background:transparent}
#mrm{color:#ff8aa0;border-color:rgba(255,77,109,.45)}
.btn.danger:hover:not(:disabled){border-color:var(--bad);background:rgba(255,77,109,.12);color:#ff8aa0}
.icon-btn{display:inline-grid;place-items:center;width:34px;height:34px;border-radius:10px;border:1px solid var(--line);background:var(--glass2);cursor:pointer;color:var(--mut);transition:.15s;flex:none}
.icon-btn:hover{color:var(--fg);border-color:var(--line2)}
input[type=text],input[type=password],input:not([type]),select{background:rgba(8,10,18,.7);border:1px solid var(--line2);border-radius:10px;padding:8px 12px;outline:none;transition:.15s}
input:focus,select:focus{border-color:var(--a1);box-shadow:0 0 0 3px rgba(124,92,255,.2)}
select{-webkit-appearance:none;appearance:none;color:var(--fg);padding-right:34px;cursor:pointer;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238a8fa8' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 12px center}
select option{background:#141724;color:var(--fg)}
input[type=checkbox]{-webkit-appearance:none;appearance:none;width:16px;height:16px;flex:none;margin:0;padding:0;border:1.5px solid rgba(255,255,255,.28);border-radius:5px;background:rgba(8,10,18,.7);cursor:pointer;display:inline-grid;place-content:center;vertical-align:-3px;transition:.15s}
input[type=checkbox]:hover{border-color:var(--a1)}
input[type=checkbox]:checked{background:var(--grad);border-color:transparent}
input[type=checkbox]:checked::after{content:"";width:4px;height:8px;border:solid #fff;border-width:0 2px 2px 0;transform:translateY(-1px) rotate(45deg)}
input[type=checkbox]:indeterminate{background:var(--grad);border-color:transparent}
input[type=checkbox]:indeterminate::after{content:"";width:8px;height:2px;background:#fff}
input[type=checkbox]:disabled{opacity:.35;cursor:not-allowed}
kbd{font:500 10.5px var(--mono);padding:2px 6px;border-radius:5px;border:1px solid var(--line2);background:rgba(255,255,255,.04);color:var(--mut)}
.spin{display:inline-block;width:12px;height:12px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:rot .7s linear infinite;vertical-align:-2px}
@keyframes rot{to{transform:rotate(360deg)}}
.gt{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}

/* top bar */
.top{display:flex;align-items:center;gap:16px;padding:12px 22px;border-bottom:1px solid var(--line);background:rgba(8,9,15,.55);backdrop-filter:blur(16px);position:relative;z-index:5}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:17px;letter-spacing:.3px;cursor:pointer;user-select:none}
.logo-img{width:34px;height:34px;margin:-2px 0;filter:drop-shadow(0 4px 12px rgba(92,120,255,.45))}
.logo-ico{width:32px;height:32px;border-radius:9px;background:var(--grad);display:grid;place-items:center;color:#fff;box-shadow:0 4px 18px -4px rgba(92,120,255,.8)}
#crumb{display:flex;align-items:center;gap:8px;color:var(--mut);font-weight:500}
#crumb a{cursor:pointer;color:var(--mut)}#crumb a:hover{color:var(--fg)}#crumb b{color:var(--fg)}
.kbtn{display:flex;align-items:center;gap:10px;padding:7px 10px 7px 12px;border-radius:10px;border:1px solid var(--line2);background:rgba(8,10,18,.6);color:var(--mut);cursor:pointer;min-width:260px;transition:.15s}
.kbtn:hover{border-color:rgba(124,92,255,.6);color:var(--fg)}.kbtn span{flex:1;text-align:left;white-space:nowrap}

/* views */
.view{flex:1;overflow:auto;min-height:0}
#home{padding:34px clamp(16px,4vw,48px) 60px}
.hero{display:flex;align-items:flex-end;gap:20px;flex-wrap:wrap;margin-bottom:26px}
.eyebrow{display:inline-flex;align-items:center;gap:8px;font:600 11px var(--sans);letter-spacing:1.6px;text-transform:uppercase;color:#a99bff;padding:5px 10px;border-radius:20px;background:rgba(124,92,255,.12);border:1px solid rgba(124,92,255,.25)}
.eyebrow .live{width:6px;height:6px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 0 rgba(34,227,161,.7);animation:pulse 1.8s infinite}
.hero h1{font-size:clamp(28px,3.4vw,42px);line-height:1.1;margin:14px 0 8px;font-weight:800;letter-spacing:-1px}
.hero p{margin:0}
.hero-actions{display:flex;gap:10px;margin-left:auto}

.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin-bottom:34px}
.kpi{position:relative;overflow:hidden;padding:18px 20px;border-radius:var(--r);background:var(--glass);border:1px solid var(--line);backdrop-filter:blur(12px);animation:rise .6s both}
.kpi:nth-child(2){animation-delay:.06s}.kpi:nth-child(3){animation-delay:.12s}.kpi:nth-child(4){animation-delay:.18s}
.kpi-top{display:flex;align-items:center;justify-content:space-between;color:var(--mut);font-weight:500}
.kpi-ico{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:rgba(255,255,255,.05);color:var(--c)}
.kpi-v{font-size:34px;font-weight:800;letter-spacing:-1px;margin-top:10px;line-height:1}
.kpi-v small{font-size:14px;color:var(--mut);font-weight:500;margin-left:6px;letter-spacing:0}
.kpi-s{color:var(--dim);font-size:12px;margin-top:6px}
.kpi::after{content:"";position:absolute;right:-40px;top:-40px;width:140px;height:140px;border-radius:50%;background:var(--c);opacity:.12;filter:blur(30px)}
.kpi .spark{position:absolute;left:0;right:0;bottom:0;height:3px;background:linear-gradient(90deg,transparent,var(--c),transparent);opacity:.7}
.tone-acc{--c:#8f7bff}.tone-ok{--c:var(--ok)}.tone-bad{--c:var(--bad)}.tone-cy{--c:var(--a2)}

.sechead{display:flex;align-items:baseline;gap:14px;margin:0 0 14px;flex-wrap:wrap}.sechead h2{margin:0;font-size:18px;font-weight:700}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:16px}
.scard{position:relative;border-radius:var(--r);padding:18px;background:var(--glass);border:1px solid var(--line);backdrop-filter:blur(12px);cursor:pointer;
  transition:transform .22s,border-color .22s,box-shadow .22s;animation:rise .55s both;animation-delay:var(--d,0ms);display:flex;flex-direction:column;gap:14px;min-height:220px}
.scard::before{content:"";position:absolute;inset:-1px;border-radius:inherit;padding:1px;background:var(--grad);-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;opacity:0;transition:.25s}
.scard:hover{transform:translateY(-4px);box-shadow:0 20px 50px -18px rgba(92,120,255,.45)}
.scard:hover::before{opacity:1}
.scard.off{opacity:.72}.scard.off:hover{box-shadow:0 20px 50px -18px rgba(255,77,109,.35)}.scard.off::before{background:linear-gradient(135deg,#ff4d6d,#ff9a4d)}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.sc-top{display:flex;align-items:flex-start;gap:12px}
.sc-name{font-weight:700;font-size:15.5px;word-break:break-all}
.sc-host{font:12px var(--mono);color:var(--mut);word-break:break-all;margin-top:2px}
.pulse{width:10px;height:10px;border-radius:50%;margin-top:6px;flex:none;background:var(--c,var(--mut));box-shadow:0 0 0 0 var(--c);animation:pulse 2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--c,#22e3a1) 70%,transparent)}70%{box-shadow:0 0 0 9px transparent}100%{box-shadow:0 0 0 0 transparent}}
.st-ok{--c:var(--ok)}.st-sudo{--c:#5fa8ff}.st-need_password{--c:var(--warn)}.st-unreachable,.st-no_access,.st-error{--c:var(--bad)}.st-unknown{--c:var(--dim)}
.tag{display:inline-flex;align-items:center;gap:4px;font:600 10.5px var(--sans);letter-spacing:.6px;text-transform:uppercase;padding:4px 8px;border-radius:20px;color:var(--c);background:color-mix(in srgb,var(--c) 14%,transparent);border:1px solid color-mix(in srgb,var(--c) 30%,transparent);flex:none}
.scard .x{width:30px;height:30px;opacity:.75;transition:.15s;flex:none}.scard:hover .x{opacity:1}.scard .x:hover{color:var(--fg);border-color:#7c5cff;background:rgba(124,92,255,.15)}
.rings{display:flex;justify-content:space-between;gap:8px}
.ring{position:relative;width:76px;text-align:center}
.ring svg{width:64px;height:64px;transform:rotate(-90deg);display:block;margin:0 auto}
.ring .trk{fill:none;stroke:rgba(255,255,255,.07);stroke-width:5}
.ring .val{fill:none;stroke-width:5;stroke-linecap:round;animation:draw 1.2s cubic-bezier(.2,.8,.2,1) both;filter:drop-shadow(0 0 6px currentColor)}
@keyframes draw{from{stroke-dashoffset:var(--c0)}}
.ring b{position:absolute;top:21px;left:0;right:0;font-size:15px;font-weight:700}.ring b small{font-size:9px;color:var(--mut);font-weight:500}
.ring span{display:block;font:600 10px var(--sans);letter-spacing:1.2px;color:var(--mut);margin-top:4px}
.cbar{height:6px;border-radius:6px;background:rgba(255,77,109,.35);overflow:hidden}
.cbar i{display:block;height:100%;background:linear-gradient(90deg,#16c88c,#22e3a1);border-radius:6px;box-shadow:0 0 12px rgba(34,227,161,.6);animation:grow 1.1s cubic-bezier(.2,.8,.2,1) both}
.cbar.none{background:rgba(255,255,255,.06)}
@keyframes grow{from{width:0}}
.sc-foot{display:flex;gap:14px;color:var(--mut);font-size:12.5px;margin-top:-6px}.sc-foot b{color:var(--fg)}
.sc-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto}
.pill{font:500 11px var(--mono);padding:3px 8px;border-radius:7px;background:rgba(255,255,255,.04);border:1px solid var(--line);color:var(--mut)}
.sc-err{font:12px var(--mono);color:#ff8aa0;background:rgba(255,77,109,.08);border:1px solid rgba(255,77,109,.2);padding:10px 12px;border-radius:10px;word-break:break-word}
.scard.all{background:linear-gradient(160deg,rgba(124,92,255,.18),rgba(0,212,255,.06) 70%),var(--glass)}
.scard.add{border:1.5px dashed rgba(124,92,255,.4);background:transparent;align-items:center;justify-content:center;color:#b3a6ff;gap:10px}
.scard.add:hover{background:rgba(124,92,255,.06)}
.scard.add.mini{min-height:150px}
.ptabs{display:flex;gap:4px;padding:3px;border-radius:11px;background:rgba(8,10,18,.6);border:1px solid var(--line2);margin-left:6px}
.ptabs a{display:flex;align-items:center;gap:7px;padding:6px 14px;border-radius:8px;cursor:pointer;color:var(--mut);font-weight:600;font-size:13px;transition:.15s}
.ptabs a:hover{color:var(--fg)}.ptabs a.on{background:linear-gradient(135deg,rgba(124,92,255,.35),rgba(34,211,238,.18));color:var(--fg);box-shadow:inset 0 0 0 1px rgba(124,92,255,.45)}
.ptabs .mi{display:inline-flex}
.gsec{margin-bottom:26px}
.gsh{display:flex;align-items:center;gap:10px;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.gsh b{font-size:15px}.gsh .gacts{display:flex;gap:6px;opacity:.55;transition:.15s}.gsh:hover .gacts{opacity:1}
.icon-btn.sm{width:28px;height:28px}.icon-btn.x2:hover{color:var(--bad);border-color:var(--bad)}
.gsh .chev{display:inline-flex;transition:transform .15s}.gsh .chev.open{transform:rotate(90deg)}
.msep{height:1px;background:var(--line);margin:14px 0}
.pick{display:flex;flex-direction:column;gap:6px;max-height:260px;overflow:auto;margin-top:4px}
.form .pk,.pk{display:flex!important;flex-direction:row!important;align-items:center;gap:12px;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:rgba(255,255,255,.02);cursor:pointer;margin:0!important;text-transform:none!important;letter-spacing:0!important}
.pk:hover{border-color:var(--line2);background:rgba(124,92,255,.06)}.form .pk input,.pk input{width:18px!important;height:18px;flex:none;margin:0;padding:0;accent-color:#7c5cff}
.pk div{display:flex;flex-direction:column;flex:1;min-width:0}.pk b{color:var(--fg);font-size:13.5px}.pk span.mono{font-size:11.5px;color:var(--mut)}
@media (max-width:640px){.ptabs a{padding:6px 10px}.hero-actions{flex-wrap:wrap}}
.addic{width:52px;height:52px;border-radius:16px;display:grid;place-items:center;background:rgba(124,92,255,.14);border:1px solid rgba(124,92,255,.35)}
.bigic{width:44px;height:44px;border-radius:13px;display:grid;place-items:center;background:var(--grad);color:#fff;box-shadow:0 8px 24px -8px rgba(92,120,255,.9)}

/* skeleton */
.sk{background:linear-gradient(90deg,rgba(255,255,255,.04) 25%,rgba(255,255,255,.09) 37%,rgba(255,255,255,.04) 63%);background-size:400% 100%;animation:sh 1.4s ease infinite;border-radius:var(--r)}
@keyframes sh{0%{background-position:100% 50%}100%{background-position:0 50%}}

/* list view */
#listv{display:flex;flex-direction:column;overflow:hidden}
.lhead{display:flex;align-items:center;gap:16px;padding:22px clamp(16px,3vw,32px) 8px;flex-wrap:wrap}
.lhead h2{margin:0;font-size:24px;font-weight:800;letter-spacing:-.5px;display:flex;align-items:center;gap:10px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{display:flex;align-items:center;gap:8px;padding:7px 12px;border-radius:10px;background:var(--glass);border:1px solid var(--line);font-size:12px;color:var(--mut)}
.chip b{color:var(--fg);font-weight:600}
.mbar{width:46px;height:5px;border-radius:5px;background:rgba(255,255,255,.08);overflow:hidden}.mbar i{display:block;height:100%;border-radius:5px}
.toolbar{display:flex;align-items:center;gap:10px;padding:10px clamp(16px,3vw,32px) 14px;flex-wrap:wrap}
.search{display:flex;align-items:center;gap:8px;flex:1;min-width:240px;padding:0 12px;border-radius:10px;border:1px solid var(--line2);background:rgba(8,10,18,.6);color:var(--mut);transition:.15s}
.search:focus-within{border-color:var(--a1);box-shadow:0 0 0 3px rgba(124,92,255,.2)}
.search input{flex:1;border:0;background:none;padding:9px 0;box-shadow:none!important}
.seg{display:flex;padding:3px;border-radius:11px;background:rgba(8,10,18,.6);border:1px solid var(--line2)}
.seg button{border:0;background:none;padding:6px 12px;border-radius:8px;cursor:pointer;color:var(--mut);font-weight:500;display:flex;gap:6px;align-items:center}
.seg button.on{background:var(--glass2);color:var(--fg);box-shadow:inset 0 0 0 1px var(--line2)}
.seg button span{font:500 10.5px var(--mono);padding:1px 6px;border-radius:6px;background:rgba(255,255,255,.06)}
.toggle{display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--mut);user-select:none;font-weight:500}
.toggle input{display:none}
.toggle span{width:34px;height:20px;border-radius:20px;background:rgba(255,255,255,.1);position:relative;transition:.2s;flex:none}
.toggle span::after{content:"";position:absolute;top:3px;left:3px;width:14px;height:14px;border-radius:50%;background:#fff;transition:.2s}
.toggle input:checked+span{background:var(--grad)}.toggle input:checked+span::after{left:17px}
.toggle:has(input:checked){color:var(--fg)}
.lwrap{flex:1;overflow:auto;padding:0 clamp(16px,3vw,32px) 40px}
.grp{margin-bottom:14px;border-radius:var(--r);background:var(--glass);border:1px solid var(--line);backdrop-filter:blur(12px);overflow:hidden}.grp.anim{animation:rise .4s both}
.ghead{display:flex;align-items:center;gap:10px;padding:11px 16px;cursor:pointer;user-select:none;background:rgba(255,255,255,.015);border-bottom:1px solid var(--line)}
.ghead:hover{background:rgba(255,255,255,.035)}
.ghead b{font-weight:650}
.chev{transition:transform .2s;color:var(--mut);display:inline-block}.chev.open{transform:rotate(90deg)}
.gcount{display:flex;align-items:center;gap:6px;font:500 12px var(--mono);color:var(--mut)}
.gico{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;background:rgba(124,92,255,.14);color:#b3a6ff}
.crow{display:grid;grid-template-columns:118px minmax(220px,2.2fr) minmax(120px,1fr) 130px minmax(170px,1.1fr);gap:14px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);cursor:pointer;transition:background .12s;position:relative}
.crow:last-child{border-bottom:0}
.crow:hover{background:rgba(124,92,255,.07)}
.crow.sel{background:rgba(124,92,255,.13)}.crow.sel::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--grad)}
.c-name{min-width:0}.c-name b{display:block;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.c-name span{display:block;font:11.5px var(--mono);color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.c-up{font-size:12px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.spill{display:inline-flex;align-items:center;gap:7px;padding:4px 10px 4px 8px;border-radius:20px;font:600 11px var(--sans);letter-spacing:.3px}
.spill i{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 8px currentColor}
.spill.run{color:var(--ok);background:rgba(34,227,161,.1);border:1px solid rgba(34,227,161,.22)}
.spill.run i{animation:blink 2.4s infinite}
.spill.stop{color:#ff7d93;background:rgba(255,77,109,.1);border:1px solid rgba(255,77,109,.22)}
@keyframes blink{50%{opacity:.35}}
.hb{font-size:10.5px;padding:1px 6px;border-radius:5px;margin-left:6px}
.hb.ok{color:var(--ok);background:rgba(34,227,161,.12)}.hb.bad{color:var(--bad);background:rgba(255,77,109,.14)}
.ports{display:flex;gap:5px;flex-wrap:wrap}
.port{font:500 11px var(--mono);padding:2px 7px;border-radius:6px;background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.2);color:#7fe6ff}
.bars{display:flex;flex-direction:column;gap:5px}
.bar{display:grid;grid-template-columns:30px 1fr 52px;gap:8px;align-items:center;font:500 10.5px var(--mono);color:var(--mut)}
.bar .t{height:5px;border-radius:5px;background:rgba(255,255,255,.07);overflow:hidden}
.bar .t i{display:block;height:100%;border-radius:5px;transition:width .6s cubic-bezier(.2,.8,.2,1)}
.bar em{font-style:normal;text-align:right;color:var(--fg)}
.empty{padding:60px 20px;text-align:center;color:var(--mut)}
#mseg .mi{display:inline-flex}#mseg .mi svg{width:14px;height:14px}
.urole{font:600 10px var(--sans);letter-spacing:.5px;text-transform:uppercase;padding:2px 7px;border-radius:6px;background:rgba(124,92,255,.14);color:#b9a9ff;border:1px solid rgba(124,92,255,.3)}
.urole.me{background:rgba(34,227,161,.1);color:var(--ok);border-color:rgba(34,227,161,.25)}
.urole.root{background:rgba(255,159,26,.12);color:var(--warn);border-color:rgba(255,159,26,.3)}
.crow.dk{grid-template-columns:22px 118px minmax(220px,2.2fr) minmax(120px,1fr) 130px minmax(170px,1.1fr)}
.crow.picked{background:rgba(157,139,255,.07)}
.ck{width:16px;height:16px;accent-color:#9d8bff;cursor:pointer;margin:0}
.ghead .ck{margin-right:2px}
#bulkbar{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:40;display:flex;align-items:center;gap:8px;padding:9px 12px 9px 16px;border-radius:14px;
  background:rgba(18,20,34,.96);border:1px solid var(--line2);box-shadow:0 18px 50px rgba(0,0,0,.5);max-width:calc(100vw - 32px);flex-wrap:wrap}
#bulkbar b{margin-right:6px}
.dtabs{display:flex;gap:2px;padding:0 clamp(16px,3vw,32px);border-bottom:1px solid var(--line);overflow-x:auto}
.dtabs a{padding:9px 14px;cursor:pointer;color:var(--mut);font-weight:550;display:flex;align-items:center;gap:7px;border-bottom:2px solid transparent;margin-bottom:-1px;white-space:nowrap}
.dtabs a:hover{color:var(--fg)}.dtabs a.on{color:var(--fg);border-bottom-color:#9d8bff}
.dtabs a span{font:500 10.5px var(--mono);padding:1px 6px;border-radius:6px;background:rgba(255,255,255,.06)}
.rsum{display:flex;align-items:center;gap:14px;padding:10px 16px;border-bottom:1px solid var(--line);color:var(--mut);font-size:12.5px;flex-wrap:wrap}
.rrow{display:grid;grid-template-columns:22px minmax(200px,2fr) 120px 90px minmax(160px,1.6fr) 96px;gap:14px;align-items:center;padding:9px 16px;border-bottom:1px solid var(--line);font-size:13px}
.rrow.h{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:7px 16px;background:rgba(255,255,255,.015)}
.rrow .nm{min-width:0}.rrow .nm b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}.rrow .nm span{display:block;color:var(--mut);font:500 11px var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.uchips{display:flex;gap:5px;flex-wrap:wrap;min-width:0}.uchip{font:500 11px var(--mono);padding:2px 7px;border-radius:6px;background:rgba(255,255,255,.05);border:1px solid var(--line);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.uchip.run{border-color:rgba(34,227,161,.35);color:var(--ok)}
.tag{font-size:10.5px;padding:2px 7px;border-radius:6px;font-weight:600}.tag.free{color:var(--warn);background:rgba(255,184,77,.12)}.tag.use{color:var(--ok);background:rgba(34,227,161,.1)}.tag.mut{color:var(--mut);background:rgba(255,255,255,.05)}
@media (max-width:800px){.rrow{grid-template-columns:22px 1fr auto}.rrow>*:nth-child(3),.rrow>*:nth-child(4),.rrow>*:nth-child(5){display:none}}
.rrow.picked{background:rgba(157,139,255,.07)}.ck:disabled{opacity:.3;cursor:not-allowed}
.svrow{display:grid;grid-template-columns:14px minmax(180px,1.3fr) minmax(120px,2fr) 84px 230px;gap:12px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line);font-size:13px;cursor:pointer}
.svrow:hover{background:rgba(255,255,255,.03)}.svrow.on{background:rgba(157,139,255,.08)}
.svrow .d{width:9px;height:9px;border-radius:50%;background:var(--mut)}.svrow .d.active{background:var(--ok)}.svrow .d.failed{background:var(--bad)}.svrow .d.activating,.svrow .d.reloading{background:var(--warn)}
.svrow b{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.svrow .ds{color:var(--mut);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.svacts{display:flex;gap:6px;justify-content:flex-end}
@media (max-width:800px){.svrow{grid-template-columns:14px 1fr auto}.svrow>*:nth-child(3),.svrow>*:nth-child(4){display:none}}
mark{background:rgba(255,214,10,.35);color:inherit;border-radius:2px}
.lockrow{display:flex;align-items:center;gap:12px;padding:16px;color:var(--mut);flex-wrap:wrap}
.lockrow svg{color:var(--warn)}
.spill.err{color:var(--warn);background:rgba(255,159,26,.1);border:1px solid rgba(255,159,26,.25)}
.hidnote{display:flex;align-items:center;gap:8px;padding:12px 16px;color:var(--mut);font-size:13px}.hidnote a{color:#b3a6ff;cursor:pointer;font-weight:600}.hidnote a:hover{text-decoration:underline}
#termp{position:fixed;inset:0;z-index:60;background:rgba(3,4,8,.55);backdrop-filter:blur(3px);display:flex;align-items:flex-end;justify-content:center;padding:70px 24px 24px}
#termp.hidden{display:none}
.tsg{position:absolute;pointer-events:none;white-space:pre;overflow:hidden;color:#5c6378;z-index:5;display:none}
.tauto{font-size:12.5px;margin:0 6px;white-space:nowrap}.tauto span{transform:scale(.85)}
.tpbox{width:min(1400px,100%);height:min(78vh,900px);display:flex;flex-direction:column;border-radius:14px;overflow:hidden;border:1px solid var(--line2);background:#05060a;box-shadow:0 30px 80px -20px rgba(0,0,0,.8),0 0 0 1px rgba(124,92,255,.15);animation:rise .25s both}
#termp.full{padding:0}#termp.full .tpbox{width:100%;height:100%;border-radius:0}
.tphead{display:flex;align-items:center;gap:8px;padding:8px 10px;background:rgba(255,255,255,.03);border-bottom:1px solid var(--line)}
#ttabs{display:flex;gap:4px;overflow-x:auto;min-width:0}
.ttab{display:flex;align-items:center;gap:8px;padding:6px 8px 6px 12px;border-radius:8px;cursor:pointer;color:var(--mut);font:500 12.5px var(--mono);white-space:nowrap;border:1px solid transparent}
.ttab:hover{color:var(--fg)}.ttab.on{background:rgba(124,92,255,.14);color:var(--fg);border-color:rgba(124,92,255,.35)}
.ttab .tx{display:inline-flex;padding:2px;border-radius:5px;opacity:.6}.ttab .tx:hover{opacity:1;background:rgba(255,77,109,.2);color:#ff8aa0}
.tdot{width:7px;height:7px;border-radius:50%;background:var(--warn)}.tdot.open{background:var(--ok);box-shadow:0 0 8px var(--ok)}.tdot.closed{background:var(--bad)}
.btn.sm{padding:5px 10px;font-size:12px}
#tbodies{flex:1;position:relative;min-height:0}
.tbody{position:absolute;inset:0;padding:8px 4px 4px 10px;display:none}.tbody.on{display:block}
.tbody .xterm{height:100%}.tbody .xterm-viewport{background:#05060a!important}
#termbtn i{display:inline-flex}
.box.xl{width:min(1180px,96vw);max-height:92vh;overflow:auto}
.dkdfs{display:flex;flex-direction:column;gap:8px;margin:10px 0 14px}
.dkdf{display:grid;grid-template-columns:140px 1fr auto;gap:12px;align-items:center;font-size:12.5px}
.dkdf .t{height:8px;border-radius:5px;background:rgba(255,255,255,.06);overflow:hidden}.dkdf .t i{display:block;height:100%;border-radius:5px}
.dkgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:12px}
.dksec{border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:rgba(255,255,255,.015)}
.dksec h4{margin:0 0 8px;font-size:13px;display:flex;gap:8px;align-items:center;color:#b9a9ff}
.dkrow{display:flex;align-items:center;gap:10px;padding:7px 0;border-top:1px solid var(--line);font-size:13px}.dkrow:first-of-type{border-top:0}
.dkrow b.mono{font-size:12px;white-space:nowrap}
.rwrap{display:grid;grid-template-columns:240px 1fr;gap:16px;margin-top:10px}
.rleft{border-right:1px solid var(--line);padding-right:12px;max-height:64vh;overflow:auto}
.rg{margin-bottom:10px}.rgh{display:flex;gap:7px;align-items:center;font-size:11.5px;font-weight:600;color:var(--mut);text-transform:uppercase;letter-spacing:.5px;padding:4px 0;cursor:pointer}
.rs{display:flex;gap:8px;align-items:center;padding:5px 6px 5px 18px;border-radius:7px;cursor:pointer;font:500 12.5px var(--mono)}.rs:hover{background:rgba(124,92,255,.08)}
.rg input,.rs input{accent-color:#7c5cff;width:auto}
.rright{display:flex;flex-direction:column;gap:10px;min-width:0}
.rcmds{display:flex;flex-wrap:wrap;gap:6px}
.rc{display:inline-flex;align-items:center;gap:6px;padding:5px 6px 5px 10px;border-radius:8px;border:1px solid var(--line2);background:rgba(255,255,255,.03);font-size:12px;cursor:pointer}
.rc:hover{border-color:rgba(124,92,255,.6);background:rgba(124,92,255,.1)}.rc i{display:inline-flex;opacity:.4;padding:2px;border-radius:4px}.rc i:hover{opacity:1;color:#ff8aa0}
#rcmd{min-height:84px;font:13px/1.5 var(--mono);resize:vertical;width:100%;background:#05060a;color:var(--fg);border:1px solid var(--line2);border-radius:10px;padding:10px 12px;outline:none}#rcmd:focus{border-color:rgba(124,92,255,.6)}
.rsum{display:flex;gap:12px;align-items:center;font-size:12.5px;padding:6px 2px}
.rr{border:1px solid var(--line);border-radius:10px;margin-top:8px;background:rgba(255,255,255,.015)}
.rr summary{display:flex;gap:10px;align-items:center;padding:9px 12px;cursor:pointer;list-style:none;font-size:13px}
.rr summary::-webkit-details-marker{display:none}.rr .term{margin:0 10px 10px;max-height:340px}
.palnote{padding:8px 14px;color:var(--mut);font-size:12px;display:flex;gap:8px;align-items:center}
@media (max-width:800px){.rwrap{grid-template-columns:1fr}.rleft{border-right:0;max-height:200px}.dkgrid{grid-template-columns:1fr}}
.fblist{margin-top:10px;border:1px solid var(--line);border-radius:12px;max-height:58vh;overflow-y:auto;overflow-x:hidden}.fbown{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fbrow{display:grid;grid-template-columns:22px minmax(140px,1fr) 64px 70px 100px 300px;gap:12px;align-items:center;padding:7px 12px;border-bottom:1px solid var(--line);cursor:pointer;font-size:13px}
.fbrow:hover{background:rgba(124,92,255,.07)}.fbrow:last-child{border-bottom:0}
.fbrow.dir .fbic{color:#b9a9ff}.fbrow.file .fbic{color:var(--mut)}.fbrow .fbic{display:inline-flex}
.fbname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:12.5px}.fbrow.dir .fbname{font-weight:600;color:var(--fg)}
.fbacts{display:flex;gap:6px;justify-content:flex-end;opacity:.55}.fbrow:hover .fbacts{opacity:1}
@media (max-width:800px){.fbrow{grid-template-columns:22px 1fr auto}.fbrow>span:nth-child(3),.fbrow>span:nth-child(4),.fbrow>span:nth-child(5){display:none}}
.memtxt{font:500 11.5px var(--mono);color:var(--mut)}
.empty .bigic{margin:0 auto 14px}

/* drawer */
#scrim{position:fixed;inset:0;background:rgba(3,4,8,.35);z-index:19;animation:fade .2s}
#detail{position:fixed;top:0;right:0;bottom:0;width:min(860px,100vw);z-index:20;background:rgba(12,14,22,.92);backdrop-filter:blur(22px);border-left:1px solid var(--line2);
  box-shadow:-30px 0 80px -20px rgba(0,0,0,.8);display:flex;flex-direction:column;animation:slide .32s cubic-bezier(.2,.8,.2,1)}
@keyframes slide{from{transform:translateX(40px);opacity:0}}
@keyframes fade{from{opacity:0}}
.dr-head{display:flex;gap:14px;align-items:flex-start;padding:22px 24px 12px}
.dr-ico{width:48px;height:48px;border-radius:14px;display:grid;place-items:center;flex:none}
.dr-ico.run{background:rgba(34,227,161,.12);color:var(--ok);box-shadow:0 0 30px -6px rgba(34,227,161,.5)}
.dr-ico.stop{background:rgba(255,77,109,.12);color:var(--bad)}
.dr-head h2{margin:0 0 6px;font-size:19px;font-weight:750;word-break:break-all;letter-spacing:-.3px}
.dr-sub{display:flex;gap:12px;flex-wrap:wrap;align-items:center;color:var(--mut);font-size:12.5px}
.dr-sub>span{display:inline-flex;align-items:center;gap:5px}
.dr-actions{display:flex;gap:8px;flex-wrap:wrap;padding:6px 24px 12px}
.act{display:flex;align-items:center;gap:7px;padding:8px 12px;border-radius:10px;border:1px solid var(--line2);background:var(--glass2);cursor:pointer;font-weight:500;transition:.15s}
.act:hover:not(:disabled){transform:translateY(-1px);border-color:rgba(124,92,255,.6);background:rgba(124,92,255,.12)}
.act:disabled{opacity:.35;cursor:not-allowed}
.act.danger:hover:not(:disabled){border-color:var(--bad);background:rgba(255,77,109,.12);color:#ff8aa0}
.act.accent{background:var(--grad);border-color:transparent;color:#fff;box-shadow:0 6px 20px -8px rgba(92,120,255,.9)}
.act.accent:hover:not(:disabled){filter:brightness(1.12);background:var(--grad)}
.vsep{width:1px;background:var(--line2);margin:4px 4px}
.dgauges{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:0 24px 12px}
.g{padding:12px 14px;border-radius:12px;background:var(--glass);border:1px solid var(--line)}
.g .l{font:600 10px var(--sans);letter-spacing:1.2px;color:var(--mut);text-transform:uppercase}
.g .v{font-size:20px;font-weight:750;margin-top:4px;letter-spacing:-.4px}.g .v small{font-size:11px;color:var(--mut);font-weight:500;margin-left:4px}
.g .t{height:4px;border-radius:4px;background:rgba(255,255,255,.07);margin-top:8px;overflow:hidden}.g .t i{display:block;height:100%;border-radius:4px;transition:width .6s}
.tabs{display:flex;gap:4px;padding:0 24px;border-bottom:1px solid var(--line);position:relative}
.tabs a{padding:10px 14px;cursor:pointer;color:var(--mut);font-weight:550;display:flex;align-items:center;gap:7px;transition:color .15s}
.tabs a:hover{color:var(--fg)}.tabs a.on{color:var(--fg)}
.ink{position:absolute;bottom:-1px;height:2px;background:var(--grad);border-radius:2px;transition:left .25s cubic-bezier(.2,.8,.2,1),width .25s;box-shadow:0 0 12px rgba(92,120,255,.8)}
#tabbody{flex:1;overflow:auto;padding:16px 24px 24px;display:flex;flex-direction:column;gap:12px;min-height:0}
.term{margin:0;font:12px/1.6 var(--mono);background:#05060a;border:1px solid var(--line);border-radius:12px;padding:14px 16px;overflow:auto;white-space:pre-wrap;word-break:break-all;color:#c9cde0;box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}
.term.chrome{padding-top:38px;position:relative}
.term.chrome::before{content:"";position:absolute;top:13px;left:14px;width:10px;height:10px;border-radius:50%;background:#ff5f57;box-shadow:16px 0 #febc2e,32px 0 #28c840}
#out{margin:0 24px 12px;max-height:240px;flex:none}
.term .e{color:#ff7d93}.term .w{color:#ffcc66}.term .ts{color:#596080}.term .ok{color:var(--ok)}.term .p{color:#9d8bff}
.kv{display:grid;grid-template-columns:140px 1fr;gap:0;border-radius:12px;overflow:hidden;border:1px solid var(--line)}
.kv>div{padding:10px 14px;border-bottom:1px solid var(--line)}.kv>div:nth-child(odd){color:var(--mut);background:rgba(255,255,255,.02)}
.kv>div:nth-last-child(-n+2){border-bottom:0}
.kv code,code{font:12px var(--mono);word-break:break-all}
.copy{cursor:pointer;margin-left:8px;color:var(--dim)}.copy:hover{color:var(--fg)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.end{justify-content:flex-end}
.note{padding:11px 14px;border-radius:11px;background:var(--glass);border:1px solid var(--line);border-left:3px solid var(--a1)}
.note.warn{border-left-color:var(--warn)}.note.bad{border-left-color:var(--bad);background:rgba(255,77,109,.06)}.note.good{border-left-color:var(--ok);background:rgba(34,227,161,.06)}
#editor{flex:1;min-height:360px;display:flex}
#editor .CodeMirror,#editor textarea{flex:1;height:auto;min-height:360px;border:1px solid var(--line2);border-radius:12px;font:12.5px/1.6 var(--mono)}
#edwrap .CodeMirror{height:62vh;border:1px solid var(--line2);border-radius:12px;font:12.5px/1.6 var(--mono);background:#05060a}
#edwrap .CodeMirror-gutters{background:#05060a;border-right:1px solid var(--line)}
#edrev pre.diff{max-height:56vh;overflow:auto}
#editor .CodeMirror{background:#05060a}#editor .CodeMirror-gutters{background:#05060a;border-right:1px solid var(--line)}
#editor textarea{width:100%;background:#05060a;color:var(--fg);padding:12px;resize:none;outline:none}
.diff .a{color:var(--ok);background:rgba(34,227,161,.07);display:inline-block;width:100%}.diff .d{color:#ff7d93;background:rgba(255,77,109,.07);display:inline-block;width:100%}.diff .h{color:#7fb4ff}

/* modal / palette / toasts */
#modal2{position:fixed;inset:0;background:rgba(3,4,8,.55);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;z-index:90;animation:fade .15s}
.shield{color:#7dd3fc}.tag.prot{--c:#7dd3fc}
.fbdrop{outline:2px dashed var(--a2);outline-offset:-6px;background:rgba(0,212,255,.05)}
.upl{display:grid;grid-template-columns:minmax(120px,1fr) 160px 90px;gap:10px;align-items:center;font-size:12.5px;padding:3px 0}
.upl .mbar{width:100%}.upl b{font-weight:500;font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#modal,#pal{position:fixed;inset:0;background:rgba(3,4,8,.6);backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;z-index:70;animation:fade .15s}
#pal{align-items:flex-start;padding-top:12vh}
.box{background:rgba(16,18,28,.96);border:1px solid var(--line2);border-radius:18px;padding:24px;width:min(460px,92vw);box-shadow:var(--shadow);animation:pop .22s cubic-bezier(.2,.8,.2,1)}
.box.wide{width:min(540px,94vw)}
@keyframes pop{from{transform:scale(.96) translateY(6px);opacity:0}}
.box h3{margin:0 0 6px;font-size:17px;display:flex;align-items:center;gap:10px}
.box p{margin:6px 0 14px;color:var(--mut)}
.form label{display:block;color:var(--mut);margin:12px 0 5px;font-size:12px;font-weight:500}
.form input:not([type=checkbox]){width:100%}
.form .two{display:grid;grid-template-columns:1fr 96px;gap:10px}
.err{color:#ff8aa0;min-height:18px}
.palbox{width:min(640px,94vw);background:rgba(16,18,28,.97);border:1px solid var(--line2);border-radius:16px;box-shadow:var(--shadow);overflow:hidden;animation:pop .2s}
.palbox .search{border:0;border-bottom:1px solid var(--line);border-radius:0;background:none;padding:6px 18px;box-shadow:none!important}
.palbox .search input{font-size:16px;padding:12px 0}
#palres{max-height:52vh;overflow:auto;padding:6px}
.pi{display:flex;align-items:center;gap:12px;padding:9px 12px;border-radius:10px;cursor:pointer}
.pi.on{background:rgba(124,92,255,.16)}
.pi .pic{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;background:rgba(255,255,255,.05);flex:none}
.pi b{font-weight:600}.pi .sub{font:11.5px var(--mono);color:var(--dim)}
.palfoot{padding:9px 16px;border-top:1px solid var(--line);color:var(--dim);font-size:11.5px;display:flex;gap:14px}
#toasts{position:fixed;right:20px;bottom:20px;display:flex;flex-direction:column;gap:10px;z-index:80}
.toast{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;background:rgba(18,20,32,.95);border:1px solid var(--line2);box-shadow:var(--shadow);backdrop-filter:blur(12px);animation:tin .3s cubic-bezier(.2,.8,.2,1);max-width:420px}
.toast.ok{border-left:3px solid var(--ok)}.toast.bad{border-left:3px solid var(--bad)}.toast.info{border-left:3px solid var(--a1)}
.toast.out{animation:tout .25s forwards}
@keyframes tin{from{transform:translateX(30px);opacity:0}}@keyframes tout{to{transform:translateX(30px);opacity:0}}

@media (max-width:1000px){.crow{grid-template-columns:100px 1fr 120px}.crow .c-ports,.crow .c-up{display:none}.dgauges{grid-template-columns:repeat(2,1fr)}.kbtn{min-width:0}.kbtn span{display:none}}
@media (max-width:640px){.crow{grid-template-columns:1fr}.crow .bars{display:none}.top{padding:10px 14px}#crumb{display:none}}
</style></head><body>
<div class="bg"><i></i><i></i><i></i></div>
<header class="top">
  <div class="brand" id="logo"><img src="/icon.png" alt="" class="logo-img"><span>dctl</span></div>
  <nav class="ptabs" id="ptabs"><a data-pg="docker"><span class="mi" data-i="box"></span>Docker</a><a data-pg="pm2"><span class="mi" data-i="bolt"></span>PM2</a></nav>
  <nav id="crumb"></nav>
  <span class="grow"></span>
  <button class="btn ghost" id="toprun" title="Run a command on one or many servers"></button>
  <button class="btn ghost hidden" id="termbtn" title="Show open terminals"><i id="tbtnic"></i><span>Terminal</span></button>
  <button class="kbtn" id="openpal"><span id="kbtnic"></span><span>Search servers, containers, PM2, ports…</span><kbd>Ctrl K</kbd></button>
</header>

<section id="home" class="view"></section>

<section id="listv" class="view hidden">
  <div class="lhead">
    <button class="icon-btn" id="backhome" title="Back to servers"></button>
    <div class="grow" style="min-width:200px"><h2 id="ltitle"></h2><div id="lsub" class="mut mono small"></div></div>
    <div class="seg" id="mseg"><button data-m="docker" class="on"><span class="mi" data-i="box"></span>Containers <span id="m-docker">·</span></button><button data-m="pm2"><span class="mi" data-i="bolt"></span>PM2 <span id="m-pm2">·</span></button></div>
    <div id="lmetrics" class="chips"></div>
    <button class="btn ghost" id="topfiles" title="Browse files, download or copy them"></button>
    <button class="btn ghost" id="topdisk" title="See what uses disk space and clean it up"></button>
    <button class="btn ghost" id="topsvc" title="systemd services: status, restart, logs"></button>
    <button class="btn ghost" id="toprun2" title="Run a command on this server"></button>
    <button class="btn primary" id="topterm" title="Open a shell on this server"></button>
    <button class="btn ghost" id="srvopt" title="Move to group / remove this server from the page"></button>
  </div>
  <nav class="dtabs hidden" id="dtabs"><a data-dt="containers" class="on">Containers <span id="dt-containers">·</span></a><a data-dt="images">Images <span id="dt-images">·</span></a><a data-dt="volumes">Volumes <span id="dt-volumes">·</span></a><a data-dt="networks">Networks <span id="dt-networks">·</span></a></nav>
  <div class="toolbar">
    <div class="search"><span id="sic"></span><input id="q" placeholder="Filter containers, images, projects…" autocomplete="off"><kbd>/</kbd></div>
    <div class="seg" id="fseg"><button data-f="all" class="on">All <span id="c-all">0</span></button><button data-f="run">Running <span id="c-run">0</span></button><button data-f="stop">Stopped <span id="c-stop">0</span></button></div>
    <label class="toggle" id="lcomp"><input type="checkbox" id="fcomp"><span></span>Compose only</label>
    <label class="toggle" id="llive" title="Refresh CPU/memory every 5s and container status every 10s (remembered)"><input type="checkbox" id="flive"><span></span>Live stats</label>
    <button class="btn ghost hidden" id="pmlogs" title="Watch the logs of many PM2 processes together"></button>
    <button class="btn ghost hidden" id="pmusers" title="Choose which users' PM2 to show"></button>
    <button class="btn ghost" id="rescan"></button>
  </div>
  <div class="lwrap"><div id="rows"></div></div>
</section>

<div id="scrim" class="hidden"></div>
<aside id="detail" class="hidden"></aside>
<div id="bulkbar" class="hidden"></div>
<div id="modal" class="hidden"><div class="box" id="mbox"></div></div>
<div id="modal2" class="hidden"><div class="box" id="mbox2"></div></div>
<div id="pal" class="hidden"><div class="palbox"><div class="search"><span id="palic"></span><input id="palq" placeholder="Search servers, containers, PM2 processes, ports (e.g. 6015)…" autocomplete="off"></div><div id="palres"></div>
  <div class="palfoot"><span><kbd>↑</kbd> <kbd>↓</kbd> navigate</span><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span></div></div></div>
<div id="termp" class="hidden"><div class="tpbox">
  <div class="tphead"><div id="ttabs"></div><span class="grow"></span>
    <button class="btn ghost sm hidden" id="trecon">Reconnect</button>
    <button class="btn ghost sm" id="tfiles" title="Browse / download files of this server">Files</button>
    <button class="btn ghost sm" id="tsudo" title="Become root on this server (asks your sudo password in the terminal)">sudo -i</button>
    <label class="toggle tauto" title="Selected text is copied to the clipboard automatically. Ctrl+Shift+C copies, Ctrl+Shift+V or right-click pastes."><input type="checkbox" id="tauto"><span></span>Copy on select</label>
    <label class="toggle tauto" title="Suggest commands from your history as you type (like zsh-autosuggestions). Press → or End to accept, Ctrl+→ for one word. Nothing is installed on the server."><input type="checkbox" id="tsugg"><span></span>Suggestions</label>
    <button class="btn ghost sm" id="tnew" title="Another session on this server">+ New</button>
    <button class="btn ghost sm" id="tfull" title="Full screen">⤢</button>
    <button class="btn ghost sm" id="tmin" title="Hide — sessions keep running">Minimize</button></div>
  <div id="tbodies"></div></div></div>
<div id="toasts"></div>

<script>
const TOKEN="__TOKEN__",NOPTY=__NOPTY__;
const S={picked:new Set(),rpicked:new Set(),dtab:'containers',res:{},view:'home',fleet:[],containers:[],stats:{},sel:null,tab:'overview',file:null,orig:'',sha:'',tmp:null,cm:null,ta:null,
  logTimer:null,liveTimer:null,mode:'docker',pm2:{},page:(()=>{try{return localStorage.getItem('dctl.page')==='pm2'?'pm2':'docker'}catch(_){return'docker'}})(),layout:null,layoutPage:null,known:[],gcol:{},fserver:'',filter:'all',collapsed:{},lastCheck:null,palI:0,palItems:[]};
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const OFF=['unreachable','no_access','error'];
const STATE={ok:'Online',sudo:'Online · sudo',need_password:'Locked',no_access:'No access',unreachable:'Offline',error:'Error',unknown:'Checking'};

// ---------- icons ----------
const P={
 box:'<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
 server:'<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
 play:'<path d="M7 4l13 8-13 8z" fill="currentColor"/>',stop:'<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
 restart:'<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',up:'<path d="M12 19V5M5 12l7-7 7 7"/>',
 download:'<path d="M12 3v12M6 11l6 6 6-6M4 21h16"/>',list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
 edit:'<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',term:'<path d="M4 17l6-5-6-5M12 19h8"/>',
 globe:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',plus:'<path d="M12 5v14M5 12h14"/>',
 lock:'<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',x:'<path d="M18 6L6 18M6 6l12 12"/>',
 search:'<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',back:'<path d="M15 18l-6-6 6-6"/>',
 bolt:'<path d="M13 2L3 14h9l-1 8 10-12h-9z"/>',info:'<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
 file:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
 logs:'<path d="M4 6h16M4 10h16M4 14h10M4 18h7"/>',code:'<path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/>',
 cpu:'<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
 trash:'<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/>',check:'<path d="M20 6L9 17l-5-5"/>',alert:'<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
 copy:'<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',chev:'<path d="M9 18l6-6-6-6"/>',more:'<circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="19" cy="12" r="1.6" fill="currentColor"/>',
 disk:'<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
 folder:'<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
 shield:'<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/>',upload:'<path d="M12 16V4M6 9l6-6 6 6M4 21h16"/>',
};
const ic=(n,s=16)=>`<svg class="ic" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${P[n]||''}</svg>`;
$('#kbtnic').innerHTML=ic('search',15);$('#sic').innerHTML=ic('search',15);$('#palic').innerHTML=ic('search',18);
$('#backhome').innerHTML=ic('back',18);$('#srvopt').innerHTML=ic('more',16);$('#srvopt').onclick=()=>S.fserver&&serverMenu(S.fserver);$('#rescan').innerHTML=ic('restart',15)+' Refresh';

// ---------- api / ui primitives ----------
async function raw(path,data){
  const r=await fetch(path,{method:'POST',headers:{'X-Dctl':TOKEN,'Content-Type':'application/json'},body:JSON.stringify(data||{})});
  if(!r.ok) throw new Error('HTTP '+r.status+' — restart dctl-ui and reload this page');
  return r.json();
}
async function api(path,data){
  const j=await raw(path,data);
  if(j.need_sudo){ if(await askSudo(j.need_sudo)) return api(path,data); throw new Error('sudo password needed for '+j.need_sudo); }
  if(j.protected){ if(await protectConfirm(j.protected,j.what))
      return api(path,{...data,confirm_server:[data&&data.confirm_server,j.protected].filter(Boolean).join(',')});
    throw new Error(`Cancelled — ${j.protected} is a protected server`); }
  if(!j.ok) throw new Error(j.error);
  return j;
}
function toast(msg,type='info',ms=3800){
  const t=document.createElement('div');t.className='toast '+type;
  t.innerHTML=ic(type==='ok'?'check':type==='bad'?'alert':'info',16)+`<div>${msg}</div>`;
  t.querySelector('svg').style.color=type==='ok'?'var(--ok)':type==='bad'?'var(--bad)':'#9d8bff';
  $('#toasts').appendChild(t);setTimeout(()=>{t.classList.add('out');setTimeout(()=>t.remove(),260)},ms);
}
function modal(h,wide){$('#mbox').className='box'+(wide===true?' wide':wide?' '+wide:'');$('#mbox').innerHTML=h;$('#modal').classList.remove('hidden')}
function closeModal(){$('#modal').classList.add('hidden');if(S.homeDirty&&S.view==='home'){S.homeDirty=false;renderHome(true)}}
function confirmBox(msg,ok='Confirm',danger){return new Promise(res=>{
  modal(`<h3>${ic(danger?'alert':'info',20)} Are you sure?</h3><p>${msg}</p><div class="row end"><button class="btn ghost" id="mno">Cancel</button><button class="btn ${danger?'danger':'primary'}" id="myes">${esc(ok)}</button></div>`);
  $('#mno').onclick=()=>{closeModal();res(false)};$('#myes').onclick=()=>{closeModal();res(true)};$('#myes').focus();
})}
// second layer above the main window, so a question never replaces what is open (editor, files, …)
function dlg2(h){$('#mbox2').className='box wide';$('#mbox2').innerHTML=h;$('#modal2').classList.remove('hidden')}
function closeDlg2(){$('#modal2').classList.add('hidden');$('#mbox2').innerHTML=''}
function protectConfirm(server,what){return new Promise(res=>{
  dlg2(`<h3>${ic('shield',20)} ${esc(server)} is protected</h3>
   <p>You are about to <b>${esc(what||'make a change')}</b> on a protected server.</p>
   <div class="form"><label>Type the server name <code>${esc(server)}</code> to continue</label><input id="d2in" autocomplete="off" spellcheck="false"></div>
   <div class="row end" style="margin-top:6px"><button class="btn ghost" id="d2no">Cancel</button><button class="btn danger" id="d2yes" disabled>${ic('shield',14)} Continue</button></div>`);
  $('#d2in').oninput=()=>{$('#d2yes').disabled=$('#d2in').value.trim()!==server};
  $('#d2in').onkeydown=e=>{if(e.key==='Enter'&&!$('#d2yes').disabled)$('#d2yes').click()};
  $('#d2no').onclick=()=>{closeDlg2();res(false)};$('#d2yes').onclick=()=>{closeDlg2();res(true)};setTimeout(()=>$('#d2in').focus(),30)})}
function choose2(title,html,btns){return new Promise(res=>{   // btns: [[key,label,cls]]
  dlg2(`<h3>${title}</h3>${html}<div class="row end" style="margin-top:12px;flex-wrap:wrap"><button class="btn ghost" id="d2no">Cancel</button>${btns.map(([k,l,c])=>`<button class="btn ${c||''}" data-d2="${k}">${l}</button>`).join('')}</div>`);
  $('#d2no').onclick=()=>{closeDlg2();res(null)};$$('#mbox2 [data-d2]').forEach(b=>b.onclick=()=>{closeDlg2();res(b.dataset.d2)})})}
function askSudo(server){return new Promise(res=>{
  dlg2(`<h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('lock',18)}</span> Unlock ${esc(server)}</h3>
  <p>Your user needs <b>sudo</b> on this server for Docker / compose files. The password stays only in dctl's memory — never written to disk.</p>
  <div class="form"><input type="password" id="d2pw" placeholder="sudo password"></div><div id="d2err" class="err" style="margin-top:8px"></div>
  <div class="row end" style="margin-top:6px"><button class="btn ghost" id="d2no">Cancel</button><button class="btn primary" id="d2yes">${ic('lock',14)} Unlock</button></div>`);
  const go=async()=>{$('#d2yes').disabled=true;$('#d2err').innerHTML='<span class="spin"></span>';
    try{const j=await raw('/api/sudo',{server,password:$('#d2pw').value});if(!j.ok){$('#d2err').textContent=j.error;$('#d2yes').disabled=false;return}
      closeDlg2();toast(`${esc(server)} unlocked`,'ok');res(true)}catch(e){$('#d2err').textContent=e.message;$('#d2yes').disabled=false}};
  $('#d2yes').onclick=go;$('#d2pw').onkeydown=e=>{if(e.key==='Enter')go()};$('#d2no').onclick=()=>{closeDlg2();res(false)};
  setTimeout(()=>$('#d2pw').focus(),30);
})}
async function busy(btn,fn){const t=btn?.innerHTML;if(btn){btn.disabled=true;btn.innerHTML='<span class="spin"></span> '+(btn.textContent.trim()||'')}
  try{return await fn()}finally{if(btn&&btn.isConnected){btn.disabled=false;btn.innerHTML=t}}}
const clamp=p=>Math.max(0,Math.min(100,Math.round(p||0)));
const pcol=p=>p>=85?'var(--bad)':p>=65?'var(--warn)':'var(--ok)';
const pgrad=p=>p>=85?'linear-gradient(90deg,#ff4d6d,#ff7d93)':p>=65?'linear-gradient(90deg,#ff9f1a,#ffc94d)':'linear-gradient(90deg,#16c88c,#22e3a1)';
function ring(p,label){p=clamp(p);const r=26,c=+(2*Math.PI*r).toFixed(1);
  return `<div class="ring"><svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="${r}" class="trk"/><circle cx="32" cy="32" r="${r}" class="val" style="color:${pcol(p)};stroke:${pcol(p)};--c0:${c};stroke-dasharray:${c};stroke-dashoffset:${(c*(1-p/100)).toFixed(1)}"/></svg><b>${p}<small>%</small></b><span>${label}</span></div>`}
function fmtUp(s){if(!s)return'';const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m`}
function countUp(root){(root||document).querySelectorAll('[data-to]').forEach(el=>{const to=+el.dataset.to,t0=performance.now(),dur=900;
  const f=t=>{const k=Math.min(1,(t-t0)/dur),e=1-Math.pow(1-k,3);el.textContent=Math.round(to*e);if(k<1)requestAnimationFrame(f)};requestAnimationFrame(f)})}
function cpuPct(m){return m&&m.nproc&&m.load!=null?m.load/m.nproc*100:null}
function counts(s){const m=s.m||{};if(m.all!=null)return{run:m.run,all:m.all};
  const cs=S.containers.filter(c=>c.server===s.name);return cs.length?{run:cs.filter(c=>c.running).length,all:cs.length}:null}

// ---------- views / nav ----------
function setView(v){S.view=v;$('#home').classList.toggle('hidden',v!=='home');$('#listv').classList.toggle('hidden',v!=='list');
  if(v!=='list')stopLive();renderCrumb();renderBulk()}
function renderCrumb(){$('#crumb').innerHTML=S.view==='list'?`<span class="dim">/</span><a id="cr-home">${PGT[S.page]} servers</a><span class="dim">/</span><b>${esc(S.fserver||'All servers')}</b>`:'';
  $('#cr-home')&&($('#cr-home').onclick=goHome)}
async function goHome(){if(!await closeDetail())return;loadHome()}
$('#logo').onclick=goHome;$('#backhome').onclick=goHome;

// ---------- home ----------
async function loadHome(){
  setView('home');renderPTabs();const pg=S.page;
  if(!S.layout||S.layoutPage!==pg)$('#home').innerHTML=`<div class="hero" style="margin-bottom:22px"><div><h1 style="font-size:24px;margin:0">${PGT[pg]} servers</h1><p class="mut small" style="margin-top:4px"><span class="spin"></span> checking servers…</p></div></div>
    <div class="grid">${'<div class="sk" style="height:230px"></div>'.repeat(3)}</div>`;
  try{const j=await api('/api/servers',{page:pg});if(S.page!==pg)return;applyPage(j);S.lastCheck=new Date()}catch(e){toast(esc(e.message),'bad');return}
  renderHome();bgScan();
}
const PGT={docker:'Docker',pm2:'PM2'};
function applyPage(j){S.layout=j.layout;S.layoutPage=S.page;S.known=j.known||[];S.fleet=j.servers}
function mergeFleet(list){const by=Object.fromEntries((list||[]).map(x=>[x.name,x]));S.fleet=S.fleet.map(x=>by[x.name]||x)}
function renderPTabs(){$$('#ptabs a').forEach(a=>a.classList.toggle('on',a.dataset.pg===S.page))}
$('#ptabs').onclick=async e=>{const a=e.target.closest('[data-pg]');if(!a)return;
  if(a.dataset.pg===S.page&&S.view==='home')return;if(!await closeDetail())return;
  S.page=a.dataset.pg;try{localStorage.setItem('dctl.page',S.page)}catch(_){}S.fleet=[];S.layout=null;loadHome()};
let scanning=false;
async function bgScan(){const pg=S.page,names=S.fleet.filter(s=>s.state!=='unreachable').map(s=>s.name);if(!names.length)return;
  if(pg==='pm2'){await Promise.all(S.fleet.filter(s=>s.state!=='unreachable'&&s.m&&s.m.pm2).map(async s=>{
      try{const j=await raw('/api/pm2',{server:s.name});if(j.ok){S.pm2[s.name]=j.pm2;if(S.view==='home'&&S.page==='pm2')renderHome(true)}}catch(_){}}));return}
  if(scanning)return;scanning=true;
  try{const j=await api('/api/scan',{servers:names});S.containers=j.containers;mergeFleet(j.servers);if(S.view==='home'&&S.page===pg)renderHome(true)}catch(e){}
  scanning=false}
function pmSum(n){const d=S.pm2[n];if(!d)return null;const v=pmVisible(d),ps=v.flatMap(x=>x.procs);
  return{on:ps.filter(pRun).length,all:ps.length,locked:v.filter(x=>x.locked).length}}
function renderHome(quiet){
  if(quiet&&!$('#modal').classList.contains('hidden')){S.homeDirty=true;return}
  const L=S.layout||{ungrouped:[],groups:[]},by=Object.fromEntries(S.fleet.map(s=>[s.name,s]));
  const t=S.lastCheck?S.lastCheck.toLocaleTimeString():'';let i=0;
  const cards=(names,g)=>`<div class="grid">${names.filter(n=>by[n]).map(n=>card(by[n],++i)).join('')}
      <div class="scard add mini" data-addg="${esc(g)}" style="--d:${(++i)*55}ms"><div class="addic">${ic('plus',20)}</div><b>Add server${g?` to ${esc(g)}`:''}</b></div></div>`;
  const online=names=>names.filter(n=>by[n]&&by[n].state!=='unreachable').length;
  const empty=!L.ungrouped.length&&!L.groups.length;
  $('#home').innerHTML=`
  <div class="hero" style="align-items:center;margin-bottom:22px"><div>
      <h1 style="font-size:24px;margin:0;letter-spacing:-.4px;display:flex;align-items:center;gap:10px">${ic(S.page==='pm2'?'bolt':'box',22)} ${PGT[S.page]} servers</h1>
      <p class="mut small" style="margin-top:4px">Click a server to manage its ${S.page==='pm2'?'PM2 processes':'containers'}${t?' · last checked '+t:''}</p></div>
    <div class="hero-actions"><button class="btn ghost" id="hrefresh">${ic('restart',15)} Refresh</button><button class="btn" id="hgroup">${ic('folder',15)} New group</button><button class="btn primary" id="hadd">${ic('plus',15)} Add server</button></div></div>
  ${empty?`<div class="empty" style="border:1.5px dashed var(--line2);border-radius:var(--r)"><div class="bigic">${ic(S.page==='pm2'?'bolt':'box',22)}</div><b>No servers on the ${PGT[S.page]} page yet</b>
      <div style="margin:6px 0 16px">Add a server from your SSH config, or a new one.</div><button class="btn primary" data-addg="">${ic('plus',15)} Add server</button></div>`:''}
  ${!empty&&(L.ungrouped.length||!L.groups.length)?`<section class="gsec">${L.groups.length?`<div class="gsh"><b class="mut">Ungrouped</b><span class="gcount">${L.ungrouped.length}</span></div>`:''}${cards(L.ungrouped,'')}</section>`:''}
  ${L.groups.map((g,gi)=>{const col=S.gcol[S.page+':'+g.name];return `<section class="gsec">
    <div class="gsh"><button class="icon-btn sm" data-gact="col" data-g="${esc(g.name)}"><span class="chev ${col?'':'open'}">${ic('chev',14)}</span></button>
      <span class="gico">${ic('folder',14)}</span><b>${esc(g.name)}</b><span class="gcount"><span style="color:var(--ok)">●</span> ${online(g.servers)}/${g.servers.length}</span><span class="grow"></span>
      <span class="gacts">${gi?`<button class="icon-btn sm" data-gact="up" data-g="${esc(g.name)}" title="Move group up">${ic('up',14)}</button>`:''}
      <button class="icon-btn sm" data-gact="run" data-g="${esc(g.name)}" title="Run a command on all servers of this group">${ic('play',12)}</button>
      <button class="icon-btn sm" data-gact="add" data-g="${esc(g.name)}" title="Add server to this group">${ic('plus',14)}</button>
      <button class="icon-btn sm" data-gact="ren" data-g="${esc(g.name)}" title="Rename group">${ic('edit',14)}</button>
      <button class="icon-btn sm x2" data-gact="del" data-g="${esc(g.name)}" title="Delete group">${ic('x',14)}</button></span></div>
    ${col?'':cards(g.servers,g.name)}</section>`}).join('')}`;
  if(!quiet)countUp($('#home'));
  $('#hrefresh').onclick=e=>busy(e.currentTarget,loadHome);$('#hadd').onclick=()=>addServer('');$('#hgroup').onclick=newGroup;
}
function card(s,i){
  const pm=S.page==='pm2',st=pm&&s.state!=='unreachable'?'ok':s.state;
  const off=pm?s.state==='unreachable':OFF.includes(s.state),m=s.m||{},c=counts(s),cpu=cpuPct(m),ps=pm?pmSum(s.name):null;
  const meta=[m.os&&esc(m.os),pm?(m.pm2&&'pm2 ×'+m.pm2):(m.ver&&'docker '+esc(m.ver)),m.nproc&&m.nproc+' vCPU',m.uptime&&'up '+fmtUp(m.uptime)].filter(Boolean);
  let foot;
  if(pm)foot=ps&&!ps.all&&!ps.locked?`<div class="cbar none"></div><div class="sc-foot"><span>No PM2 processes</span></div>`:ps?`<div class="cbar"><i style="width:${ps.all?ps.on/ps.all*100:0}%"></i></div><div class="sc-foot"><span><b>${ps.on}</b> online</span><span><b>${ps.all-ps.on}</b> stopped</span>${ps.locked?`<span style="color:var(--warn)">${ic('lock',12)} ${ps.locked} locked</span>`:''}</div>`
    :`<div class="cbar none"></div><div class="sc-foot"><span>${m.pm2?`<b>${m.pm2}</b> PM2 daemon${m.pm2==1?'':'s'} · loading…`:'No PM2 running'}</span></div>`;
  else foot=c?`<div class="cbar"><i style="width:${c.all?c.run/c.all*100:0}%"></i></div><div class="sc-foot"><span><b>${c.run}</b> running</span><span><b>${c.all-c.run}</b> stopped</span></div>`
    :m.hasdocker===false?`<div class="cbar none"></div><div class="sc-foot"><span>Docker not installed</span></div>`
    :`<div class="cbar none"></div><div class="sc-foot">${s.state==='need_password'?`<span style="color:var(--warn)">${ic('lock',13)} Click to unlock with sudo password</span>`:'<span>container counts loading…</span>'}</div>`;
  return `<div class="scard st-${st} ${off?'off':''}" data-open="${esc(s.name)}" style="--d:${i*55}ms">
    <div class="sc-top"><span class="pulse"></span><div class="grow" style="min-width:0"><div class="sc-name">${esc(s.name)}${s.protected?` <span class="shield" title="Protected: changes ask you to type the server name">${ic('shield',13)}</span>`:''}</div><div class="sc-host">${esc(s.host||'')}</div></div>
      <span class="tag">${st==='need_password'?ic('lock',11):''}${STATE[st]||st}</span>
      <button class="icon-btn x" data-menu="${esc(s.name)}" title="Move to group / remove server">${ic('more',16)}</button></div>
    ${off?`<div class="sc-err">${esc(s.error||'Not reachable')}</div><div class="mut small">Check VPN / firewall / SSH</div>`:`
    <div class="rings">${ring(cpu,'CPU')}${ring(m.mem,'MEMORY')}${ring(m.disk,'DISK')}</div>${foot}`}
    ${meta.length?`<div class="sc-meta">${meta.map(x=>`<span class="pill">${x}</span>`).join('')}</div>`:''}
  </div>`}
let menuAt=0;
$('#home').addEventListener('pointerdown',e=>{const mn=e.target.closest('[data-menu]');if(!mn||e.button!==0)return;
  e.preventDefault();e.stopPropagation();menuAt=Date.now();serverMenu(mn.dataset.menu)},true);
$('#home').addEventListener('click',async e=>{
  if(Date.now()-menuAt<1500){e.stopPropagation();return}
  const mn=e.target.closest('[data-menu]'),ga=e.target.closest('[data-gact]'),ad=e.target.closest('[data-addg]'),op=e.target.closest('[data-open]');
  if(mn){e.stopPropagation();return serverMenu(mn.dataset.menu)}
  if(ga){const g=ga.dataset.g,a=ga.dataset.gact;
    if(a==='col'){S.gcol[S.page+':'+g]=!S.gcol[S.page+':'+g];renderHome(true)}
    else if(a==='up')layoutOp('group_up',{group:g});
    else if(a==='add')addServer(g);
    else if(a==='run')openRun((S.layout.groups.find(x=>x.name===g)||{servers:[]}).servers);
    else if(a==='ren')renameGroup(g);
    else if(a==='del'&&await confirmBox(`Delete group <b>${esc(g)}</b>? Its servers move to <b>Ungrouped</b> — nothing changes on the servers.`,'Delete group',true))layoutOp('group_delete',{group:g});
    return}
  if(ad)return addServer(ad.dataset.addg);
  if(op){const s=S.fleet.find(x=>x.name===op.dataset.open);
    if(S.page==='pm2'){if(s&&s.state==='unreachable'){toast(`<b>${esc(s.name)}</b>: ${esc(s.error||'offline')}`,'bad',6000);return}return openServer(op.dataset.open)}
    if(s&&OFF.includes(s.state)){toast(`<b>${esc(s.name)}</b>: ${esc(s.error||STATE[s.state])}`,'bad',6000);return}
    if(s&&s.state==='need_password'&&!s.unlocked&&!await askSudo(s.name))return;
    openServer(op.dataset.open)}
});
async function layoutOp(op,extra){
  try{const j=await api('/api/layout',{page:S.page,op,...extra});applyPage(j);renderHome(true);return j}
  catch(e){toast(esc(e.message),'bad');throw e}}
function groupSelect(sel){const L=S.layout||{groups:[]};
  return `<select id="as_group" style="width:100%"><option value="">Ungrouped</option>${L.groups.map(g=>`<option ${g.name===sel?'selected':''}>${esc(g.name)}</option>`).join('')}<option value="__new">+ New group…</option></select>
    <input id="as_gnew" class="hidden" placeholder="New group name, e.g. Production" style="margin-top:8px">`}
function wireGroupSelect(){$('#as_group').onchange=e=>{$('#as_gnew').classList.toggle('hidden',e.target.value!=='__new');if(e.target.value==='__new')$('#as_gnew').focus()}}
function groupValue(){const v=$('#as_group').value;return v==='__new'?$('#as_gnew').value.trim():v}
function nameModal(title,label,val,okTxt){return new Promise(res=>{
  modal(`<div class="form"><h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('folder',18)}</span> ${title}</h3>
    <label>${label}</label><input id="gname" value="${esc(val||'')}" placeholder="e.g. Production, Staging, Clients"><div id="gerr" class="err" style="margin-top:8px"></div>
    <div class="row end" style="margin-top:6px"><button class="btn ghost" id="mno">Cancel</button><button class="btn primary" id="myes">${okTxt}</button></div></div>`);
  const go=()=>{const v=$('#gname').value.trim();if(!v){$('#gerr').textContent='Enter a name';return}closeModal();res(v)};
  $('#myes').onclick=go;$('#gname').onkeydown=e=>{if(e.key==='Enter')go()};$('#mno').onclick=()=>{closeModal();res(null)};$('#gname').focus();$('#gname').select()})}
async function newGroup(){const n=await nameModal(`New group on ${PGT[S.page]} page`,'Group name','','Create');if(n){await layoutOp('group_add',{group:n}).catch(()=>{});toast(`Group <b>${esc(n)}</b> created — use <b>+</b> on it to add servers`,'ok')}}
async function renameGroup(g){const n=await nameModal('Rename group','New name',g,'Rename');if(n&&n!==g)layoutOp('group_rename',{group:g,to:n}).catch(()=>{})}
function serverMenu(name){
  const s=S.fleet.find(x=>x.name===name)||{name},L=S.layout||{groups:[],ungrouped:[]},cur=(L.groups.find(g=>g.servers.includes(name))||{}).name||'';
  const other=S.page==='pm2'?'docker':'pm2',k=(S.known||[]).find(x=>x.name===name),onOther=k&&k.pages.includes(other);
  modal(`<div class="form"><h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('server',18)}</span> ${esc(name)}</h3>
    <div class="row" style="margin-top:-6px;gap:8px;align-items:center"><p class="mono small grow" style="margin:0">${esc(s.host||'')}</p>
      ${s.added?`<button class="btn sm" id="medit" title="Change host, user, port, key, jump host or name">${ic('edit',13)} Edit</button>`:`<span class="dim small" title="This server comes from ~/.ssh/config">edit in ~/.ssh/config</span>`}</div>
    <div class="row" style="gap:8px;margin-top:4px"><button class="btn primary" id="mterm" style="flex:1;justify-content:center">${ic('term',15)} Terminal</button>
      <button class="btn" id="mrun" style="flex:1;justify-content:center">${ic('play',13)} Run command</button><button class="btn" id="mdisk" style="flex:1;justify-content:center">${ic('disk',15)} Disk cleanup</button></div>
    <div class="row" style="gap:8px;margin-top:8px"><button class="btn" id="mfiles" style="flex:1;justify-content:center">${ic('folder',15)} Files</button><button class="btn" id="msvc" style="flex:1;justify-content:center">${ic('cpu',15)} Services</button></div>
    <div class="row" style="gap:10px;margin-top:12px;align-items:center"><span class="shield">${ic('shield',18)}</span>
      <div class="grow small">${s.protected?'<b>Protected.</b> Restart, stop, delete, edit, cleanup… ask you to type the server name first.':'<b>Protect this server</b> (e.g. production): every change asks you to type its name first.'}</div>
      <button class="btn sm" id="mprot">${s.protected?'Remove protection':'Protect'}</button></div>
    <label>Group on ${PGT[S.page]} page</label>${groupSelect(cur)}
    <div class="row end" style="margin-top:4px"><button class="btn primary" id="mmove">${ic('check',14)} Move</button></div>
    <div class="msep"></div>
    <div class="row" style="flex-wrap:wrap;gap:8px">
      <button class="btn" id="mother">${ic(other==='pm2'?'bolt':'box',14)} Move to ${PGT[other]} page</button>
      <span class="grow"></span><button class="btn danger" id="mrm">${ic('x',14)} Remove from ${PGT[S.page]} page</button></div>
    ${s.added?`<div class="row end" style="margin-top:8px"><button class="btn ghost" id="mforget" style="color:var(--bad)">Forget this server completely</button></div>`:''}
    <div class="row end" style="margin-top:10px"><button class="btn ghost" id="mno">Close</button></div></div>`);
  wireGroupSelect();$('#mno').onclick=closeModal;$('#mterm').onclick=()=>{closeModal();openTerm(name)};$('#mrun').onclick=()=>{closeModal();openRun([name])};$('#mdisk').onclick=()=>{closeModal();openDisk(name)};$('#mfiles').onclick=()=>{closeModal();openFiles(name)};$('#msvc').onclick=()=>{closeModal();openSvc(name)};
  $('#mprot').onclick=async()=>{const on=!s.protected;try{await api('/api/protect',{server:name,on});s.protected=on;
      for(const x of S.fleet)if(x.name===name)x.protected=on;closeModal();toast(on?`${ic('shield',14)} <b>${esc(name)}</b> is now protected`:`Protection removed from <b>${esc(name)}</b>`,'ok');
      if(S.view==='home')renderHome(true);else renderLHead()}catch(e){toast(esc(e.message),'bad')}};
  $('#mmove').onclick=async()=>{const g=groupValue();if($('#as_group').value==='__new'&&!g){$('#as_gnew').focus();return}closeModal();
    await layoutOp('move',{server:name,group:g}).catch(()=>{});toast(`${esc(name)} → <b>${esc(g||'Ungrouped')}</b>`,'ok')};
  $('#mrm').onclick=async()=>{closeModal();if(!await confirmBox(`Remove <b>${esc(name)}</b> from the ${PGT[S.page]} page? Nothing changes on the server — you can add it back any time.`,'Remove',true))return;
    await layoutOp('remove',{server:name}).catch(()=>{});toast(`${esc(name)} removed from ${PGT[S.page]} page`,'ok');if(S.view==='list')goHome()};
  $('#mother').onclick=async()=>{try{await api('/api/layout',{page:other,op:'add',server:name,group:''});closeModal();toast(`${esc(name)} moved to the <b>${PGT[other]}</b> page`,'ok');
    if(S.view==='list'){goHome();return}const j=await api('/api/servers',{page:S.page});applyPage(j);renderHome(true)}catch(e){toast(esc(e.message),'bad')}};
  $('#medit')&&($('#medit').onclick=()=>{closeModal();editServer(name)});
  $('#mforget')&&($('#mforget').onclick=async()=>{closeModal();
    if(await confirmBox(`Forget <b>${esc(name)}</b>? It is removed from all pages and from dctl. Nothing changes on the server itself.`,'Forget',true)){
      try{await api('/api/server_remove',{name});toast(`${esc(name)} removed`,'ok')}catch(err){toast(esc(err.message),'bad')}loadHome()}});
}
async function editServer(name){
  let a;try{a=await api('/api/server_get',{name})}catch(e){toast(esc(e.message),'bad');return}
  let auth='key';
  modal(`<div class="form"><h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('edit',18)}</span> Edit ${esc(name)}</h3>
    <div class="two"><div><label>Host / IP</label><input id="es_host" value="${esc(a.host)}"></div><div><label>Port</label><input id="es_port" value="${esc(a.port||22)}"></div></div>
    <div class="two" style="grid-template-columns:1fr 1fr"><div><label>Display name</label><input id="es_name" value="${esc(a.name)}"></div><div><label>SSH user</label><input id="es_user" value="${esc(a.user)}" placeholder="ubuntu"></div></div>
    <label>Authentication</label>
    <div class="seg" style="width:100%"><button data-auth="key" class="on" style="flex:1;justify-content:center">${ic('lock',14)} SSH key</button><button data-auth="pw" style="flex:1;justify-content:center">${ic('term',14)} Password</button></div>
    <div id="es_key_box"><label>Private key file (optional — your default keys are used if empty)</label><input id="es_key" value="${esc(a.key)}" placeholder="~/.ssh/my-server.pem"></div>
    <div id="es_pw_box" class="hidden"><label>SSH password</label><input id="es_pw" type="password">
      <div class="note" style="margin-top:10px;font-size:12.5px">Used <b>once</b> to add your SSH public key to the server. The password is never stored.</div></div>
    <label>Jump host <span class="dim">(optional)</span></label>
    <input id="es_jump" list="es_jumps" value="${esc(a.jump)}" placeholder="bastion   or   user@bastion.example.com:22" autocomplete="off">
    <datalist id="es_jumps">${(S.known||[]).filter(k=>k.name!==name).map(k=>`<option value="${esc(k.name)}">${esc(k.host||'')}</option>`).join('')}</datalist>
    <div class="note" style="margin-top:12px;font-size:12.5px">If you change the connection, dctl tests it before saving. Nothing changes on the server.</div>
    <div id="es_err" class="err" style="margin-top:10px"></div>
    <div class="row end" style="margin-top:8px"><button class="btn ghost" id="mno">Cancel</button><button class="btn primary" id="myes">${ic('check',15)} Save</button></div></div>`,true);
  $$('#mbox [data-auth]').forEach(b=>b.onclick=()=>{auth=b.dataset.auth;$$('#mbox [data-auth]').forEach(x=>x.classList.toggle('on',x===b));
    $('#es_key_box').classList.toggle('hidden',auth!=='key');$('#es_pw_box').classList.toggle('hidden',auth!=='pw')});
  $('#mno').onclick=closeModal;$('#es_host').focus();
  const go=async()=>{
    const d={old:name,name:$('#es_name').value.trim(),host:$('#es_host').value.trim(),port:$('#es_port').value.trim(),user:$('#es_user').value.trim(),
             key:auth==='key'?$('#es_key').value.trim():(a.key||''),password:auth==='pw'?$('#es_pw').value:'',jump:$('#es_jump').value.trim()};
    if(auth==='pw'&&!d.password){$('#es_err').textContent='Enter the SSH password';return}
    $('#myes').disabled=true;$('#es_err').innerHTML='<span class="spin"></span> <span class="mut">checking…</span>';
    try{const j=await api('/api/server_edit',d);closeModal();
      toast(j.renamed?`<b>${esc(name)}</b> renamed to <b>${esc(j.server)}</b>`:`<b>${esc(j.server)}</b> saved`,'ok');
      if(S.view==='list'&&S.fserver===name){if(j.renamed)goHome();else openServer(name)}else await loadHome();
    }catch(e){$('#es_err').textContent=e.message;$('#myes').disabled=false}};
  $('#myes').onclick=go;$$('#mbox input').forEach(i=>i.onkeydown=e=>{if(e.key==='Enter')go()});
}
function addServer(group){
  let mode='existing',auth='key';
  const avail=(S.known||[]).filter(k=>!k.pages.includes(S.page));
  if(!avail.length)mode='new';
  modal(`<div class="form"><h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('server',18)}</span> Add server to ${PGT[S.page]} page</h3>
    <div class="seg" style="width:100%"><button data-mode="existing" class="${mode==='existing'?'on':''}" style="flex:1;justify-content:center">${ic('list',14)} From my servers <span>${avail.length}</span></button><button data-mode="new" class="${mode==='new'?'on':''}" style="flex:1;justify-content:center">${ic('plus',14)} New server</button></div>
    <div id="as_ex" class="${mode==='existing'?'':'hidden'}">
      ${avail.length?`<label>Server <span class="dim">(from ~/.ssh/config or added before)</span></label>
      <div class="pick" id="as_pick">${avail.map((k,i)=>`<label class="pk"><input type="checkbox" value="${esc(k.name)}"><div><b>${esc(k.name)}</b><span class="mono">${esc(k.host||'')}</span></div>${k.pages.length?`<span class="pill" title="Adding it here removes it from the ${k.pages.map(p=>PGT[p]).join(', ')} page">moves from ${k.pages.map(p=>PGT[p]).join(', ')}</span>`:''}</label>`).join('')}</div>`
      :`<div class="note" style="margin-top:12px">All your servers are already on this page. Use <b>New server</b>.</div>`}
    </div>
    <div id="as_new" class="${mode==='new'?'':'hidden'}">
    <div class="two"><div><label>Host / IP</label><input id="as_host" placeholder="10.0.0.12"></div><div><label>Port</label><input id="as_port" value="22"></div></div>
    <div class="two" style="grid-template-columns:1fr 1fr"><div><label>Display name</label><input id="as_name" placeholder="prod-api"></div><div><label>SSH user</label><input id="as_user" placeholder="ubuntu"></div></div>
    <label>Authentication</label>
    <div class="seg" style="width:100%"><button data-auth="key" class="on" style="flex:1;justify-content:center">${ic('lock',14)} SSH key</button><button data-auth="pw" style="flex:1;justify-content:center">${ic('term',14)} Password</button></div>
    <div id="as_key_box"><label>Private key file (optional — your default keys are used if empty)</label><input id="as_key" placeholder="~/.ssh/my-server.pem"></div>
    <div id="as_pw_box" class="hidden"><label>SSH password</label><input id="as_pw" type="password">
      <div class="note" style="margin-top:10px;font-size:12.5px">Used <b>once</b> to add your SSH public key to the server (like <code>ssh-copy-id</code>). Afterwards no password is needed. The password is never stored.</div></div>
    <label>Jump host <span class="dim">(optional: only if this server is reachable just through another server / bastion)</span></label>
    <input id="as_jump" list="as_jumps" placeholder="bastion   or   user@bastion.example.com:22" autocomplete="off">
    <datalist id="as_jumps">${(S.known||[]).map(k=>`<option value="${esc(k.name)}">${esc(k.host||'')}</option>`).join('')}</datalist>
    </div>
    <label>Group</label>${groupSelect(group)}
    <div id="as_err" class="err" style="margin-top:10px"></div>
    <div class="row end" style="margin-top:8px"><button class="btn ghost" id="mno">Cancel</button><button class="btn primary" id="myes">${ic('check',15)} Add</button></div></div>`,true);
  wireGroupSelect();
  $$('#mbox [data-mode]').forEach(b=>b.onclick=()=>{mode=b.dataset.mode;$$('#mbox [data-mode]').forEach(x=>x.classList.toggle('on',x===b));
    $('#as_ex').classList.toggle('hidden',mode!=='existing');$('#as_new').classList.toggle('hidden',mode!=='new');$('#myes').innerHTML=ic('check',15)+(mode==='new'?' Test &amp; add':' Add');if(mode==='new')$('#as_host').focus()});
  $$('#mbox [data-auth]').forEach(b=>b.onclick=()=>{auth=b.dataset.auth;$$('#mbox [data-auth]').forEach(x=>x.classList.toggle('on',x===b));
    $('#as_key_box').classList.toggle('hidden',auth!=='key');$('#as_pw_box').classList.toggle('hidden',auth!=='pw')});
  $('#mno').onclick=closeModal;
  if(mode==='new')$('#myes').innerHTML=ic('check',15)+' Test &amp; add';
  const go=async()=>{
    const g=groupValue();if($('#as_group').value==='__new'&&!g){$('#as_err').textContent='Enter the new group name';return}
    if(mode==='existing'){const picked=$$('#as_pick input:checked').map(x=>x.value);if(!picked.length){$('#as_err').textContent='Tick at least one server';return}
      $('#myes').disabled=true;$('#as_err').innerHTML='<span class="spin"></span>';
      try{let j;for(const n of picked)j=await api('/api/layout',{page:S.page,op:'add',server:n,group:g});applyPage(j);closeModal();
        toast(`${picked.length} server${picked.length>1?'s':''} added${g?` to <b>${esc(g)}</b>`:''}`,'ok');renderHome();bgScan()}
      catch(e){$('#as_err').textContent=e.message;$('#myes').disabled=false}return}
    const d={name:$('#as_name').value,host:$('#as_host').value,port:$('#as_port').value,user:$('#as_user').value,
             key:auth==='key'?$('#as_key').value:'',password:auth==='pw'?$('#as_pw').value:'',jump:$('#as_jump').value.trim(),page:S.page,group:g};
    if(auth==='pw'&&!d.password){$('#as_err').textContent='Enter the SSH password';return}
    $('#myes').disabled=true;$('#as_err').innerHTML='<span class="spin"></span> <span class="mut">connecting…</span>';
    try{const j=await raw('/api/server_add',d);
      if(!j.ok){$('#as_err').textContent=j.error;$('#myes').disabled=false;return}
      closeModal();toast(`<b>${esc(j.server)}</b> added`,'ok');await loadHome();
    }catch(e){$('#as_err').textContent=e.message;$('#myes').disabled=false}};
  $('#myes').onclick=go;$$('#as_new input').forEach(i=>i.onkeydown=e=>{if(e.key==='Enter')go()});
}

// ---------- container list ----------
function openServer(name,thenSelect){
  if(S.sel&&name&&S.sel.server!==name)forceCloseDetail();
  if(thenSelect&&S.page!=='docker'){S.page='docker';renderPTabs()}
  if(S.fserver!==name){S.picked.clear();S.rpicked.clear();if(S.dtab!=='containers'){S.dtab='containers';$$('#dtabs a').forEach(x=>x.classList.toggle('on',x.dataset.dt==='containers'));['#fseg','#llive'].forEach(x=>$(x).classList.remove('hidden'))}}
  applyMode(S.page);
  S.fserver=name;syncDtabs();setView('list');$('#q').value='';renderLHead();renderRows();
  if(S.mode==='pm2'){if(name)pm2Scan();if($('#flive').checked)startLive();return}
  scan(name||undefined).then(()=>{if(thenSelect){const c=S.containers.find(x=>x.server===thenSelect.server&&x.name===thenSelect.name);if(c&&(!S.sel||S.sel.name!==c.name))select(c)}});
  if(thenSelect)select(thenSelect);
  if($('#flive').checked)startLive();
}
function renderLHead(){
  const s=S.fleet.find(x=>x.name===S.fserver);
  $('#ltitle').innerHTML=S.fserver?`<span class="pulse st-${s?.state||'ok'}" style="margin:0"></span>${esc(S.fserver)}${s?.protected?` <span class="tag prot" title="Changes ask you to type the server name">${ic('shield',11)} protected</span>`:''}`:`${ic('globe',22)} All servers`;
  $('#lsub').textContent=S.fserver?(s?.host||''):`${S.fleet.filter(x=>!OFF.includes(x.state)).length} servers online`;
  const m=s?.m||{},chip=(l,p)=>p==null?'':`<div class="chip">${l} <div class="mbar"><i style="width:${clamp(p)}%;background:${pgrad(p)}"></i></div><b>${clamp(p)}%</b></div>`;
  $('#lmetrics').innerHTML=S.fserver?chip('CPU',cpuPct(m))+chip('MEM',m.mem)+chip('DISK',m.disk)+(m.uptime?`<div class="chip">Uptime <b>${fmtUp(m.uptime)}</b></div>`:'')+(m.ver&&S.page==='docker'?`<div class="chip">Docker <b>${esc(m.ver)}</b></div>`:''):'';
  modeCounts();
}
async function scan(server){
  const b=$('#rescan');b.disabled=true;b.innerHTML='<span class="spin"></span> Scanning';
  if(!S.containers.some(c=>!server||c.server===server))$('#rows').innerHTML=`<div class="grp">${'<div class="sk" style="height:52px;margin:8px;border-radius:10px"></div>'.repeat(6)}</div>`;
  try{const j=await api('/api/scan',{server});mergeFleet(j.servers);S.containers=j.containers;
    const ks=new Set(S.containers.map(c=>c.server+'/'+c.name));[...S.picked].forEach(k=>ks.has(k)||S.picked.delete(k))}
  catch(e){toast(esc(e.message),'bad')}
  finally{b.disabled=false;b.innerHTML=ic('restart',15)+' Refresh'}
  renderLHead();renderRows();
  if(S.sel&&S.sel.kind!=='pm2'){const c=S.containers.find(c=>c.server===S.sel.server&&c.name===S.sel.name);if(c){S.sel=c;renderHead()}}
}
function scoped(){return S.containers.filter(c=>!S.fserver||c.server===S.fserver)}
function filtered(){
  const q=$('#q').value.toLowerCase().trim().split(/\s+/).filter(Boolean);
  return scoped().filter(c=>(S.filter==='all'||(S.filter==='run')===c.running)&&(!$('#fcomp').checked||c.files.length)
    &&q.every(w=>(c.server+' '+c.name+' '+c.image+' '+c.project+' '+c.workdir+' '+c.ports).toLowerCase().includes(w)));
}
function shortStatus(st){let m;
  if((m=st.match(/^Up (.+?)( \((healthy|unhealthy|health: starting)\))?$/)))return{t:'up '+m[1].replace('About ','~'),h:m[3]};
  if((m=st.match(/^Exited \((\d+)\) (.+)$/)))return{t:`exit ${m[1]} · ${m[2]}`};return{t:st}}
function portsOf(p){const out=[],seen=new Set();for(const m of (p||'').matchAll(/:(\d+)->(\d+)\/(tcp|udp)/g)){const k=m[1]+'>'+m[2];if(!seen.has(k)){seen.add(k);out.push(`:${m[1]}→${m[2]}`)}}return out}
function bar(l,p,txt){const w=clamp(p);return `<div class="bar"><span>${l}</span><div class="t"><i style="width:${Math.max(w,p>0?2:0)}%;background:${pgrad(w)}"></i></div><em>${txt??(p.toFixed(1)+'%')}</em></div>`}
function groupItems(k){return filtered().filter(c=>(S.fserver?'':c.server+'  ›  ')+(c.project||'Standalone containers')===k)}
function renderRows(quiet){
  renderBulk();
  if(S.mode==='pm2')return renderPm2Rows(quiet);
  if(S.dtab!=='containers')return renderRes();
  const sc=scoped();$('#c-all').textContent=sc.length;$('#dt-containers').textContent=sc.length;$('#c-run').textContent=sc.filter(c=>c.running).length;$('#c-stop').textContent=sc.filter(c=>!c.running).length;
  const rows=filtered(),groups=new Map();
  rows.forEach(c=>{const k=(S.fserver?'':c.server+'  ›  ')+(c.project||'Standalone containers');if(!groups.has(k))groups.set(k,[]);groups.get(k).push(c)});
  if(!rows.length){$('#rows').innerHTML=sc.length||!scanning?`<div class="empty"><div class="bigic">${ic('search',22)}</div><b>No containers match</b><div>Try a different filter.</div></div>`:'';return}
  $('#rows').innerHTML=[...groups].map(([k,arr],gi)=>{const run=arr.filter(c=>c.running).length,col=S.collapsed[k];
    const allp=arr.every(c=>S.picked.has(c.server+'/'+c.name));
    return `<div class="grp ${quiet?'':'anim'}" style="animation-delay:${Math.min(gi,8)*40}ms"><div class="ghead" data-g="${esc(k)}"><input type="checkbox" class="ck" data-gpick="${esc(k)}" ${allp?'checked':''} title="Select all in this group"><span class="chev ${col?'':'open'}">${ic('chev',14)}</span><span class="gico">${ic(arr[0].project?'box':'term',14)}</span>
      <b>${esc(k)}</b><span class="dim mono small" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">${esc(arr[0].project?arr[0].workdir:'')}</span><span class="grow"></span>
      <span class="gcount"><span style="color:var(--ok)">●</span> ${run}/${arr.length}</span></div>
      ${col?'':arr.map(row).join('')}</div>`}).join('');
}
function row(c){
  const st=S.stats[c.server+'/'+c.name],ss=shortStatus(c.status),ports=portsOf(c.ports),sel=S.sel&&S.sel.server===c.server&&S.sel.name===c.name;
  const pk=c.server+'/'+c.name;
  return `<div class="crow dk ${sel?'sel':''} ${S.picked.has(pk)?'picked':''}" data-s="${esc(c.server)}" data-n="${esc(c.name)}">
    <div><input type="checkbox" class="ck" data-pick="${esc(pk)}" ${S.picked.has(pk)?'checked':''} title="Select"></div>
    <div><span class="spill ${c.running?'run':'stop'}"><i></i>${c.running?'RUNNING':'STOPPED'}</span></div>
    <div class="c-name"><b title="${esc(c.name)}">${esc(c.name)}</b><span title="${esc(c.image)}">${esc(c.image)}</span></div>
    <div class="c-ports ports">${ports.slice(0,3).map(p=>`<span class="port">${p}</span>`).join('')}${ports.length>3?`<span class="dim small">+${ports.length-3}</span>`:''}</div>
    <div class="c-up">${esc(ss.t)}${ss.h==='healthy'?'<span class="hb ok">healthy</span>':ss.h==='unhealthy'?'<span class="hb bad">unhealthy</span>':''}</div>
    <div class="bars">${st?bar('CPU',st.cpu)+bar('MEM',st.memp,st.mem.split('/')[0].trim()):c.running&&S.liveTimer?'<span class="dim small"><span class="spin" style="width:9px;height:9px"></span> stats…</span>':''}</div>
  </div>`}
$('#rows').addEventListener('click',e=>{
  if(S.mode!=='docker'||S.dtab!=='containers')return;
  const pk=e.target.closest('[data-pick]');if(pk){e.stopPropagation();S.picked.has(pk.dataset.pick)?S.picked.delete(pk.dataset.pick):S.picked.add(pk.dataset.pick);renderRows(true);return}
  const gp=e.target.closest('[data-gpick]');if(gp){e.stopPropagation();const arr=groupItems(gp.dataset.gpick),all=arr.every(c=>S.picked.has(c.server+'/'+c.name));
    arr.forEach(c=>all?S.picked.delete(c.server+'/'+c.name):S.picked.add(c.server+'/'+c.name));renderRows(true);return}
  const g=e.target.closest('[data-g]');if(g){S.collapsed[g.dataset.g]=!S.collapsed[g.dataset.g];renderRows();return}
  const r=e.target.closest('.crow');if(r){const c=S.containers.find(x=>x.server===r.dataset.s&&x.name===r.dataset.n);if(c)select(c)}
});
$('#q').addEventListener('input',renderRows);$('#fcomp').addEventListener('change',renderRows);
$('#fseg').onclick=e=>{const b=e.target.closest('[data-f]');if(!b)return;S.filter=b.dataset.f;$$('#fseg button').forEach(x=>x.classList.toggle('on',x===b));renderRows()};
$('#rescan').onclick=()=>S.mode==='pm2'?pm2Scan():S.dtab!=='containers'?loadRes(S.dtab):scan(S.fserver||undefined);

// ---------- PM2 ----------
$$('#mseg .mi,#ptabs .mi').forEach(x=>x.innerHTML=ic(x.dataset.i,14));
const fmtB=b=>{if(b==null)return'–';const u=['B','KB','MB','GB','TB'];let i=0;while(b>=1024&&i<4){b/=1024;i++}return (i?b.toFixed(b<10?1:0):b)+' '+u[i]};
const pRun=p=>p.status==='online'||p.status==='launching';
const pKey=p=>p.user+'|'+p.home+'|'+p.id;
function applyMode(m){S.mode=m;$$('#mseg button').forEach(b=>b.classList.toggle('on',b.dataset.m===m));
  $('#lcomp').classList.toggle('hidden',m==='pm2');$('#pmusers').classList.toggle('hidden',m!=='pm2');$('#pmlogs').classList.toggle('hidden',m!=='pm2');
  if(m==='pm2'&&S.dtab!=='containers')setDtab('containers');$('#dtabs').classList.toggle('hidden',m!=='docker'||!S.fserver);renderBulk();
  $('#q').placeholder=m==='pm2'?'Filter PM2 processes, users, scripts…':'Filter containers, images, projects…'}
$('#mseg').onclick=async e=>{const b=e.target.closest('[data-m]');if(!b||b.dataset.m===S.mode)return;
  if(S.sel&&!await closeDetail())return;applyMode(b.dataset.m);renderRows();
  if(S.mode==='pm2'&&!S.pm2[S.fserver])pm2Scan()};
function pm2Data(){return S.pm2[S.fserver]}
// which users' PM2 to show: chosen list, or automatic = users that have processes (+ locked/error ones, count unknown)
function pmVisible(d){return d.daemons.filter(dm=>d.users?d.users.includes(dm.user):(dm.procs.length||dm.locked||dm.error))}
function pm2Procs(){const d=pm2Data();if(!d)return[];
  return pmVisible(d).flatMap(dm=>dm.procs.map(p=>({...p,server:S.fserver,user:dm.user,home:dm.home,mine:dm.mine})))}
function modeCounts(){const d=pm2Data(),sv=S.fleet.find(x=>x.name===S.fserver);
  $('#m-docker').textContent=S.fserver?scoped().length:'·';
  $('#m-pm2').textContent=d?pm2Procs().length:(sv?.m?.pm2?'…':'0');
  $('#mseg').classList.add('hidden')}
async function pm2Scan(quiet){const srv=S.fserver;if(!srv)return;const b=$('#rescan'),mine=S.mode==='pm2'&&!quiet;
  if(mine){b.disabled=true;b.innerHTML='<span class="spin"></span> Scanning'}
  try{const j=await raw('/api/pm2',{server:srv});if(!j.ok)throw new Error(j.error);S.pm2[srv]=j.pm2;if(j.servers)mergeFleet(j.servers)}
  catch(e){if(S.mode==='pm2'&&!quiet)toast(esc(e.message),'bad');if(!S.pm2[srv])S.pm2[srv]={me:'',daemons:[],error:e.message}}
  finally{if(mine){b.disabled=false;b.innerHTML=ic('restart',15)+' Refresh'}}
  if(S.fserver!==srv)return;modeCounts();
  if(S.mode==='pm2'&&S.view==='list'){renderRows(quiet);pRefreshSel();
    if(S.pendingP){const p=pm2Procs().find(x=>x.user===S.pendingP.user&&x.name===S.pendingP.name);S.pendingP=null;if(p)pselect(p)}}}
function pRefreshSel(){if(!S.sel||S.sel.kind!=='pm2')return;const p=pm2Procs().find(x=>pKey(x)===pKey(S.sel));if(p){S.sel={...p,kind:'pm2'};renderPHead();updPGauges()}}
function role(dm){return dm.mine?'<span class="urole me">you</span>':dm.user==='root'?'<span class="urole root">root · sudo</span>':'<span class="urole">via sudo</span>'}
function renderPm2Rows(quiet){
  const d=pm2Data(),all=pm2Procs();
  $('#c-all').textContent=all.length;$('#c-run').textContent=all.filter(pRun).length;$('#c-stop').textContent=all.filter(p=>!pRun(p)).length;modeCounts();
  if(!d){$('#rows').innerHTML=`<div class="grp">${'<div class="sk" style="height:52px;margin:8px;border-radius:10px"></div>'.repeat(4)}</div>`;return}
  if(d.error&&!d.daemons.length){$('#rows').innerHTML=`<div class="empty"><div class="bigic">${ic('alert',22)}</div><b>Could not check PM2</b><div>${esc(d.error)}</div></div>`;return}
  if(!d.daemons.length){$('#rows').innerHTML=`<div class="empty"><div class="bigic">${ic('bolt',22)}</div><b>No PM2 running on this server</b><div>No PM2 daemon found for any user.</div></div>`;return}
  const q=$('#q').value.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const ok=p=>(S.filter==='all'||(S.filter==='run')===pRun(p))&&q.every(w=>(p.name+' '+p.user+' '+(p.script||'')+' '+(p.cwd||'')+' '+p.status).toLowerCase().includes(w));
  const vis=pmVisible(d),hid=d.daemons.length-vis.length;
  $('#rows').innerHTML=vis.map((dm,gi)=>{const k='pm2:'+dm.user+dm.home,col=S.collapsed[k];
    const procs=dm.procs.map(p=>({...p,server:S.fserver,user:dm.user,home:dm.home,mine:dm.mine})),shown=procs.filter(ok);
    if(q.length&&!shown.length&&!dm.locked&&!dm.error)return'';
    let body='';
    if(dm.locked)body=`<div class="lockrow">${ic('lock',16)}<span>PM2 of <b>${esc(dm.user)}</b> needs your <b>sudo</b> password to view.</span><button class="btn primary" data-unlock="1">${ic('lock',14)} Unlock</button></div>`;
    else if(dm.error)body=`<div class="lockrow">${ic('alert',16)}<span>${esc(dm.error)}</span></div>`;
    else if(!procs.length)body=`<div class="lockrow"><span>No processes in this PM2.</span></div>`;
    else body=shown.map(prow).join('')||`<div class="lockrow"><span>No processes match.</span></div>`;
    return `<div class="grp ${quiet?'':'anim'}" style="animation-delay:${Math.min(gi,8)*40}ms"><div class="ghead" data-g="${esc(k)}"><span class="chev ${col?'':'open'}">${ic('chev',14)}</span><span class="gico">${ic(dm.user==='root'?'lock':'bolt',14)}</span>
      <b>${esc(dm.user)}</b>${role(dm)}<span class="dim mono small" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">${esc(dm.home)} · pm2 ${esc(dm.pm2ver)}</span><span class="grow"></span>
      ${dm.locked?'':`<span class="gcount"><span style="color:var(--ok)">●</span> ${procs.filter(pRun).length}/${procs.length}</span>`}</div>
      ${col?'':body}</div>`}).join('')+(hid?`<div class="hidnote">${ic('info',14)} ${hid} PM2 user${hid>1?'s':''} hidden (${d.daemons.filter(x=>!vis.includes(x)).map(x=>esc(x.user)).join(', ')}) — ${d.users?'not selected':'no processes'} · <a data-pmusers="1">Manage users</a></div>`:'')
    ||`<div class="empty"><div class="bigic">${ic('search',22)}</div><b>No processes match</b><div>Try a different filter.</div></div>`;
}
function pStatus(p){const r=pRun(p),e=p.status==='errored';return `<span class="spill ${r?'run':e?'err':'stop'}"><i></i>${esc((p.status||'?').toUpperCase())}</span>`}
function pUp(p){return pRun(p)&&p.uptime?'up '+fmtUp((Date.now()-p.uptime)/1000):esc(p.status||'')}
function prow(p){const sel=S.sel&&S.sel.kind==='pm2'&&pKey(S.sel)===pKey(p),cl=(p.mode||'').startsWith('cluster');
  return `<div class="crow ${sel?'sel':''}" data-pk="${esc(pKey(p))}">
    <div>${pStatus(p)}</div>
    <div class="c-name"><b title="${esc(p.name)}">${esc(p.name)} <span class="dim small">#${p.id}</span></b><span title="${esc(p.script)}">${esc(p.script||'')}</span></div>
    <div class="c-ports ports"><span class="port">${cl?'cluster':'fork'}</span>${p.pid?`<span class="dim small">pid ${p.pid}</span>`:''}</div>
    <div class="c-up">${pUp(p)}${p.restarts?`<span class="hb ${p.restarts>20?'bad':''}" title="restarts">↺ ${p.restarts}</span>`:''}</div>
    <div class="bars">${pRun(p)?bar('CPU',p.cpu||0)+`<div class="bar"><span>MEM</span><em class="memtxt" style="text-align:left">${fmtB(p.mem)}</em></div>`:''}</div>
  </div>`}
$('#rows').addEventListener('click',async e=>{if(S.mode!=='pm2')return;
  if(e.target.closest('[data-pmusers]')){e.stopPropagation();return pmUsers()}
  if(e.target.closest('[data-unlock]')){e.stopPropagation();if(await askSudo(S.fserver))pm2Scan();return}
  const r=e.target.closest('[data-pk]');if(r){const p=pm2Procs().find(x=>pKey(x)===r.dataset.pk);if(p)pselect(p)}},true);

$('#pmusers').innerHTML=ic('lock',15)+' PM2 users';$('#pmusers').onclick=()=>pmUsers();
function pmUsers(){const d=pm2Data();if(!d||!d.daemons.length){toast('No PM2 found on this server','info');return}
  let auto=!d.users;
  const info=dm=>dm.skip?'<span class="dim">not loaded</span>':dm.locked?`<span style="color:var(--warn)">${ic('lock',12)} locked</span>`:dm.error?'<span style="color:var(--bad)">error</span>':`<b>${dm.procs.length}</b> process${dm.procs.length==1?'':'es'}`;
  modal(`<div class="form"><h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('lock',18)}</span> PM2 users on ${esc(S.fserver)}</h3>
    <p style="margin-top:-4px">Choose whose PM2 to show. You log in as <b>${esc(d.me||'you')}</b>; other users are read with sudo.</p>
    <div class="seg" style="width:100%"><button data-um="auto" class="${auto?'on':''}" style="flex:1;justify-content:center">Automatic</button><button data-um="pick" class="${auto?'':'on'}" style="flex:1;justify-content:center">Only selected users</button></div>
    <div id="um_note" class="mut small" style="margin-top:8px"></div>
    <div class="pick" id="um_pick" style="margin-top:8px">${d.daemons.map(dm=>`<label class="pk"><input type="checkbox" value="${esc(dm.user)}" ${(d.users?d.users.includes(dm.user):(dm.procs.length||dm.locked||dm.error))?'checked':''}>
      <div><b>${esc(dm.user)} ${dm.mine?'<span class="urole me">you</span>':''}</b><span class="mono">${esc(dm.home)}</span></div><span class="small">${info(dm)}</span></label>`).join('')}</div>
    <div id="um_err" class="err" style="margin-top:8px"></div>
    <div class="row end" style="margin-top:8px"><button class="btn ghost" id="mno">Cancel</button><button class="btn primary" id="myes">${ic('check',15)} Save</button></div></div>`);
  const upd=()=>{$$('#mbox [data-um]').forEach(b=>b.classList.toggle('on',(b.dataset.um==='auto')===auto));
    $('#um_note').textContent=auto?'Shows users that have PM2 processes. Empty PM2s are hidden.':'Only the ticked users are shown and read — the others are not touched (no sudo needed for them).';
    $$('#um_pick input').forEach(i=>i.disabled=auto);$('#um_pick').style.opacity=auto?.55:1};
  $$('#mbox [data-um]').forEach(b=>b.onclick=()=>{auto=b.dataset.um==='auto';upd()});upd();
  $('#mno').onclick=closeModal;
  $('#myes').onclick=async()=>{const users=auto?null:$$('#um_pick input:checked').map(i=>i.value);
    if(!auto&&!users.length){$('#um_err').textContent='Tick at least one user, or choose Automatic';return}
    $('#myes').disabled=true;$('#um_err').innerHTML='<span class="spin"></span>';
    try{const j=await api('/api/pm2_users',{server:S.fserver,users});S.pm2[S.fserver]=j.pm2;closeModal();renderRows();toast(auto?'Showing users that have processes':`Showing ${users.map(esc).join(', ')}`,'ok')}
    catch(e){$('#um_err').textContent=e.message;$('#myes').disabled=false}};
}
function pselect(p){stopLogs();S.sel={...p,kind:'pm2'};S.file=null;S.tab='overview';
  $('#detail').classList.remove('hidden');$('#scrim').classList.remove('hidden');renderRows(true);renderPDetail()}
function renderPDetail(){
  $('#detail').innerHTML=`<div class="dr-head"><div class="dr-ico"></div><div class="grow" id="dh" style="min-width:0"></div>
      <button class="icon-btn" id="dclose" title="Close (Esc)">${ic('x',18)}</button></div>
    <div class="dr-actions">
      <button class="act" data-p="restart">${ic('restart',15)}<span>Restart</span></button>
      <button class="act" data-p="reload" title="zero-downtime reload (cluster mode)">${ic('bolt',15)}<span>Reload</span></button>
      <button class="act danger" data-p="stop">${ic('stop',13)}<span>Stop</span></button>
      <button class="act" data-p="start">${ic('play',13)}<span>Start</span></button>
      <span class="vsep"></span>
      <button class="act" data-p="flush">${ic('x',14)}<span>Flush logs</span></button>
      <button class="act accent" id="psave" title="pm2 save — restore this list on reboot">${ic('download',15)}<span>pm2 save</span></button>
    </div>
    <div class="dgauges" id="dg"></div>
    <pre id="out" class="term hidden"></pre>
    <nav class="tabs">${[['overview','info'],['logs','logs']].map(([t,i])=>`<a data-t="${t}" class="${t===S.tab?'on':''}">${ic(i,14)} ${t[0].toUpperCase()+t.slice(1)}</a>`).join('')}<i class="ink"></i></nav>
    <div id="tabbody"></div>`;
  renderPHead();updPGauges();
  $('#dclose').onclick=()=>closeDetail();
  $$('#detail [data-p]').forEach(b=>b.onclick=()=>pAction(b,b.dataset.p));
  $('#psave').onclick=e=>pSave(e.currentTarget);
  $$('#detail .tabs a').forEach(a=>a.onclick=()=>{stopLogs();S.tab=a.dataset.t;$$('#detail .tabs a').forEach(x=>x.classList.toggle('on',x===a));moveInk();pTab()});
  requestAnimationFrame(moveInk);pTab();
}
function pTab(){({overview:pOverview,logs:pLogs})[S.tab]($('#tabbody'),S.sel)}
function renderPHead(){const p=S.sel,dh=$('#dh');if(!dh)return;
  dh.innerHTML=`<h2>${esc(p.name)} <span class="dim" style="font-weight:500">#${p.id}</span></h2><div class="dr-sub">${pStatus(p)}<span>${pUp(p)}</span><span>${ic('server',13)} ${esc(p.server)}</span><span>${ic('lock',13)} ${esc(p.user)}</span></div>`;
  const ico=$('#detail .dr-ico');if(ico){ico.className='dr-ico '+(pRun(p)?'run':'stop');ico.innerHTML=ic('bolt',24)}}
function updPGauges(){const p=S.sel,dg=$('#dg');if(!dg)return;
  const g=(l,v,suf,pc)=>`<div class="g"><div class="l">${l}</div><div class="v">${v}<small>${suf}</small></div>${pc!=null?`<div class="t"><i style="width:${clamp(pc)}%;background:${pgrad(pc)}"></i></div>`:''}</div>`;
  const [mv,mu]=fmtB(p.mem).split(' ');
  dg.innerHTML=(pRun(p)?g('CPU',(p.cpu||0).toFixed(1),'%',p.cpu||0)+g('Memory',mv,' '+(mu||'')):'')+g('Restarts',p.restarts||0,p.unstable?` · ${p.unstable} unstable`:'')+g(pRun(p)?'Uptime':'Status',pRun(p)?fmtUp((Date.now()-p.uptime)/1000):esc(p.status),'')}
function pArgs(p){return{server:p.server,user:p.user,home:p.home,id:p.id}}
async function pAction(btn,a){const p=S.sel;
  if(a!=='start'&&!await confirmBox(`<b>pm2 ${a}</b> <code>${esc(p.name)}</code> (user <b>${esc(p.user)}</b>) on <b>${esc(p.server)}</b>?`,a[0].toUpperCase()+a.slice(1),a==='stop'||a==='flush'))return;
  const head=`<span class="p">$ pm2 ${a} ${p.id}</span>  <span class="ts">${esc(p.user)}@${esc(p.server)}</span>\n`;out(head+'<span class="spin"></span> running…');
  await busy(btn,async()=>{try{const j=await api('/api/pm2_action',{...pArgs(p),action:a});
      if(j.pm2)S.pm2[p.server]=j.pm2;
      out(head+colorize(j.output||'')+`\n<span class="${j.rc?'e':'ok'}">${j.rc?'✗ exit '+j.rc:'✓ done'}</span>`);
      toast(`${esc(p.name)}: ${a} ${j.rc?'failed':'done'}`,j.rc?'bad':'ok')}
    catch(e){out(head+`<span class="e">${esc(e.message)}</span>`);toast(esc(e.message),'bad')}});
  renderRows(true);pRefreshSel();if(S.tab==='logs')pTab()}
async function pSave(btn){const p=S.sel;
  if(!await confirmBox(`Run <code>pm2 save</code> for user <b>${esc(p.user)}</b> on <b>${esc(p.server)}</b>?<br><br><span class="dim">Saves the current process list (online + stopped) so PM2 restores it after a reboot.</span>`,'Save'))return;
  await busy(btn,async()=>{try{const j=await api('/api/pm2_save',pArgs(p));out(`<span class="p">$ pm2 save</span>\n${colorize(j.output||'')}`);toast('pm2 list saved','ok')}
    catch(e){toast(esc(e.message),'bad')}})}
function pOverview(b,p){
  const cp=v=>`<span class="copy" data-copy="${esc(v)}" title="copy">${ic('copy',13)}</span>`;
  const pm=p.mine?`pm2`:`sudo -u ${p.user} PM2_HOME=${p.home} pm2`;
  const term=`ssh -t ${p.server} "${pm} logs ${p.id}"`;
  const row=(k,v)=>v===''||v==null?'':`<div>${k}</div><div>${v}</div>`;
  b.innerHTML=`<div class="kv">
   ${row('Script',p.script?`<code>${esc(p.script)}</code>${cp(p.script)}`:'')}
   ${row('Directory',p.cwd?`<code>${esc(p.cwd)}</code>${cp(p.cwd)}`:'')}
   ${row('Arguments',p.args?`<code>${esc(p.args)}</code>`:'')}
   ${row('Runs as',`<b>${esc(p.user)}</b> <span class="dim">· PM2_HOME</span> <code>${esc(p.home)}</code>`)}
   ${row('Mode',`${esc((p.mode||'').replace('_mode',''))}${p.instances>1?` · ${p.instances} instances`:''} <span class="dim">· pid</span> ${p.pid||'–'}`)}
   ${row('Interpreter',`${esc(p.interp||'')}${p.node?` <span class="dim">· node</span> ${esc(p.node)}`:''}`)}
   ${row('Version',p.version&&p.version!=='N/A'?esc(p.version):'')}
   ${row('Auto restart',(p.autorestart?'yes':'no')+(p.maxmem?` <span class="dim">· max memory</span> ${fmtB(+p.maxmem||0)}`:'')+(p.cron?` <span class="dim">· cron</span> <code>${esc(p.cron)}</code>`:'')+(p.watch?' <span class="dim">· watching files</span>':''))}
   ${row('Started',p.created?new Date(p.created).toLocaleString():'')}
   ${row('Out log',p.out?`<code>${esc(p.out)}</code>${cp(p.out)}`:'')}
   ${row('Error log',p.err?`<code>${esc(p.err)}</code>${cp(p.err)}`:'')}
   ${row('Terminal',`<code>${esc(term)}</code>${cp(term)}`)}
  </div>
  <div class="row"><button class="btn primary" id="golog">${ic('logs',15)} View logs</button></div>`;
  b.querySelectorAll('[data-copy]').forEach(x=>x.onclick=()=>{navigator.clipboard?.writeText(x.dataset.copy).then(()=>toast('Copied to clipboard','ok',1500),()=>toast('Copy failed','bad'))});
  $('#golog').onclick=()=>$('#detail [data-t=logs]').click();
}
function pLogs(b,p){
  b.innerHTML=`<div class="row"><div class="seg" id="lstream"><button data-s="all" class="on">Both</button><button data-s="stdout">Out</button><button data-s="stderr">Error</button></div>
   <select id="ltail"><option value="100">Last 100</option><option value="300" selected>Last 300</option><option value="1000">Last 1000</option><option value="5000">Last 5000</option></select>
   <div class="search" style="min-width:140px"><span>${ic('search',14)}</span><input id="lgrep" placeholder="Filter lines…"></div>
   <label class="toggle" title="show only lines that look like errors"><input type="checkbox" id="lerr"><span></span>Errors only</label>
   <label class="toggle"><input type="checkbox" id="lauto"><span></span>Follow</label><button class="btn" id="lref">${ic('restart',14)}</button></div>
   <pre id="logs" class="term chrome" style="flex:1;min-height:340px"><span class="spin"></span></pre>`;
  let L=[],stream='all';
  const show=()=>{const g=$('#lgrep')?.value.toLowerCase(),el=$('#logs');if(!el)return;const atBottom=el.scrollHeight-el.scrollTop-el.clientHeight<60;
    const parts=L.filter(l=>stream==='all'||l.name===stream).sort((a,b)=>(a.name==='stderr'?0:1)-(b.name==='stderr'?0:1));
    if(!parts.length){el.innerHTML='<span class="dim">(no log files)</span>';return}
    const eo=$('#lerr')?.checked;
    el.innerHTML=parts.map(l=>{let t=l.text.replace(/\n$/,'');if(g||eo)t=t.split('\n').filter(x=>(!g||x.toLowerCase().includes(g))&&(!eo||ERR_RE.test(x))).join('\n');
      return `<span class="p">── ${l.name==='stderr'?'error':'out'} · ${esc(l.path)} ──</span>\n${t?colorHL(t,$('#lgrep').value,l.name==='stderr'):'<span class="dim">(nothing'+(g||eo?' matching':'')+')</span>'}`}).join('\n\n');
    if(atBottom||!el.dataset.init){el.scrollTop=el.scrollHeight;el.dataset.init=1}};
  const load=async()=>{try{const j=await api('/api/pm2_logs',{...pArgs(p),tail:$('#ltail').value});L=j.logs||[];show()}catch(e){const el=$('#logs');if(el)el.innerHTML=`<span class="e">${esc(e.message)}</span>`}};
  $('#lstream').onclick=e=>{const x=e.target.closest('[data-s]');if(!x)return;stream=x.dataset.s;$$('#lstream button').forEach(y=>y.classList.toggle('on',y===x));show()};
  $('#lref').onclick=load;$('#ltail').onchange=load;$('#lgrep').oninput=show;$('#lerr').onchange=show;
  $('#lauto').onchange=e=>{stopLogs();if(e.target.checked){S.logTimer=setInterval(load,3000);load()}};
  load();
}

// ---------- shared helpers ----------
const ERR_RE=/\b(error|err!|fatal|panic|exception|failed|unhandled|refused|denied|ECONN\w*|ETIMEDOUT|EADDRINUSE|TypeError|ReferenceError|SyntaxError)\b/i;
function colorHL(t,g,allErr){const re=g?new RegExp(esc(g).replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'gi'):null;
  return t.split('\n').map(l=>{let e=esc(l);if(re)e=e.replace(re,m=>`<mark>${m}</mark>`);
    if(allErr||ERR_RE.test(l))return`<span class="e">${e}</span>`;if(/\b(warn|warning|deprecated)\b/i.test(l))return`<span class="w">${e}</span>`;
    return e.replace(/^(\d{4}-\d\d-\d\d[T ][\d:.,]+Z?)/,'<span class="ts">$1</span>')}).join('\n')}
const fmtD=b=>{if(!b)return'–';const u=['B','kB','MB','GB','TB'];let i=0;while(b>=1000&&i<4){b/=1000;i++}return (i?b.toFixed(b<10?1:0):b)+' '+u[i]};
function typeConfirm(title,html,word,okLabel){return new Promise(res=>{
  modal(`<h3>${ic('alert',20)} ${title}</h3>${html}<div class="form"><label>Type <code>${esc(word)}</code> to confirm</label><input id="tcn" autocomplete="off" spellcheck="false"></div>
   <div class="row end" style="margin-top:6px"><button class="btn ghost" id="mno">Cancel</button><button class="btn danger" id="myes" disabled>${ic('trash',14)} ${esc(okLabel)}</button></div>`);
  $('#tcn').oninput=()=>{$('#myes').disabled=$('#tcn').value!==word};$('#tcn').focus();
  $('#tcn').onkeydown=e=>{if(e.key==='Enter'&&!$('#myes').disabled)$('#myes').click()};
  $('#mno').onclick=()=>{closeModal();res(false)};$('#myes').onclick=()=>{closeModal();res(true)}})}
function errModal(title,body){modal(`<h3>${ic('alert',20)} ${title}</h3><pre class="term" style="max-height:55vh;overflow:auto;white-space:pre-wrap">${body}</pre><div class="row end"><button class="btn" id="mno">Close</button></div>`);$('#mno').onclick=closeModal}

// ---------- bulk select (containers) ----------
function renderBulk(){const b=$('#bulkbar');if(!b)return;
  const res=S.view==='list'&&S.mode==='docker'&&S.dtab!=='containers'&&S.rpicked.size>0;
  const on=S.view==='list'&&S.mode==='docker'&&S.dtab==='containers'&&S.picked.size>0;
  if(!on&&!res){b.classList.add('hidden');return}
  if(b.dataset.busy)return;
  b.classList.remove('hidden');
  if(res){const n=S.rpicked.size,w={images:'image',volumes:'volume',networks:'network'}[S.dtab];
    b.innerHTML=`<b>${n} ${w}${n>1?'s':''} selected</b><button class="btn sm danger" data-ba="rdel">${ic('trash',13)} Delete</button><button class="btn ghost sm" data-ba="rclear">${ic('x',13)} Clear</button>`;return}
  b.innerHTML=`<b>${S.picked.size} selected</b><button class="btn sm" data-ba="restart">${ic('restart',13)} Restart</button><button class="btn sm" data-ba="stop">${ic('stop',12)} Stop</button>
    <button class="btn sm" data-ba="start">${ic('play',12)} Start</button><button class="btn sm danger" data-ba="remove">${ic('trash',13)} Remove</button>
    <button class="btn ghost sm" data-ba="clear" title="Clear selection">${ic('x',13)} Clear</button>`}
$('#bulkbar').onclick=e=>{const x=e.target.closest('[data-ba]');if(!x||$('#bulkbar').dataset.busy)return;
  if(x.dataset.ba==='clear'){S.picked.clear();renderRows(true);return}
  if(x.dataset.ba==='rclear'){S.rpicked.clear();renderRows(true);return}
  if(x.dataset.ba==='rdel')return resBulk();bulkDo(x.dataset.ba)};
async function resBulk(){const t=S.dtab,sv=S.fserver,r=S.res[sv+'|'+t];if(!r||!r.items)return;
  const items=r.items.filter(i=>S.rpicked.has(rkey(t,i))&&rcan(t,i)),ids=[...new Set(items.map(i=>rkey(t,i)))];if(!ids.length){S.rpicked.clear();renderRows(true);return}
  const w={images:'image',volumes:'volume',networks:'network'}[t]+(ids.length>1?'s':'');
  const warn=t==='volumes'?'<b>All data inside these volumes is lost forever.</b>':t==='images'?'Images used by stopped containers will be refused by docker until those containers are removed.':'';
  const list=`<div class="uchips" style="max-height:170px;overflow:auto;margin:10px 0">${ids.map(k=>`<span class="uchip" title="${esc(k)}">${esc(k.startsWith('sha256:')?k.slice(7,19):k)}</span>`).join('')}</div>`;
  if(!await typeConfirm(`Delete ${ids.length} ${w}`,`<p>Delete these ${w} on <b>${esc(sv)}</b>? This cannot be undone. ${warn}</p>${list}`,'delete',`Delete ${ids.length}`))return;
  const bb=$('#bulkbar');bb.dataset.busy=1;bb.innerHTML=`<span class="spin"></span> <b>Deleting ${ids.length} ${w}…</b>`;
  try{const j=await api('/api/dres_bulk',{server:sv,kind:t,ids,confirm:'delete'});const bad=j.results.filter(x=>x.rc!==0),good=j.results.length-bad.length;
    j.results.filter(x=>x.rc===0).forEach(x=>S.rpicked.delete(x.id));
    if(!bad.length)toast(`Deleted ${good} ${w}`,'ok');
    else errModal(`${good} deleted, ${bad.length} refused`,bad.map(x=>`<span class="e">✗ ${esc(x.id)}</span>\n${esc(x.output.trim())}`).join('\n\n'))}
  catch(e){toast(esc(e.message),'bad')}
  delete bb.dataset.busy;await loadRes(t);renderBulk()}
async function bulkDo(a){
  const items=[...S.picked].map(k=>S.containers.find(c=>c.server+'/'+c.name===k)).filter(Boolean);
  if(!items.length){S.picked.clear();renderRows(true);return}
  const n=items.length,pl=n>1?'s':'',run=items.filter(c=>c.running).length;
  const list=`<div class="uchips" style="max-height:150px;overflow:auto;margin:10px 0">${items.map(c=>`<span class="uchip ${c.running?'run':''}">${S.fserver?'':esc(c.server)+' › '}${esc(c.name)}</span>`).join('')}</div>`;
  let extra={};
  if(a==='remove'){
    const ok=await new Promise(res=>{
      modal(`<h3>${ic('alert',20)} Remove ${n} container${pl}</h3><p>These containers will be deleted. This cannot be undone. Images and named volumes stay.</p>${list}
       <div class="form">${run?`<label style="display:flex!important;gap:8px;align-items:center"><input type="checkbox" id="rmf" style="width:auto!important"> ${run} of them ${run>1?'are':'is'} <b>running</b>: force (stop + remove)</label>`:''}
       <label style="display:flex!important;gap:8px;align-items:center"><input type="checkbox" id="rmv" style="width:auto!important"> Also delete their anonymous volumes (<code>-v</code>)</label>
       <label>Type <code>delete</code> to confirm</label><input id="rmn" autocomplete="off" spellcheck="false"></div>
       <div class="row end" style="margin-top:6px"><button class="btn ghost" id="mno">Cancel</button><button class="btn danger" id="myes" disabled>${ic('trash',14)} Remove ${n}</button></div>`);
      const chk=()=>{$('#myes').disabled=!($('#rmn').value.trim()==='delete'&&(!run||$('#rmf').checked))};
      $('#rmn').oninput=chk;if($('#rmf'))$('#rmf').onchange=chk;$('#rmn').focus();
      $('#mno').onclick=()=>{closeModal();res(false)};
      $('#myes').onclick=()=>{extra={confirm:'delete',force:!!($('#rmf')&&$('#rmf').checked),volumes:$('#rmv').checked};closeModal();res(true)}});
    if(!ok)return;
  }else if(!await confirmBox(`<b>${a}</b> these ${n} container${pl}?${list}`,a[0].toUpperCase()+a.slice(1)+' '+n,a==='stop'))return;
  const bb=$('#bulkbar');bb.dataset.busy=1;bb.innerHTML=`<span class="spin"></span> <b>${a}: ${n} container${pl}…</b>`;
  try{const j=await api('/api/bulk',{action:a,items:items.map(c=>({server:c.server,name:c.name})),...extra});
    const bad=j.results.filter(r=>r.rc!==0),good=j.results.length-bad.length;
    if(a==='remove')j.results.filter(r=>r.rc===0).forEach(r=>S.picked.delete(r.server+'/'+r.name));
    if(!bad.length)toast(`${a}: ${good} container${good>1?'s':''} done`,'ok');
    else errModal(`${a}: ${good} done, ${bad.length} failed`,bad.map(r=>`<span class="e">✗ ${esc(r.server)} › ${esc(r.name)}</span>\n${esc(r.output.trim())}`).join('\n\n'));
  }catch(e){toast(esc(e.message),'bad')}
  delete bb.dataset.busy;await scan(S.fserver||undefined);renderBulk()}

// ---------- images / volumes / networks tabs ----------
$('#dtabs').onclick=async e=>{const a=e.target.closest('[data-dt]');if(!a||a.dataset.dt===S.dtab)return;if(S.sel&&!await closeDetail())return;setDtab(a.dataset.dt)};
function setDtab(t){S.dtab=t;S.rpicked.clear();$$('#dtabs a').forEach(x=>x.classList.toggle('on',x.dataset.dt===t));
  const c=t==='containers';$('#fseg').classList.toggle('hidden',!c);$('#llive').classList.toggle('hidden',!c);$('#lcomp').classList.toggle('hidden',!c||S.mode==='pm2');
  $('#q').value='';$('#q').placeholder=c?(S.mode==='pm2'?'Filter PM2 processes, users, scripts…':'Filter containers, images, projects…'):`Filter ${t}…`;
  if(!c&&S.fserver&&!S.res[S.fserver+'|'+t])loadRes(t);renderRows()}
function syncDtabs(){const show=S.mode==='docker'&&!!S.fserver;$('#dtabs').classList.toggle('hidden',!show);
  if(!show&&S.dtab!=='containers')setDtab('containers');
  ['images','volumes','networks'].forEach(t=>{const r=S.res[S.fserver+'|'+t];$('#dt-'+t).textContent=r&&r.items?r.items.length:'·'})}
async function loadRes(t,o={}){const sv=S.fserver,k=sv+'|'+t,prev=S.res[k]||{};
  if(prev.sizes&&o.sizes==null)o.sizes=1;
  S.res[k]={...prev,loading:true,error:null};if(S.dtab===t)renderRows(true);
  try{const j=await api('/api/dres',{server:sv,kind:t,...o});S.res[k]={items:j.items,sizes:!!o.sizes};
    if(S.fserver===sv&&S.dtab===t){const ok=new Set(j.items.filter(i=>rcan(t,i)).map(i=>rkey(t,i)));[...S.rpicked].forEach(x=>ok.has(x)||S.rpicked.delete(x))}}catch(e){S.res[k]={...prev,loading:false,error:e.message}}
  if(S.fserver===sv){$('#dt-'+t).textContent=S.res[k].items?S.res[k].items.length:'·';if(S.dtab===t)renderRows(true)}}
function chips(arr,run){return arr.length?`<div class="uchips">${arr.slice(0,4).map(x=>`<span class="uchip" title="${esc(x)}">${esc(x)}</span>`).join('')}${arr.length>4?`<span class="dim small">+${arr.length-4}</span>`:''}</div>`:'<span class="dim small">—</span>'}
function rkey(t,i){return t==='images'?((i.repo!=='<none>'&&i.tag!=='<none>')?i.repo+':'+i.tag:i.id):i.name}
function rcan(t,i){return t==='images'?!i.running:t==='volumes'?!i.used.length:!i.builtin&&!i.used.length}
function rck(t,i){const k=rkey(t,i),ok=rcan(t,i);return `<div><input type="checkbox" class="ck" data-rpick="${esc(k)}" ${S.rpicked.has(k)?'checked':''} ${ok?'':'disabled title="in use — can\'t delete"'}></div>`}
function renderRes(){const t=S.dtab,r=S.res[S.fserver+'|'+t];
  if(!r||(r.loading&&!r.items)){$('#rows').innerHTML=`<div class="grp">${'<div class="sk" style="height:44px;margin:8px;border-radius:10px"></div>'.repeat(5)}</div>`;return}
  if(r.error&&!r.items){$('#rows').innerHTML=`<div class="empty"><div class="bigic">${ic('alert',22)}</div><b>Could not load ${t}</b><div>${esc(r.error)}</div></div>`;return}
  const q=$('#q').value.toLowerCase().trim().split(/\s+/).filter(Boolean),all=r.items;
  const its=all.map((x,i)=>[x,i]).filter(([x])=>q.every(w=>JSON.stringify(x).toLowerCase().includes(w)));
  const spin=r.loading?'<span class="spin"></span>':'';
  let sum='',head='',rows='';
  if(t==='images'){const free=all.filter(i=>!i.used.length),tb=all.reduce((a,i)=>a+i.bytes,0),fb=free.reduce((a,i)=>a+i.bytes,0);
    sum=`<span><b>${all.length}</b> images · ${fmtD(tb)}</span><span><span class="tag free">${free.length} unused</span> ${fmtD(fb)}</span>${spin}<span class="grow"></span><span class="dim small">Delete all unused at once from <a data-disk="1" style="cursor:pointer;color:#9d8bff">Disk cleanup</a></span>`;
    head=['Image','Size','Created','Used by containers',''];
    rows=its.map(([i,ix])=>{const nm=i.repo==='<none>'?'<untagged>':i.repo+':'+i.tag;
      return `<div class="rrow ${S.rpicked.has(rkey(t,i))?'picked':''}">${rck(t,i)}<div class="nm"><b title="${esc(nm)}">${esc(nm)}</b><span>${esc(i.id.replace('sha256:','').slice(0,12))}</span></div><span class="mono small">${esc(i.size)}</span><span class="dim small">${esc(i.created)}</span>
       <div>${i.used.length?chips(i.used):'<span class="tag free">unused</span>'}</div>
       <div style="text-align:right"><button class="btn sm danger" data-rm="${esc(i.id)}" data-kind="images" ${r.loading?'disabled':''} ${i.running?'disabled title="a running container uses this image"':''}>${ic('trash',13)} Delete</button></div></div>`}).join('')}
  else if(t==='volumes'){const free=all.filter(i=>!i.used.length);
    sum=`<span><b>${all.length}</b> volumes</span><span><span class="tag free">${free.length} not attached</span></span>${r.sizes?`<span>${fmtD(all.reduce((a,i)=>a+i.bytes,0))} total</span>`:`<button class="btn sm" data-sizes="1" ${r.loading?'disabled':''}>${ic('disk',13)} Show sizes</button>`}${spin}`;
    head=['Volume','Size','Status','Attached to',''];
    rows=its.map(([i,ix])=>`<div class="rrow ${S.rpicked.has(rkey(t,i))?'picked':''}">${rck(t,i)}<div class="nm"><b title="${esc(i.name)}">${esc(i.name.length>40?i.name.slice(0,12)+'…'+i.name.slice(-8):i.name)}</b><span>${esc(i.driver)}</span></div>
       <span class="mono small">${r.sizes?esc(i.size||'–'):'<span class="dim">–</span>'}</span><span>${i.used.length?(i.running?'<span class="tag use">in use</span>':'<span class="tag mut">stopped ctr</span>'):'<span class="tag free">not attached</span>'}</span>
       <div>${chips(i.used)}</div><div style="text-align:right"><button class="btn sm danger" data-rm="${esc(i.name)}" data-kind="volumes" ${r.loading?'disabled':''} ${i.used.length?'disabled title="attached to a container — remove the container first"':''}>${ic('trash',13)} Delete</button></div></div>`).join('')}
  else{sum=`<span><b>${all.length}</b> networks</span><span class="dim small">bridge, host and none are built in and can't be deleted</span>${spin}`;
    head=['Network','Subnet','Status','Containers',''];
    rows=its.map(([i,ix])=>`<div class="rrow ${S.rpicked.has(rkey(t,i))?'picked':''}">${rck(t,i)}<div class="nm"><b>${esc(i.name)}</b><span>${esc(i.driver)} · ${esc(i.scope)}</span></div><span class="mono small">${esc(i.subnet||'–')}</span>
       <span>${i.builtin?'<span class="tag mut">built-in</span>':i.used.length?'<span class="tag use">in use</span>':'<span class="tag free">unused</span>'}</span><div>${chips(i.used)}</div>
       <div style="text-align:right">${i.builtin?'':`<button class="btn sm danger" data-rm="${esc(i.name)}" data-kind="networks" ${r.loading?'disabled':''} ${i.used.length?'disabled title="containers are connected"':''}>${ic('trash',13)} Delete</button>`}</div></div>`).join('')}
  $('#rows').innerHTML=`<div class="grp"><div class="rsum">${sum}</div>${r.error?`<div class="note warn" style="margin:10px 16px">${esc(r.error)}</div>`:''}<div class="rrow h"><div>${(()=>{const ks=its.filter(([i])=>rcan(t,i)).map(([i])=>rkey(t,i));return ks.length?`<input type="checkbox" class="ck" data-rall="1" title="Select all that can be deleted" ${ks.every(k=>S.rpicked.has(k))?'checked':''}>`:''})()}</div>${head.map(h=>`<span>${h}</span>`).join('')}</div>${rows||`<div class="dim" style="padding:24px;text-align:center">Nothing ${q.length?'matches':'here'}</div>`}</div>`}
$('#rows').addEventListener('click',e=>{if(S.mode!=='docker'||S.dtab==='containers')return;
  if(e.target.closest('[data-disk]'))return openDisk(S.fserver);
  if(e.target.closest('[data-sizes]'))return loadRes(S.dtab,{sizes:1});
  const rp=e.target.closest('[data-rpick]');if(rp){e.stopPropagation();if(rp.disabled)return;const k=rp.dataset.rpick;S.rpicked.has(k)?S.rpicked.delete(k):S.rpicked.add(k);renderRows(true);return}
  if(e.target.closest('[data-rall]')){const r=S.res[S.fserver+'|'+S.dtab];if(!r||!r.items)return;const q=$('#q').value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    const ks=r.items.filter(i=>rcan(S.dtab,i)&&q.every(w=>JSON.stringify(i).toLowerCase().includes(w))).map(i=>rkey(S.dtab,i)),all=ks.every(k=>S.rpicked.has(k));
    ks.forEach(k=>all?S.rpicked.delete(k):S.rpicked.add(k));renderRows(true);return}
  const b=e.target.closest('[data-rm]');if(!b||b.disabled)return;const k=b.dataset.kind,r=S.res[S.fserver+'|'+k];if(!r||r.loading||k!==S.dtab)return;
  const it=r.items.find(i=>(k==='images'?i.id:i.name)===b.dataset.rm);if(it)resRm(k,it)});
async function resRm(t,i){const sv=S.fserver;let id,confirm;
  if(t==='images'){id=(i.repo!=='<none>'&&i.tag!=='<none>')?i.repo+':'+i.tag:i.id;
    if(!await confirmBox(`Delete image <code>${esc(i.repo==='<none>'?i.id.slice(7,19):id)}</code> (${esc(i.size)}) from <b>${esc(sv)}</b>?${i.used.length?`<br><br><span class="dim">Stopped container(s) ${i.used.map(esc).join(', ')} still use it, so docker will refuse until they are removed.</span>`:''}`,'Delete image',true))return}
  else if(t==='volumes'){id=confirm=i.name;
    if(!await typeConfirm('Delete volume',`<p>Delete volume <code>${esc(i.name)}</code> on <b>${esc(sv)}</b>? <b>All data inside it is lost forever.</b></p>`,i.name,'Delete volume'))return}
  else{id=i.name;if(!await confirmBox(`Delete network <code>${esc(i.name)}</code> on <b>${esc(sv)}</b>?`,'Delete network',true))return}
  try{const j=await api('/api/dres_rm',{server:sv,kind:t,id,confirm});
    if(j.rc)errModal('Docker refused',esc(j.output||('exit '+j.rc)));else toast(`Deleted ${esc(id.length>40?id.slice(0,40)+'…':id)}`,'ok')}
  catch(e){toast(esc(e.message),'bad')}
  loadRes(t)}

// ---------- systemd services ----------
const SV={timer:null};
function openSvc(server){Object.assign(SV,{server,f:'run',q:'',sel:null,mode:'status',items:null});clearInterval(SV.timer);
  modal(`<h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('cpu',18)}</span> Services · ${esc(server)}</h3>
   <div class="row" style="gap:8px;margin-top:6px;flex-wrap:wrap"><div class="search" style="flex:1;min-width:180px"><span>${ic('search',14)}</span><input id="svq" placeholder="Filter services… nginx, mysql, php-fpm"></div>
    <div class="seg" id="svf"><button data-f="run" class="on">Running <span id="sv-run">·</span></button><button data-f="failed">Failed <span id="sv-failed">·</span></button><button data-f="all">All <span id="sv-all">·</span></button></div>
    <button class="btn sm" id="svref" title="Refresh">${ic('restart',13)}</button></div>
   <div id="svlist" class="fblist" style="max-height:40vh"></div><div id="svpane"></div>
   <div class="row end" style="margin-top:10px"><button class="btn ghost" id="mno">Close</button></div>`,'xl');
  $('#mno').onclick=()=>{clearInterval(SV.timer);closeModal()};$('#svref').onclick=svLoad;
  $('#svq').oninput=e=>{SV.q=e.target.value.toLowerCase();svRender()};$('#svq').focus();
  $('#svf').onclick=e=>{const b=e.target.closest('[data-f]');if(!b)return;SV.f=b.dataset.f;$$('#svf button').forEach(x=>x.classList.toggle('on',x===b));svRender()};
  $('#svlist').onclick=e=>{const r=e.target.closest('[data-u]');if(!r)return;const a=e.target.closest('[data-sa]');
    if(a){e.stopPropagation();a.dataset.sa==='logs'?svShow(r.dataset.u,'logs'):svAct(r.dataset.u,a.dataset.sa,a)}else svShow(r.dataset.u,'status')};
  svLoad()}
const svRun=i=>i.active==='active'||i.active==='activating'||i.active==='reloading';
async function svLoad(){const el=$('#svlist');if(!el)return;if(!SV.items)el.innerHTML='<div class="mut" style="padding:24px;text-align:center"><span class="spin"></span></div>';
  try{SV.items=(await api('/api/svc',{server:SV.server})).items}catch(e){if($('#svlist'))$('#svlist').innerHTML=`<div class="note warn" style="margin:10px">${esc(e.message)}</div>`;return}
  const f=SV.items.filter(i=>i.active==='failed').length;
  if($('#sv-run')){$('#sv-run').textContent=SV.items.filter(svRun).length;$('#sv-failed').textContent=f;$('#sv-failed').style.color=f?'var(--bad)':'';$('#sv-all').textContent=SV.items.length}
  svRender()}
function svRender(){const el=$('#svlist');if(!el||!SV.items)return;
  const its=SV.items.filter(i=>(SV.f==='all'||(SV.f==='failed'?i.active==='failed':svRun(i)))&&(!SV.q||(i.unit+' '+i.desc).toLowerCase().includes(SV.q)))
    .sort((a,b)=>(a.active==='failed'?0:1)-(b.active==='failed'?0:1)||a.unit.localeCompare(b.unit));
  el.innerHTML=its.map(i=>`<div class="svrow ${SV.sel===i.unit?'on':''}" data-u="${esc(i.unit)}"><span class="d ${esc(i.active)}" title="${esc(i.active)} (${esc(i.sub)})"></span>
     <b title="${esc(i.unit)}">${esc(i.unit.replace(/\.service$/,''))}</b><span class="ds" title="${esc(i.desc)}">${esc(i.desc)}</span>
     <span><span class="tag ${i.enabled==='enabled'?'use':'mut'}" title="starts at boot?">${esc(i.enabled||'–')}</span></span>
     <span class="svacts"><button class="btn sm" data-sa="restart">${ic('restart',12)} Restart</button>${svRun(i)?`<button class="btn sm" data-sa="stop">${ic('stop',11)}</button>`:`<button class="btn sm" data-sa="start">${ic('play',11)}</button>`}<button class="btn sm" data-sa="logs">${ic('logs',12)} Logs</button></span></div>`).join('')
    ||`<div class="dim" style="padding:20px;text-align:center">${SV.f==='failed'&&!SV.q?'No failed services 👍':'No services match'}</div>`}
function svShow(u,mode){SV.sel=u;SV.mode=mode;clearInterval(SV.timer);svRender();
  $('#svpane').innerHTML=`<div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap"><b class="mono">${esc(u)}</b><div class="seg" id="svm"><button data-m="status" class="${mode==='status'?'on':''}">Status</button><button data-m="logs" class="${mode==='logs'?'on':''}">Logs</button></div>
    <span class="grow"></span><select id="svtail" class="${mode==='logs'?'':'hidden'}"><option value="100">Last 100</option><option value="300" selected>Last 300</option><option value="1000">Last 1000</option></select>
    <div class="search ${mode==='logs'?'':'hidden'}" style="min-width:150px"><span>${ic('search',14)}</span><input id="svgrep" placeholder="Filter lines…"></div>
    <label class="toggle ${mode==='logs'?'':'hidden'}"><input type="checkbox" id="svfol"><span></span>Follow</label><button class="btn sm" id="svpref">${ic('restart',13)}</button></div>
   <pre id="svout" class="term chrome" style="max-height:36vh;min-height:160px;overflow:auto;margin-top:8px"><span class="spin"></span></pre>`;
  let txt='';const show=()=>{const o=$('#svout');if(!o)return;const g=($('#svgrep')||{}).value||'';const atB=o.scrollHeight-o.scrollTop-o.clientHeight<60;
    o.innerHTML=colorHL(g?txt.split('\n').filter(l=>l.toLowerCase().includes(g.toLowerCase())).join('\n'):txt,g);if(mode==='logs'&&(atB||!o.dataset.init)){o.scrollTop=o.scrollHeight;o.dataset.init=1}};
  const load=async()=>{if(!$('#svout')){clearInterval(SV.timer);return}
    try{const j=await api('/api/svc_action',{server:SV.server,unit:u,action:mode,tail:($('#svtail')||{}).value});txt=j.output||'(empty)'}catch(e){txt='ERROR: '+e.message}show()};
  $('#svm').onclick=e=>{const b=e.target.closest('[data-m]');if(b&&b.dataset.m!==mode)svShow(u,b.dataset.m)};
  $('#svpref').onclick=load;if($('#svtail'))$('#svtail').onchange=load;if($('#svgrep'))$('#svgrep').oninput=show;
  $('#svfol').onchange=e=>{clearInterval(SV.timer);if(e.target.checked){SV.timer=setInterval(load,3000);load()}};
  load();setTimeout(()=>$('#svpane').scrollIntoView({block:'nearest',behavior:'smooth'}),50)}
async function svAct(u,a,btn){
  if(a!=='start'&&!await new Promise(res=>{const prev=$('#mbox').innerHTML;   // inline confirm inside the services window
      const bar=document.createElement('div');bar.className='note warn';bar.style.margin='10px 0';
      bar.innerHTML=`<b>${a}</b> <code>${esc(u)}</code> on <b>${esc(SV.server)}</b>? ${a==='stop'?'It stays stopped until started again.':'It will be briefly unavailable.'}
        <span style="float:right;display:flex;gap:6px"><button class="btn ghost sm" id="svno">Cancel</button><button class="btn sm ${a==='stop'?'danger':'primary'}" id="svyes">${a[0].toUpperCase()+a.slice(1)}</button></span>`;
      $('#svlist').before(bar);$('#svyes').focus();
      $('#svno').onclick=()=>{bar.remove();res(false)};$('#svyes').onclick=()=>{bar.remove();res(true)}}))return;
  if(btn){btn.disabled=true;btn.innerHTML='<span class="spin"></span>'}
  try{const j=await api('/api/svc_action',{server:SV.server,unit:u,action:a});
    toast(`${esc(u)}: ${a} ${j.rc?'failed':'done'} — ${esc((j.output.match(/now: (\S+)/)||[])[1]||'')}`,j.rc?'bad':'ok');
    if(j.rc&&$('#svlist')){SV.sel=u;svShow(u,'status');setTimeout(()=>{const o=$('#svout');if(o)o.insertAdjacentHTML('afterbegin',`<span class="e">$ systemctl ${a} ${esc(u)}\n${esc(j.output)}</span>\n\n`)},900)}}
  catch(e){toast(esc(e.message),'bad')}
  await svLoad();if(SV.sel===u&&!$('#svout'))svShow(u,'status')}

// ---------- PM2: watch many processes' logs together ----------
const PL={timer:null};
function pmAllLogs(){const procs=pm2Procs();if(!procs.length){toast('No PM2 processes here','info');return}
  clearInterval(PL.timer);PL.procs=procs;PL.data={};
  const def=procs.filter(p=>p.status==='errored'||(p.restarts||0)>0);PL.pick=new Set((def.length?def:procs).slice(0,12).map(pKey));
  modal(`<h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('logs',18)}</span> PM2 logs · ${esc(S.fserver)}</h3>
   <div class="rwrap"><div class="rleft" style="max-height:62vh;overflow:auto"><div class="row" style="gap:10px;margin-bottom:6px"><b class="small">Processes</b><span class="grow"></span><a id="plall" class="small" style="cursor:pointer;color:#9d8bff">All</a><a id="plnone" class="small" style="cursor:pointer;color:#9d8bff">None</a></div><div id="plist"></div></div>
   <div style="min-width:0;display:flex;flex-direction:column;gap:8px"><div class="row" style="gap:8px;flex-wrap:wrap">
     <div class="seg" id="plstream"><button data-s="all" class="on">Both</button><button data-s="stdout">Out</button><button data-s="stderr">Error</button></div>
     <label class="toggle" title="show only lines that look like errors"><input type="checkbox" id="plerr"><span></span>Errors only</label>
     <div class="search" style="flex:1;min-width:140px"><span>${ic('search',14)}</span><input id="plgrep" placeholder="Search in logs…"></div>
     <select id="pltail"><option value="50">Last 50</option><option value="200" selected>Last 200</option><option value="1000">Last 1000</option></select>
     <label class="toggle"><input type="checkbox" id="plfol" checked><span></span>Follow</label><button class="btn sm" id="plref">${ic('restart',13)}</button></div>
    <pre id="plout" class="term chrome" style="height:56vh;overflow:auto;margin:0"><span class="spin"></span></pre></div></div>
   <div class="row end" style="margin-top:10px"><span class="dim small grow" id="plinfo"></span><button class="btn ghost" id="mno">Close</button></div>`,'xl');
  const renderList=()=>{$('#plist').innerHTML=PL.procs.map(p=>`<label style="display:flex;gap:8px;align-items:center;padding:5px 2px;cursor:pointer;font-size:12.5px">
     <input type="checkbox" class="ck" data-pl="${esc(pKey(p))}" ${PL.pick.has(pKey(p))?'checked':''}><span class="pulse st-${pRun(p)?'ok':'error'}" style="margin:0"></span>
     <span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.name)} <span class="dim">#${p.id} · ${esc(p.user)}</span></span>${p.restarts?`<span class="hb ${p.restarts>20?'bad':''}">↺${p.restarts}</span>`:''}</label>`).join('')};
  let stream='all';
  const show=()=>{const o=$('#plout');if(!o)return;const g=$('#plgrep').value,gl=g.toLowerCase(),eo=$('#plerr').checked,atB=o.scrollHeight-o.scrollTop-o.clientHeight<60;
    const sel=PL.procs.filter(p=>PL.pick.has(pKey(p)));
    if(!sel.length){o.innerHTML='<span class="dim">Tick processes on the left.</span>';return}
    let hits=0;
    o.innerHTML=sel.map(p=>{const d=PL.data[pKey(p)];let body;
      if(!d)body='<span class="spin"></span>';else if(d.error)body=`<span class="e">${esc(d.error)}</span>`;
      else{const parts=d.logs.filter(l=>stream==='all'||l.name===stream);
        body=parts.map(l=>{let ls=l.text.replace(/\n$/,'').split('\n').filter(Boolean);
          if(eo)ls=ls.filter(x=>ERR_RE.test(x));if(gl)ls=ls.filter(x=>x.toLowerCase().includes(gl));hits+=ls.length;
          return ls.length?`<span class="ts">${l.name==='stderr'?'· error log':'· out log'}</span>\n${colorHL(ls.join('\n'),g,l.name==='stderr')}`:''}).filter(Boolean).join('\n')||'<span class="dim">(nothing'+(eo||gl?' matching':'')+')</span>'}
      return `<span class="p">━━ ${esc(p.name)} #${p.id} · ${esc(p.user)} ━━</span>\n${body}`}).join('\n\n');
    $('#plinfo').textContent=(eo||gl)?`${hits} matching line${hits===1?'':'s'}`:`${sel.length} process${sel.length>1?'es':''} · refreshes every 4s while Follow is on`;
    if(atB||!o.dataset.init){o.scrollTop=o.scrollHeight;o.dataset.init=1}};
  const load=async()=>{if(!$('#plout')){clearInterval(PL.timer);return}
    const sel=PL.procs.filter(p=>PL.pick.has(pKey(p)));
    await Promise.all(sel.map(async p=>{try{const j=await api('/api/pm2_logs',{...pArgs(p),tail:$('#pltail').value});PL.data[pKey(p)]={logs:j.logs||[]}}catch(e){PL.data[pKey(p)]={error:e.message}}}));show()};
  const follow=()=>{clearInterval(PL.timer);if($('#plfol').checked)PL.timer=setInterval(load,4000)};
  renderList();
  $('#plist').onchange=e=>{const k=e.target.dataset.pl;if(!k)return;e.target.checked?PL.pick.add(k):PL.pick.delete(k);
    if(PL.pick.size>20){PL.pick.delete(k);e.target.checked=false;toast('Up to 20 processes at once','info');return}show();if(e.target.checked&&!PL.data[k])load()};
  $('#plall').onclick=()=>{PL.pick=new Set(PL.procs.slice(0,20).map(pKey));renderList();load()};$('#plnone').onclick=()=>{PL.pick.clear();renderList();show()};
  $('#plstream').onclick=e=>{const x=e.target.closest('[data-s]');if(!x)return;stream=x.dataset.s;$$('#plstream button').forEach(y=>y.classList.toggle('on',y===x));show()};
  $('#plerr').onchange=show;$('#plgrep').oninput=show;$('#pltail').onchange=load;$('#plref').onclick=load;$('#plfol').onchange=follow;
  $('#mno').onclick=()=>{clearInterval(PL.timer);closeModal()};
  load();follow()}
$('#pmlogs').innerHTML=ic('logs',15)+' All logs';$('#pmlogs').onclick=()=>pmAllLogs();
$('#topsvc').innerHTML=ic('cpu',15)+' Services';$('#topsvc').onclick=()=>S.fserver&&openSvc(S.fserver);

// ---------- web terminal ----------
const T={tabs:[],cur:null,seq:0,lib:null};
function loadTermLib(){if(T.lib)return T.lib;
  const add=(tag,attrs)=>new Promise((res,rej)=>{const e=document.createElement(tag);Object.assign(e,attrs);e.onload=res;e.onerror=()=>rej(new Error('could not load terminal library (internet needed for cdn.jsdelivr.net)'));document.head.appendChild(e)});
  T.lib=Promise.all([add('link',{rel:'stylesheet',href:'https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css'}),
    add('script',{src:'https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js'})]).then(()=>add('script',{src:'https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js'}));
  T.lib.catch(()=>{T.lib=null});return T.lib}
async function openTerm(server,opts){opts=opts||{};
  if(NOPTY){toast('The web terminal needs Linux, macOS or WSL. Everything else works on Windows.','bad',6000);return}
  try{await loadTermLib()}catch(e){toast(esc(e.message),'bad');return}
  const id=++T.seq,el=document.createElement('div');el.className='tbody';$('#tbodies').appendChild(el);
  const term=new Terminal({cursorBlink:true,fontFamily:'"JetBrains Mono",ui-monospace,monospace',fontSize:13,lineHeight:1.15,scrollback:10000,allowProposedApi:true,
    theme:{background:'#05060a',foreground:'#d7dbea',cursor:'#9d8bff',selectionBackground:'rgba(124,92,255,.35)',black:'#1b1e2b',brightBlack:'#5c6378',
      red:'#ff5c7a',green:'#22e3a1',yellow:'#ffc94d',blue:'#7aa2ff',magenta:'#c49bff',cyan:'#22d3ee',white:'#d7dbea'}});
  const fit=new FitAddon.FitAddon();term.loadAddon(fit);term.open(el);
  const tab={id,server,container:opts.container||'',el,term,fit,ws:null,state:'connecting',sg:{pending:true,y:null,x:0,suf:''}};T.tabs.push(tab);
  tab.sg.el=document.createElement('div');tab.sg.el.className='tsg';el.appendChild(tab.sg.el);
  showTermPanel();activateTerm(id);connectTerm(tab);
  term.onData(d=>{sgKey(tab,d);if(tab.ws&&tab.ws.readyState===1)tab.ws.send('\x00'+d)});
  term.onRender(()=>sgSoon(tab));term.onScroll(()=>sgSoon(tab));
  if(!tab.container)histRemote(server);
  term.attachCustomKeyEventHandler(e=>{
    if(e.type==='keydown'&&!e.altKey&&!e.metaKey&&!e.shiftKey&&tab.sg.suf&&((!e.ctrlKey&&(e.key==='ArrowRight'||e.key==='End'))||(e.ctrlKey&&e.key==='ArrowRight'))){
      if(sgAccept(tab,e.ctrlKey)){e.preventDefault();return false}}
    if(e.type!=='keydown'||!e.ctrlKey||e.altKey||e.metaKey)return true;const k=e.key.toLowerCase();
    if(e.shiftKey&&k==='c'){e.preventDefault();termCopy(term);return false}                 // Ctrl+Shift+C: copy
    if(e.shiftKey&&k==='v'){e.preventDefault();termPaste(term);return false}                // Ctrl+Shift+V: paste
    if(!e.shiftKey&&k==='c'&&term.hasSelection()){e.preventDefault();termCopy(term);term.clearSelection();return false}  // Ctrl+C with a selection copies, otherwise it interrupts
    return true});
  el.addEventListener('mouseup',e=>{if(e.button===0&&termAuto())setTimeout(()=>{if(term.hasSelection())termCopy(term,true)},0)});
  el.addEventListener('contextmenu',e=>{e.preventDefault();if(term.hasSelection()){termCopy(term);term.clearSelection()}else termPaste(term)});
  term.onResize(({cols,rows})=>{if(tab.ws&&tab.ws.readyState===1)tab.ws.send('\x01'+JSON.stringify({c:cols,r:rows}))});
}
function connectTerm(tab){
  tab.term.write(`\x1b[2mConnecting to ${tab.server}${tab.container?' › container '+tab.container:''}…\x1b[0m\r\n`);tab.state='connecting';renderTermTabs();
  const ws=new WebSocket(`ws://${location.host}/ws/term?server=${encodeURIComponent(tab.server)}${tab.container?'&container='+encodeURIComponent(tab.container):''}&t=${encodeURIComponent(TOKEN)}`);
  ws.binaryType='arraybuffer';tab.ws=ws;
  ws.onopen=()=>{tab.state='open';renderTermTabs();try{tab.fit.fit()}catch(_){}ws.send('\x01'+JSON.stringify({c:tab.term.cols,r:tab.term.rows}));if(T.cur===tab.id)tab.term.focus()};
  ws.onmessage=e=>tab.term.write(new Uint8Array(e.data));
  ws.onclose=()=>{if(tab.ws!==ws)return;tab.state='closed';renderTermTabs();tab.term.write('\r\n\x1b[33mDisconnected.\x1b[0m Press \x1b[1mReconnect\x1b[0m to start a new session.\r\n')};
}
function curTab(){return T.tabs.find(t=>t.id===T.cur)}
// ---- command suggestions from history (zsh-autosuggestions style, all in the browser) ----
// The typed command is read from the screen (from where the prompt ended to the end of the line), so tab completion,
// history recall and line editing by the remote shell are all taken into account, and text that is not echoed
// (passwords) is never seen or stored.
T.sugg=(()=>{try{return localStorage.getItem('dctl.suggest')!=='0'}catch(_){return true}})();
T.hist=(()=>{try{return JSON.parse(localStorage.getItem('dctl.hist')||'{}')||{}}catch(_){return{}}})();   // {"server" or "server›container": [old … new]}
T.rhist={};     // server -> its own shell history (newest first), memory only
function histKey(tab){return tab.server+(tab.container?'›'+tab.container:'')}
function histAdd(tab,cmd){if(!cmd||cmd.length>400||/^\s/.test(cmd))return;const k=histKey(tab),a=(T.hist[k]||[]).filter(x=>x!==cmd);a.push(cmd);T.hist[k]=a.slice(-500);
  clearTimeout(T.histSave);T.histSave=setTimeout(()=>{try{localStorage.setItem('dctl.hist',JSON.stringify(T.hist))}catch(_){}},400)}
async function histRemote(server){if(T.rhist[server])return;T.rhist[server]=[];
  try{const j=await raw('/api/hist',{server});if(j.ok&&j.hist)T.rhist[server]=j.hist}catch(_){}}
function sgFind(tab,cur){
  const hit=a=>{for(let i=a.length-1;i>=0;i--){const c=a[i];if(c.length>cur.length&&c.startsWith(cur))return c}return null};
  const mine=hit(T.hist[histKey(tab)]||[]);if(mine)return mine;
  const r=T.rhist[tab.server]||[];for(const c of r)if(c.length>cur.length&&c.startsWith(cur))return c;
  for(const k in T.hist){const c=hit(T.hist[k]);if(c)return c}return null}
function sgText(tab,y,x){   // text from (row y, column x) to the end of that line and the lines it wraps into
  const b=tab.term.buffer.active;let s='';
  for(let i=0;i<=20;i++,y++){const l=b.getLine(y);if(!l)return null;s+=l.translateToString(false,i?0:x);const n=b.getLine(y+1);if(!n||!n.isWrapped)break}
  return s.replace(/\s{4,}\S.*$/,'').trimEnd()}    // drop a right-side prompt (zsh RPROMPT)
function sgFlush(tab){const r=tab.sg.rec;if(!r)return;tab.sg.rec=null;clearTimeout(r.t);
  if(tab.term.buffer.active.type!=='normal')return;const c=sgText(tab,r.y,r.x);
  histAdd(tab,c&&c.startsWith(r.now)?c:r.now)}     // later text only if it just completes what was on screen at Enter
function sgRead(tab){   // {text, atEnd} of the command being typed, or null when unknown
  const a=tab.sg,t=tab.term,b=t.buffer.active;if(a.pending||a.y==null||b.type!=='normal')return null;
  const cy=b.baseY+b.cursorY;if(cy<a.y||cy-a.y>20)return null;
  const text=sgText(tab,a.y,a.x);if(text===null)return null;
  const off=(cy-a.y)*t.cols+b.cursorX-a.x;return{text,atEnd:off===text.length}}
function sgKey(tab,d){const a=tab.sg,b=tab.term.buffer.active;sgFlush(tab);
  if(a.pending&&b.type==='normal'){a.y=b.baseY+b.cursorY;a.x=b.cursorX;a.pending=false}
  if(d.includes('\r')||d.includes('\n')){   // remember the command a moment later, once the shell has echoed every typed character
    const now=b.type==='normal'&&!a.pending?sgText(tab,a.y,a.x):'';    // nothing on screen (e.g. a password that is not echoed) -> nothing remembered
    if(now)a.rec={y:a.y,x:a.x,now,t:setTimeout(()=>sgFlush(tab),600)};a.pending=true}
  else if(d==='\x03'||d==='\x04'||d==='\x0c')a.pending=true;
  sgHide(tab)}
function sgHide(tab){tab.sg.suf='';tab.sg.el.style.display='none'}
function sgSoon(tab){if(tab.sg.raf)return;tab.sg.raf=requestAnimationFrame(()=>{tab.sg.raf=0;sgShow(tab)})}
function sgShow(tab){const a=tab.sg,t=tab.term,b=t.buffer.active;
  if(!T.sugg||tab.state!=='open'||b.viewportY!==b.baseY)return sgHide(tab);
  const r=sgRead(tab);if(!r||!r.atEnd||!r.text.trim())return sgHide(tab);
  const full=sgFind(tab,r.text);if(!full)return sgHide(tab);
  const room=t.cols-b.cursorX;if(room<1)return sgHide(tab);
  const scr=tab.el.querySelector('.xterm-screen');if(!scr)return sgHide(tab);
  const cw=scr.clientWidth/t.cols,ch=scr.clientHeight/t.rows,sr=scr.getBoundingClientRect(),er=tab.el.getBoundingClientRect();
  a.suf=full.slice(r.text.length);const e=a.el;e.textContent=a.suf.slice(0,room).replace(/\n.*/s,'');
  Object.assign(e.style,{display:'block',left:(sr.left-er.left+b.cursorX*cw)+'px',top:(sr.top-er.top+b.cursorY*ch)+'px',height:ch+'px',lineHeight:ch+'px',
    maxWidth:(room*cw)+'px',fontFamily:t.options.fontFamily,fontSize:t.options.fontSize+'px'})}
function sgAccept(tab,word){const a=tab.sg;if(!a.suf||!tab.ws||tab.ws.readyState!==1)return false;
  const part=word?(a.suf.match(/^\s*\S+/)||[a.suf])[0]:a.suf;tab.ws.send('\x00'+part);sgHide(tab);return true}
T.auto=(()=>{try{return localStorage.getItem('dctl.autocopy')!=='0'}catch(_){return true}})();
function termAuto(){return T.auto}
async function clipWrite(txt){
  try{await navigator.clipboard.writeText(txt);return true}catch(_){}
  const a=document.createElement('textarea');a.value=txt;a.style.cssText='position:fixed;top:-1000px;opacity:0';document.body.appendChild(a);a.select();
  let ok=false;try{ok=document.execCommand('copy')}catch(_){}a.remove();return ok}
async function termCopy(term,quiet){const txt=term.getSelection();if(!txt)return;const ok=await clipWrite(txt);term.focus();
  if(!ok)return toast('Could not copy to the clipboard','bad');
  const n=txt.split('\n').length;toast(n>1?`Copied ${n} lines`:'Copied','ok',1200)}
async function clipRead(){
  try{const t=await navigator.clipboard.readText();if(t)return t}catch(_){}
  const a=document.createElement('textarea');a.style.cssText='position:fixed;top:-1000px;opacity:0';document.body.appendChild(a);a.focus();   // app window (WebKitGTK): readText comes back empty, execCommand works
  let t=null;try{if(document.execCommand('paste'))t=a.value}catch(_){}a.remove();return t}
async function termPaste(term){const txt=await clipRead();term.focus();
  if(txt===null)return toast('Could not read the clipboard','bad');if(txt)term.paste(txt)}
function activateTerm(id){T.cur=id;T.tabs.forEach(t=>t.el.classList.toggle('on',t.id===id));renderTermTabs();
  const t=curTab();if(t)requestAnimationFrame(()=>{try{t.fit.fit()}catch(_){}t.term.focus()})}
function renderTermTabs(){
  $('#ttabs').innerHTML=T.tabs.map(t=>`<a class="ttab ${t.id===T.cur?'on':''}" data-tid="${t.id}"><i class="tdot ${t.state}"></i>${esc(t.server)}${t.container?` <span class="dim">›</span> ${esc(t.container)}`:''}<span class="tx" data-tclose="${t.id}" title="Close session">${ic('x',12)}</span></a>`).join('');
  const n=T.tabs.length;$('#termbtn').classList.toggle('hidden',!n);$('#termbtn span').textContent=n>1?`Terminal · ${n}`:'Terminal';
  const t=curTab();$('#trecon').classList.toggle('hidden',!t||t.state!=='closed')}
function showTermPanel(){$('#termp').classList.remove('hidden');const t=curTab();if(t)requestAnimationFrame(()=>{try{t.fit.fit()}catch(_){}t.term.focus()})}
function hideTermPanel(){$('#termp').classList.add('hidden')}
function closeTerm(id){const t=T.tabs.find(x=>x.id===id);if(!t)return;const ws=t.ws;t.ws=null;try{ws&&ws.close()}catch(_){}t.term.dispose();t.el.remove();
  T.tabs=T.tabs.filter(x=>x!==t);if(T.cur===id)T.cur=T.tabs.length?T.tabs[T.tabs.length-1].id:null;
  if(!T.tabs.length)hideTermPanel();else activateTerm(T.cur);renderTermTabs()}
$('#ttabs').onclick=async e=>{const c=e.target.closest('[data-tclose]');if(c){e.stopPropagation();const t=T.tabs.find(x=>x.id==c.dataset.tclose);
    if(t&&t.state==='open'&&!await confirmBox(`Close the terminal session on <b>${esc(t.server)}</b>? Anything running in it is stopped.`,'Close session',true))return;return closeTerm(+c.dataset.tclose)}
  const a=e.target.closest('[data-tid]');if(a)activateTerm(+a.dataset.tid)};
$('#tmin').onclick=hideTermPanel;$('#tfiles').onclick=()=>{const t=curTab();if(t){hideTermPanel();openFiles(t.server)}};$('#termbtn').onclick=showTermPanel;
$('#tnew').onclick=()=>{const t=curTab();if(t)openTerm(t.server,{container:t.container})};
$('#trecon').onclick=()=>{const t=curTab();if(t){t.term.reset();connectTerm(t)}};
$('#tsudo').onclick=()=>{const t=curTab();if(t&&t.ws&&t.ws.readyState===1){t.ws.send('\x00sudo -i\r');t.term.focus()}};
$('#tsugg').checked=T.sugg;$('#tsugg').onchange=e=>{T.sugg=e.target.checked;try{localStorage.setItem('dctl.suggest',T.sugg?'1':'0')}catch(_){}
  T.tabs.forEach(t=>T.sugg?sgSoon(t):sgHide(t));const t=curTab();if(t)t.term.focus()};
$('#tauto').checked=termAuto();$('#tauto').onchange=e=>{T.auto=e.target.checked;try{localStorage.setItem('dctl.autocopy',e.target.checked?'1':'0')}catch(_){}
  toast(e.target.checked?'Selected text is copied automatically':'Copy on select is off — use Ctrl+Shift+C','info',2200);const t=curTab();if(t)t.term.focus()};
$('#tfull').onclick=()=>{$('#termp').classList.toggle('full');const t=curTab();if(t)setTimeout(()=>{try{t.fit.fit()}catch(_){}},60)};
window.addEventListener('resize',()=>{const t=curTab();if(t&&!$('#termp').classList.contains('hidden'))try{t.fit.fit()}catch(_){}});
window.addEventListener('beforeunload',e=>{if(T.tabs.some(t=>t.state==='open')){e.preventDefault();e.returnValue=''}});
$('#tbtnic').innerHTML=ic('term',15);$('#toprun').innerHTML=ic('play',13)+' Run';$('#toprun').onclick=()=>openRun();
$('#topfiles').innerHTML=ic('folder',15)+' Files';$('#topfiles').onclick=()=>S.fserver&&openFiles(S.fserver);
$('#topdisk').innerHTML=ic('disk',15)+' Disk';$('#topdisk').onclick=()=>S.fserver&&openDisk(S.fserver);
$('#toprun2').innerHTML=ic('play',13)+' Run';$('#toprun2').onclick=()=>S.fserver&&openRun([S.fserver]);$('#topterm').innerHTML=ic('term',16)+' Terminal';$('#topterm').onclick=()=>S.fserver&&openTerm(S.fserver);
// ---------- disk cleanup ----------
const hb=b=>fmtB(b);
async function openDisk(server){
  modal(`<h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('disk',18)}</span> Disk cleanup · ${esc(server)}</h3><div id="dkbody"><div class="mut" style="padding:30px 0;text-align:center"><span class="spin"></span> Analysing disk usage… <span class="dim">(up to ~30s)</span></div></div>
    <div class="row end" style="margin-top:12px"><button class="btn ghost" id="mno">Close</button></div>`,'xl');
  $('#mno').onclick=closeModal;
  let d;try{d=await api('/api/disk',{server})}catch(e){if($('#dkbody'))$('#dkbody').innerHTML=`<div class="note warn">${esc(e.message)}</div>`;return}
  if(!$('#dkbody'))return;renderDisk(server,d);
}
function renderDisk(server,d){
  const btn=(a,label,danger,arg)=>`<button class="btn sm ${danger?'danger':''}" data-clean="${a}" data-arg="${esc(arg||'')}">${label}</button>`;
  const dbar=f=>{const p=f.used/f.size*100;return `<div class="dkdf"><b class="mono">${esc(f.mount)}</b><div class="t"><i style="width:${clamp(p)}%;background:${pgrad(p)}"></i></div><span><b>${clamp(p)}%</b> · ${hb(f.used)} of ${hb(f.size)} · <b style="color:var(--ok)">${hb(f.free)} free</b></span></div>`};
  const dk=Object.fromEntries(d.docker.map(x=>[x.type,x]));
  const sec=(title,icon,body)=>`<section class="dksec"><h4>${ic(icon,15)} ${title}</h4>${body}</section>`;
  const row=(label,size,actions,sub)=>`<div class="dkrow"><div class="grow" style="min-width:0"><div>${label}</div>${sub?`<div class="dim small mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${sub}</div>`:''}</div><b class="mono">${size}</b>${actions||''}</div>`;
  let h=`${d.elevated?'':'<div class="note warn" style="margin-bottom:10px">Running without sudo — some folders could not be read, numbers may be incomplete.</div>'}
  <div class="dkdfs">${d.df.map(dbar).join('')}</div><div class="dkgrid">`;
  if(d.has_docker){const im=dk.Images||{},bc=dk['Build Cache']||{},ct=dk.Containers||{},vo=dk['Local Volumes']||{};
    h+=sec('Docker','box',
      row(`Images <span class="dim">· ${esc(im.total||0)} total, ${esc(im.active||0)} in use</span>`,`${hb(im.size)} <span class="dim">· ${hb(im.reclaim)} free-able</span>`,btn('docker_dangling','Dangling')+btn('docker_images','All unused',1))
      +row('Build cache',hb(bc.size),bc.size?btn('docker_builder','Clear'):'')
      +row(`Stopped containers <span class="dim">· ${esc((+ct.total||0)-(+ct.active||0))} stopped</span>`,hb(ct.reclaim),(+ct.total||0)>(+ct.active||0)?btn('docker_containers','Remove stopped',1):'')
      +row(`Volumes <span class="dim">· not touched (may hold data)</span>`,hb(vo.size),''));
    const big=d.clogs.filter(c=>c.size>100e6);
    h+=sec('Container logs','logs',(d.clogs.length?d.clogs.slice(0,8).map(c=>row(esc(c.name),hb(c.size),c.size>1e6?btn('clog','Empty',0,c.path):'')).join(''):'<div class="dim small">No container log files found.</div>')
      +(big.length?`<div class="row end" style="margin-top:8px">${btn('clogs_big',`Empty all &gt; 100 MB (${big.length})`)}</div>`:''));}
  h+=sec('System logs','file',row('systemd journal',hb(d.journal),d.journal>250e6?btn('journal','Shrink to 200 MB'):'')
    +row(`/var/log <span class="dim">· total</span>`,hb(d.varlog),'')
    +row(`Old rotated logs <span class="dim">· ${d.rotated.count} files (*.gz, *.1)</span>`,hb(d.rotated.size),d.rotated.count?btn('rotated','Delete'):'')
    +d.biglogs.slice(0,5).map(b=>row(`<span class="dim">big file</span>`,hb(b.size),'',esc(b.path))).join(''));
  if(d.pm2.length)h+=sec('PM2 logs','bolt',d.pm2.map(p=>row(esc(p.path.replace('/.pm2/logs','')),hb(p.size),p.size>1e6?btn('pm2logs','Empty',0,p.path):'',esc(p.path))).join(''));
  h+=sec('Biggest folders','folder',d.topdirs.filter(t=>!['/var','/var/lib','/home','/opt','/srv','/root','/tmp','/usr/local'].includes(t.path)).slice(0,10).map(t=>row(`<span class="mono">${esc(t.path)}</span>`,hb(t.size),'')).join('')||'<div class="dim small">—</div>');
  h+='</div>';
  $('#dkbody').innerHTML=h;
  $$('#dkbody [data-clean]').forEach(b=>b.onclick=async()=>{const a=b.dataset.clean,arg=b.dataset.arg;
    const what={docker_dangling:'remove dangling (untagged) Docker images',docker_images:'remove <b>ALL Docker images not used by a container</b> (they will be pulled again when needed)',
      docker_builder:'clear the Docker build cache',docker_containers:'<b>delete all STOPPED containers</b> (their compose/run config is gone; compose projects can recreate them with up -d)',
      clogs_big:'empty every container log file bigger than 100 MB',clog:`empty the log file of this container`,journal:'shrink the systemd journal to 200 MB',
      rotated:'delete old rotated log files in /var/log (*.gz, *.1, *.old)',pm2logs:`empty all PM2 log files in <code>${esc(arg)}</code>`}[a];
    closeModal();const ok=await confirmBox(`On <b>${esc(server)}</b>: ${what}?<br><br><span class="dim">This cannot be undone.</span>`,'Clean up',/images|containers/.test(a));
    if(!ok)return openDiskCached(server,d);
    openDiskCached(server,d,true);
    try{const j=await api('/api/cleanup',{server,action:a,arg});toast(j.rc?`Cleanup finished with errors`:`Freed <b>${j.freed!=null?hb(Math.max(0,j.freed)):'space'}</b> on ${esc(server)}`,j.rc?'bad':'ok',6000)}
    catch(e){toast(esc(e.message),'bad')}
    openDisk(server)});
}
function openDiskCached(server,d,busyNote){modal(`<h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('disk',18)}</span> Disk cleanup · ${esc(server)}</h3><div id="dkbody"></div>
  <div class="row end" style="margin-top:12px"><button class="btn ghost" id="mno">Close</button></div>`,'xl');$('#mno').onclick=closeModal;
  if(busyNote){$('#dkbody').innerHTML='<div class="mut" style="padding:30px 0;text-align:center"><span class="spin"></span> Cleaning up…</div>'}else renderDisk(server,d)}

// ---------- run command on servers + saved commands ----------
const R={sel:new Set(),cmds:[],last:null};
async function openRun(pre){
  if(pre)R.sel=new Set(pre);
  let idx;try{idx=await ensureIndex(false)}catch(_){}
  try{R.cmds=(await api('/api/cmds',{})).cmds}catch(_){R.cmds=[]}
  const lay=await getLayouts();
  const grp=(pg)=>{const L=lay[pg];if(!L)return'';const blocks=[];if(L.ungrouped.length)blocks.push(['',L.ungrouped]);L.groups.forEach(g=>g.servers.length&&blocks.push([g.name,g.servers]));
    return blocks.map(([g,arr])=>`<div class="rg"><label class="rgh"><input type="checkbox" data-rgall="${esc(arr.join(','))}"> ${ic(pg==='pm2'?'bolt':'box',13)} ${esc(PGT[pg])}${g?` · ${esc(g)}`:''}</label>
      ${arr.map(n=>`<label class="rs"><input type="checkbox" value="${esc(n)}" ${R.sel.has(n)?'checked':''}>${esc(n)}</label>`).join('')}</div>`).join('')};
  modal(`<h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('term',18)}</span> Run command on servers</h3>
   <div class="rwrap"><div class="rleft"><div class="small mut" style="margin-bottom:6px">Servers</div><div id="rsrv">${grp('docker')}${grp('pm2')}</div></div>
   <div class="rright">
     <div class="small mut">Saved commands</div><div id="rcmds" class="rcmds"></div>
     <textarea id="rcmd" spellcheck="false" placeholder="e.g.  df -h /   ·   docker ps   ·   pm2 list   ·   systemctl status nginx"></textarea>
     <div class="row" style="flex-wrap:wrap;gap:10px">
       <label class="toggle"><input type="checkbox" id="rsudo"><span></span>Run with sudo</label>
       <select id="rtmo"><option value="30">30s timeout</option><option value="120" selected>2 min timeout</option><option value="600">10 min timeout</option></select>
       <span class="grow"></span><button class="btn" id="rsave">${ic('download',14)} Save command</button><button class="btn primary" id="rgo">${ic('play',13)} Run</button></div>
     <div id="rres"></div></div></div>
   <div class="row end" style="margin-top:10px"><button class="btn ghost" id="mno">Close</button></div>`,'xl');
  $('#mno').onclick=closeModal;
  const upd=()=>{R.sel=new Set($$('#rsrv .rs input:checked').map(i=>i.value));$('#rgo').innerHTML=`${ic('play',13)} Run on ${R.sel.size} server${R.sel.size==1?'':'s'}`;
    $$('#rsrv [data-rgall]').forEach(g=>{const a=g.dataset.rgall.split(',');g.checked=a.every(n=>R.sel.has(n));g.indeterminate=!g.checked&&a.some(n=>R.sel.has(n))})};
  $('#rsrv').onchange=e=>{const g=e.target.closest('[data-rgall]');if(g){const a=g.dataset.rgall.split(',');$$('#rsrv .rs input').forEach(i=>{if(a.includes(i.value))i.checked=g.checked})}upd()};
  renderCmds();upd();
  $('#rsave').onclick=saveCmd;$('#rgo').onclick=()=>runCmd();
  $('#rcmd').onkeydown=e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();runCmd()}};
  $('#rcmd').focus();
  if(R.last)showResults(R.last);
}
async function getLayouts(){const out={};for(const pg of ['docker','pm2']){try{out[pg]=(await api('/api/layout_get',{page:pg})).layout}catch(_){}}return out}
function renderCmds(){$('#rcmds').innerHTML=R.cmds.map(c=>`<span class="rc" data-cid="${esc(c.id)}" title="${esc(c.cmd)}">${c.sudo?ic('lock',11):''}${esc(c.name)}<i data-cdel="${esc(c.id)}" title="Delete">${ic('x',11)}</i></span>`).join('')||'<span class="dim small">None yet — type a command and press Save.</span>';
  $('#rcmds').onclick=async e=>{const del=e.target.closest('[data-cdel]');
    if(del){e.stopPropagation();try{R.cmds=(await api('/api/cmds',{op:'delete',id:del.dataset.cdel})).cmds;renderCmds();toast('Command deleted','ok',1500)}catch(er){toast(esc(er.message),'bad')}return}
    const c=R.cmds.find(x=>x.id===e.target.closest('[data-cid]')?.dataset.cid);if(c){$('#rcmd').value=c.cmd;$('#rsudo').checked=c.sudo;$('#rcmd').focus()}}}
async function saveCmd(){const cmd=$('#rcmd').value.trim();if(!cmd){toast('Type a command first','info');return}
  const box=$('#rres');const prev=box.innerHTML;
  box.innerHTML=`<div class="form" style="margin-top:6px"><label>Name for this command</label><div class="row"><input id="rname" placeholder="e.g. Restart nginx" style="flex:1"><button class="btn primary" id="rnok">Save</button><button class="btn ghost" id="rncancel">Cancel</button></div></div>`;
  $('#rname').focus();const back=()=>{box.innerHTML=prev};$('#rncancel').onclick=back;
  const go=async()=>{const name=$('#rname').value.trim();if(!name)return;try{R.cmds=(await api('/api/cmds',{op:'save',name,cmd,sudo:$('#rsudo').checked})).cmds;renderCmds();back();toast(`Saved <b>${esc(name)}</b>`,'ok')}catch(e){toast(esc(e.message),'bad')}};
  $('#rnok').onclick=go;$('#rname').onkeydown=e=>{if(e.key==='Enter')go()}}
async function runCmd(only){const cmd=$('#rcmd').value.trim(),sudo=$('#rsudo').checked,servers=only||[...R.sel];
  if(!cmd){toast('Type a command','info');return}if(!servers.length){toast('Tick at least one server','info');return}
  if(!only&&servers.length>1){const box=$('#rres');box.innerHTML=`<div class="note" style="margin-top:6px">Run <code>${esc(cmd)}</code>${sudo?' <b>with sudo</b>':''} on <b>${servers.length}</b> servers?
     <div class="row end" style="margin-top:8px"><button class="btn ghost" id="rcx">Cancel</button><button class="btn primary" id="rcy">Yes, run</button></div></div>`;
    const ok=await new Promise(r=>{$('#rcy').onclick=()=>r(true);$('#rcx').onclick=()=>r(false)});if(!ok){box.innerHTML='';return}}
  const res=only&&R.last?R.last:{cmd,results:[]};res.cmd=cmd;
  servers.forEach(s=>{res.results=res.results.filter(r=>r.server!==s);res.results.push({server:s,pending:true})});R.last=res;showResults(res);
  try{const j=await api('/api/multirun',{servers,cmd,sudo,timeout:$('#rtmo').value});
    j.results.forEach(r=>{const i=res.results.findIndex(x=>x.server===r.server);res.results[i]=r});}
  catch(e){res.results.forEach(r=>{if(r.pending){r.pending=false;r.output=e.message;r.rc=null}})}
  if($('#rres'))showResults(res)}
function showResults(res){const box=$('#rres');if(!box)return;
  const ok=res.results.filter(r=>r.rc===0).length,bad=res.results.filter(r=>!r.pending&&r.rc!==0).length;
  box.innerHTML=`<div class="rsum"><code>${esc(res.cmd)}</code><span class="grow"></span>${ok?`<span style="color:var(--ok)">✓ ${ok}</span>`:''}${bad?`<span style="color:var(--bad)">✗ ${bad}</span>`:''}</div>`+
   res.results.map(r=>`<details class="rr" ${res.results.length<=3||r.rc!==0?'open':''}><summary><b>${esc(r.server)}</b>${r.pending?'<span class="spin"></span>':r.rc===0?'<span class="hb ok">exit 0</span>':r.need_sudo?'<span class="hb bad">locked</span>':`<span class="hb bad">${r.rc==null?'error':'exit '+r.rc}</span>`}
     ${r.ms!=null?`<span class="dim small">${(r.ms/1000).toFixed(1)}s</span>`:''}<span class="grow"></span>${r.need_sudo?`<button class="btn sm" data-unl="${esc(r.server)}">${ic('lock',12)} Unlock &amp; retry</button>`:''}</summary>
     ${r.pending?'':`<pre class="term">${colorize(r.output||'(no output)')}</pre>`}</details>`).join('');
  $$('#rres [data-unl]').forEach(b=>b.onclick=async e=>{e.preventDefault();const s=b.dataset.unl;const keep=$('#mbox').innerHTML;
    const st={cmd:$('#rcmd').value,sudo:$('#rsudo').checked,sel:[...R.sel]};
    if(await askSudo(s)){await openRun(st.sel);$('#rcmd').value=st.cmd;$('#rsudo').checked=st.sudo;runCmd([s])}else openRun(st.sel).then(()=>{$('#rcmd').value=st.cmd;$('#rsudo').checked=st.sudo})});
}

// ---------- global search index ----------
async function ensureIndex(force){
  if(!force&&S.idx&&Date.now()-S.idx.t<90000)return S.idx;
  if(S.idxP)return S.idxP;
  S.idxP=api('/api/index',{force}).then(j=>{S.idx={...j,t:Date.now()};S.idxP=null;return S.idx}).catch(e=>{S.idxP=null;throw e});
  return S.idxP}
// ---------- file browser ----------
const FB={server:'',path:'',data:null,q:''};
function pathKey(){return FB.c?'dctl.cpath.'+FB.server+'/'+FB.c:'dctl.path.'+FB.server}
function lastPath(){try{return localStorage.getItem(pathKey())||(FB.c?'/':'~')}catch(_){return FB.c?'/':'~'}}
function dlUrl(server,path,inline,c,k){return `/dl?server=${encodeURIComponent(server)}&path=${encodeURIComponent(path)}&t=${encodeURIComponent(TOKEN)}${inline?'&inline=1':''}${c?`&c=${encodeURIComponent(c)}&k=${k||'file'}`:''}`}
function openFiles(server,path,o={}){FB.server=server;FB.q='';FB.c=o.container||null;FB.data=null;
  modal(`<h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic(FB.c?'box':'folder',18)}</span> ${FB.c?`Files in container <code>${esc(FB.c)}</code> · ${esc(server)}`:'Files · '+esc(server)}</h3>
   <div class="row" style="gap:8px;margin-top:6px"><button class="btn sm" id="fbup" title="Parent folder">${ic('up',13)}</button><button class="btn sm" id="fbhome" title="Home folder">~</button>
     <input id="fbpath" class="mono" style="flex:1;font-size:12.5px" spellcheck="false" placeholder="/var/www/html  or a file path"><button class="btn primary sm" id="fbgo">Go</button></div>
   <div class="row" style="gap:8px;margin-top:8px"><div class="search" style="flex:1;min-width:0"><span>${ic('search',14)}</span><input id="fbq" placeholder="Filter in this folder…"></div><span id="fbinfo" class="dim small"></span>
     ${FB.c?'':`<button class="btn sm" id="fbupl" title="Upload files from this computer into this folder (or drag files onto the list)">${ic('upload',13)} Upload</button><input type="file" id="fbfile" multiple class="hidden">`}</div>
   <div id="fbprog" class="hidden" style="margin-top:10px"></div>
   <div id="fblist" class="fblist"></div>
   <div class="row end" style="margin-top:10px"><button class="btn ghost" id="mno">Close</button></div>`,'xl');
  $('#mno').onclick=closeModal;
  $('#fbgo').onclick=()=>fbLoad($('#fbpath').value);$('#fbpath').onkeydown=e=>{if(e.key==='Enter')fbLoad($('#fbpath').value)};
  if(FB.c){$('#fbhome').textContent='/';$('#fbhome').title='Container root'}
  $('#fbhome').onclick=()=>fbLoad(FB.c?'/':'~');$('#fbup').onclick=()=>{const p=(FB.data&&FB.data.path)||FB.path;fbLoad(p.replace(/\/[^\/]+\/?$/,'')||'/')};
  $('#fbq').oninput=e=>{FB.q=e.target.value.toLowerCase();fbRender()};
  if(!FB.c){$('#fbupl').onclick=()=>$('#fbfile').click();$('#fbfile').onchange=e=>{const f=[...e.target.files];e.target.value='';fbUpload(f)};
    const L=$('#fblist'),on=e=>{if(e.dataTransfer&&[...e.dataTransfer.types].includes('Files')){e.preventDefault();L.classList.add('fbdrop')}};
    L.ondragenter=on;L.ondragover=on;L.ondragleave=e=>{if(!L.contains(e.relatedTarget))L.classList.remove('fbdrop')};
    L.ondrop=e=>{e.preventDefault();L.classList.remove('fbdrop');const f=[...(e.dataTransfer.files||[])];
      const dirs=[...(e.dataTransfer.items||[])].filter(i=>i.webkitGetAsEntry&&i.webkitGetAsEntry()?.isDirectory).length;
      if(dirs)toast('Folders can\'t be uploaded — only files (zip the folder first)','info');
      fbUpload(f.filter((x,i)=>!(e.dataTransfer.items[i]?.webkitGetAsEntry?.()?.isDirectory)))}}
  $('#fblist').onclick=e=>{const a=e.target.closest('[data-fa]');const r=e.target.closest('[data-fn]');if(!r)return;
    const it=FB.data.items[+r.dataset.fn],full=FB.data.kind==='file'?FB.data.path:FB.data.path.replace(/\/$/,'')+'/'+it.name;
    if(a){e.stopPropagation();return fbAct(a.dataset.fa,full,it)}
    if(it.type==='dir'||it.type==='link')fbLoad(full);else fbAct('dl',full,it)};
  fbLoad(path||lastPath());
}
async function fbLoad(p){FB.path=p;$('#fbpath').value=p;$('#fblist').innerHTML='<div class="mut" style="padding:24px;text-align:center"><span class="spin"></span></div>';
  try{FB.data=FB.c?await api('/api/cls',{server:FB.server,name:FB.c,path:p}):await api('/api/ls',{server:FB.server,path:p})}catch(e){$('#fblist').innerHTML=`<div class="note warn" style="margin:10px">${esc(e.message)}</div>`;return}
  if(FB.data.stopped){$('#fbpath').value=FB.data.path;$('#fbinfo').textContent='';
    $('#fblist').innerHTML=`<div class="note warn" style="margin:10px">This container is <b>stopped</b>, so its folders can't be listed. You can still download: type the full path above, then
      <div class="row" style="gap:8px;margin-top:10px"><button class="btn sm primary" id="fbsf">${ic('download',13)} Download file</button><button class="btn sm" id="fbsd">${ic('download',13)} Download folder (.tar)</button></div></div>`;
    const go=k=>{const pth=$('#fbpath').value.trim();if(!pth||pth==='/'&&k==='file'){toast('Type the file path first','info');return}fbAct(k==='dir'?'tar':'dl',pth,{name:pth.split('/').pop()||'root',size:null})};
    $('#fbsf').onclick=()=>go('file');$('#fbsd').onclick=()=>go('dir');return}
  if(FB.data.kind==='file'){FB.data.items=FB.data.items.slice(0,1)}
  else try{localStorage.setItem(pathKey(),FB.data.path)}catch(_){}
  $('#fbpath').value=FB.data.path;fbRender()}
function fbRender(){const d=FB.data;if(!d)return;
  const when=t=>{const s=(Date.now()/1000-t);return s<3600?Math.max(1,Math.round(s/60))+'m ago':s<86400?Math.round(s/3600)+'h ago':s<86400*60?Math.round(s/86400)+'d ago':new Date(t*1000).toLocaleDateString()};
  const rows=d.items.map((it,i)=>[it,i]).filter(([it])=>!FB.q||it.name.toLowerCase().includes(FB.q));
  $('#fbinfo').innerHTML=(d.sudo?`${ic('lock',12)} read with sudo · `:'')+(FB.c?'inside container · ':'')+(d.kind==='dir'?`${d.items.length} items`:'file');
  $('#fblist').innerHTML=rows.map(([it,i])=>`<div class="fbrow ${it.type}" data-fn="${i}">
     <span class="fbic">${ic(it.type==='dir'?'folder':it.type==='link'?'chev':'file',15)}</span>
     <span class="fbname" title="${esc(it.name)}">${esc(it.name)}</span>
     <span class="dim small mono">${it.type==='dir'?'':fmtB(it.size)}</span><span class="dim small">${when(it.mtime)}</span><span class="dim small mono fbown" title="${esc(it.owner)}">${esc(it.owner)}</span>
     <span class="fbacts">${it.type==='file'?`${!FB.c&&it.size<=5*1048576?`<button class="btn sm" data-fa="edit" title="Edit this file">${ic('edit',13)} Edit</button>`:''}${it.size<=50e6?`<button class="btn sm" data-fa="copy" title="Copy file content to clipboard">${ic('copy',13)} Copy</button>`:''}<button class="btn sm" data-fa="view" title="Open in a new browser tab">${ic('file',13)}</button><button class="btn sm primary" data-fa="dl">${ic('download',13)} Download</button>`:FB.c&&it.type==='dir'?`<button class="btn sm" data-fa="tar" title="Download this whole folder as a .tar file">${ic('download',13)} .tar</button>`:''}</span></div>`).join('')
   ||'<div class="dim" style="padding:20px;text-align:center">Empty folder</div>'}
// ---------- upload files into the open folder ----------
function upOne(ticket,f,overwrite,onp){return new Promise((res,rej)=>{const x=new XMLHttpRequest();
  x.open('POST',`/upload?ticket=${encodeURIComponent(ticket)}&name=${encodeURIComponent(f.name)}&overwrite=${overwrite?1:0}`);
  x.setRequestHeader('X-Dctl',TOKEN);x.setRequestHeader('Content-Type','application/octet-stream');
  x.upload.onprogress=e=>{if(e.lengthComputable)onp(e.loaded/e.total)};
  x.onload=()=>{try{const j=JSON.parse(x.responseText);j.ok?res(j):rej(new Error(j.need_sudo?'sudo password needed':j.error))}catch(_){rej(new Error('HTTP '+x.status))}};
  x.onerror=()=>rej(new Error('connection to dctl lost'));x.send(f)})}
async function fbUpload(files){
  if(!files.length)return;if(!FB.data||FB.data.kind!=='dir'){toast('Open a folder first','info');return}
  if(FB.up){toast('An upload is already running','info');return}
  const dir=FB.data.path,server=FB.server;let chk;
  try{chk=await api('/api/upcheck',{server,dir,names:files.map(f=>f.name)})}catch(e){toast(esc(e.message),'bad');return}
  if(chk.dirs.length){toast(`Skipped ${chk.dirs.map(esc).join(', ')} — a folder with that name exists`,'bad',6000);files=files.filter(f=>!chk.dirs.includes(f.name))}
  let over=false;
  if(chk.exists.length){const n=chk.exists.length;
    const a=await choose2(`${ic('alert',20)} ${n} file${n>1?'s':''} already exist${n>1?'':'s'}`,
      `<p>In <code>${esc(chk.path)}</code>:</p><pre class="term" style="max-height:30vh;overflow:auto">${chk.exists.map(esc).join('\n')}</pre><p class="small mut">Replacing keeps the file's owner and permissions.</p>`,
      [...(files.length>n?[['skip','Skip these']]:[]),['over','Replace','danger']]);
    if(!a)return;if(a==='skip')files=files.filter(f=>!chk.exists.includes(f.name));else over=true}
  if(!files.length)return;
  FB.up=true;const P=$('#fbprog');P.classList.remove('hidden');
  P.innerHTML=`<div class="note"><div class="row" style="margin-bottom:6px"><b>${ic('upload',14)} Uploading to <code>${esc(chk.path)}</code>${chk.sudo?` <span class="dim small">${ic('lock',11)} with sudo</span>`:''}</b></div>${files.map((f,i)=>`<div class="upl" id="up${i}"><b title="${esc(f.name)}">${esc(f.name)}</b><div class="mbar"><i style="width:0%"></i></div><span class="dim small mono">${fmtB(f.size)}</span></div>`).join('')}</div>`;
  let ok=0,bad=[];
  for(const [i,f] of files.entries()){const row=$('#up'+i),bar=row&&row.querySelector('i'),st=row&&row.querySelector('span');
    try{await upOne(chk.ticket,f,over,p=>{if(bar)bar.style.width=(p*100).toFixed(1)+'%';if(st&&p>=1)st.innerHTML='<span class="spin"></span> checking'});
      ok++;if(bar){bar.style.width='100%';bar.style.background='var(--ok)'}if(st)st.innerHTML=`<span style="color:var(--ok)">${ic('check',12)} done</span>`}
    catch(e){bad.push(f.name+': '+e.message);if(bar)bar.style.background='var(--bad)';if(st)st.innerHTML=`<span style="color:var(--bad)" title="${esc(e.message)}">failed</span>`}}
  FB.up=false;
  if(bad.length)toast(`${ok} uploaded, ${bad.length} failed:<br>${bad.map(esc).join('<br>')}`,'bad',9000);else toast(`${ok} file${ok>1?'s':''} uploaded to <b>${esc(chk.path)}</b>`,'ok');
  if($('#fblist')&&FB.server===server){if(FB.data&&FB.data.path===dir)fbLoad(dir);setTimeout(()=>{if(!FB.up&&$('#fbprog')&&!bad.length)$('#fbprog').classList.add('hidden')},2500)}}
async function fbAct(a,full,it){
  if(a==='edit')return fbEdit(full,it);
  if(a==='dl'||a==='tar'){const x=document.createElement('a');x.href=dlUrl(FB.server,full,false,FB.c,a==='tar'?'dir':'file');x.download=it.name+(a==='tar'?'.tar':'');document.body.appendChild(x);x.click();x.remove();
    toast(`Downloading <b>${esc(it.name)}${a==='tar'?'.tar':''}</b>${it.size!=null&&a==='dl'?` (${fmtB(it.size)})`:''}`,'ok');return}
  if(a==='view'){window.open(dlUrl(FB.server,full,true,FB.c),'_blank');return}
  if(a==='copy'){try{const r=await fetch(dlUrl(FB.server,full,true,FB.c));if(!r.ok)throw new Error(await r.text());const t=await r.text();
      await navigator.clipboard.writeText(t);toast(`Copied <b>${esc(it.name)}</b> — ${t.split('\n').length.toLocaleString()} lines`,'ok')}
    catch(e){toast('Copy failed: '+esc(e.message),'bad')}}}

// ---------- edit a file on the server ----------
const CM_BASE='https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/';
const CM_EXT={yml:'yaml',yaml:'yaml',json:'javascript',js:'javascript',mjs:'javascript',cjs:'javascript',ts:'javascript',
  sh:'shell',bash:'shell',zsh:'shell',env:'shell',py:'python',conf:'nginx',xml:'xml',svg:'xml',html:'xml',css:'css',sql:'sql',
  ini:'properties',cfg:'properties',properties:'properties',toml:'toml',md:'markdown',service:'properties',timer:'properties'};
const CM_DEPS={dockerfile:['addon/mode/simple.min.js']};
const cmLoaded={yaml:true};
function cmMode(name,path){const n=name.toLowerCase();
  if(n==='dockerfile'||n.startsWith('dockerfile.')||n.endsWith('.dockerfile'))return'dockerfile';
  if(/^\.(bashrc|bash_profile|profile|zshrc|bash_logout|bash_aliases)$/.test(n)||n.startsWith('.env')||n==='crontab')return'shell';
  if(/\/nginx\//.test(path)||/\/(sites-available|sites-enabled|conf\.d)\//.test(path))return'nginx';
  if(n==='.gitconfig'||n==='.gitmodules')return'properties';
  return CM_EXT[n.includes('.')?n.split('.').pop():'']||null}
function cmLoad(mode){if(!mode||!window.CodeMirror)return Promise.resolve(null);if(cmLoaded[mode])return Promise.resolve(mode);
  const add=src=>new Promise((res,rej)=>{const e=document.createElement('script');e.src=CM_BASE+src;e.onload=res;e.onerror=rej;document.head.appendChild(e)});
  return (CM_DEPS[mode]||[]).reduce((p,d)=>p.then(()=>add(d)),Promise.resolve()).then(()=>add(`mode/${mode}/${mode}.min.js`))
    .then(()=>{cmLoaded[mode]=true;return mode}).catch(()=>null)}
function edDirty(){return !!(FB.ed&&FB.ed.cm&&FB.ed.cm.getValue()!==FB.ed.orig)}
async function edLeave(){return !edDirty()||await new Promise(res=>{
  const bar=document.createElement('div');bar.className='note warn';bar.style.margin='10px 0';
  bar.innerHTML=`You have <b>unsaved changes</b>. Leave and lose them?<span style="float:right;display:flex;gap:6px"><button class="btn ghost sm" id="lvno">Keep editing</button><button class="btn danger sm" id="lvyes">Discard</button></span>`;
  const host=$('#edwrap');if(!host){res(true);return}host.before(bar);$('#lvno').focus();
  $('#lvno').onclick=()=>{bar.remove();res(false)};$('#lvyes').onclick=()=>{bar.remove();res(true)}})}
function fbEdit(full,it){const dir=full.replace(/\/[^\/]*$/,'')||'/';
  FB.ed={path:full,name:it.name,dir,cm:null,orig:'',sha:'',writable:'no'};
  modal(`<h3><span class="bigic" style="width:36px;height:36px;border-radius:11px">${ic('edit',18)}</span> <span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Edit <code>${esc(full)}</code></span> <span class="dim" style="font-weight:500">· ${esc(FB.server)}</span></h3>
   <div class="row" style="gap:8px;margin-top:4px;flex-wrap:wrap"><button class="btn sm" id="edback">${ic('back',13)} Files</button>
     <span id="ednote" class="dim small grow"></span>
     <label class="toggle" title="Before saving, copy the current file to ${esc(it.name)}.bak.<date> next to it"><input type="checkbox" id="edbak" checked><span></span>Keep backup</label>
     <button class="btn sm" id="edreload" title="Load the file again from the server">${ic('restart',13)} Reload</button>
     <button class="btn sm primary" id="edreview" disabled>${ic('check',13)} Review &amp; save</button></div>
   <div id="edwrap" style="margin-top:10px"><div class="sk" style="height:62vh;border-radius:12px"></div></div>
   <div id="edrev" class="hidden" style="margin-top:10px"></div>
   <div class="row end" style="margin-top:10px"><button class="btn ghost" id="mno">Close</button></div>`,'xl');
  $('#mno').onclick=async()=>{if(await edLeave()){FB.ed=null;closeModal()}};
  $('#edback').onclick=async()=>{if(await edLeave()){FB.ed=null;openFiles(FB.server,dir)}};
  $('#edreload').onclick=async()=>{if(await edLeave())edLoad()};
  $('#edreview').onclick=()=>edReview();
  edLoad()}
async function edLoad(){const E=FB.ed;if(!E)return;$('#edrev').classList.add('hidden');const w=$('#edwrap');w.classList.remove('hidden');
  w.innerHTML='<div class="sk" style="height:62vh;border-radius:12px"></div>';$('#edreview').disabled=true;
  let j;try{j=await api('/api/fread',{server:FB.server,path:E.path})}catch(e){w.innerHTML=`<div class="note bad">${esc(e.message)}</div>`;return}
  if(FB.ed!==E)return;
  E.orig=j.content;E.sha=j.sha;E.writable=j.writable;E.path=j.path||E.path;
  const mode=await cmLoad(cmMode(E.name,E.path));
  const ro=j.writable==='no';
  const note=()=>{$('#ednote').innerHTML=[edDirty()&&'<b style="color:var(--warn)">● unsaved changes</b>',j.sudo&&`${ic('lock',12)} read with sudo`,
    ro?'<b>read-only</b>: you have no permission to save this file':j.writable==='sudo'&&'saves with sudo',
    !ro&&'<kbd>Ctrl</kbd> <kbd>S</kbd> to review'].filter(Boolean).join(' · ')};
  w.innerHTML='';
  if(window.CodeMirror){E.cm=CodeMirror(w,{value:j.content,mode:mode||null,theme:'material-darker',lineNumbers:true,readOnly:ro,indentUnit:2,tabSize:4,
      lineSeparator:j.content.includes('\r\n')?'\r\n':null,extraKeys:{'Ctrl-S':()=>edReview(),'Cmd-S':()=>edReview()}});
    E.cm.on('change',()=>{$('#edreview').disabled=ro||!edDirty();note()});setTimeout(()=>{E.cm&&E.cm.refresh();E.cm&&E.cm.focus()},30)}
  else{const ta=document.createElement('textarea');ta.spellcheck=false;ta.value=j.content;ta.readOnly=ro;ta.style.cssText='width:100%;height:62vh;font:12.5px/1.6 var(--mono)';w.appendChild(ta);
    E.cm={getValue:()=>ta.value,refresh(){},focus:()=>ta.focus()};ta.oninput=()=>{$('#edreview').disabled=ro||!edDirty();note()};
    ta.onkeydown=e=>{if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();edReview()}}}
  note()}
async function edReview(){const E=FB.ed;if(!E||!E.cm||E.writable==='no')return;const content=E.cm.getValue();
  if(content===E.orig){toast('No changes to save','info',2000);return}
  let j;try{j=await api('/api/fdiff',{orig:E.orig,content,name:E.name})}catch(e){toast(esc(e.message),'bad');return}
  const r=$('#edrev');$('#edwrap').classList.add('hidden');r.classList.remove('hidden');const bak=$('#edbak').checked;
  r.innerHTML=`<div class="mut small" style="font-weight:600;letter-spacing:1px">CHANGES</div><pre class="term chrome diff">${diffHtml(j.diff)}</pre>
    <div class="note ${bak?'good':'warn'}" style="margin-top:8px">${bak?`A backup of the current file is saved next to it as <code>${esc(E.name)}.bak.&lt;date&gt;</code>.`:'<b>No backup</b> will be made (the "Keep backup" switch is off).'}${E.writable==='sudo'?' The file is saved with sudo; its owner and permissions stay the same.':' Its owner and permissions stay the same.'}</div>
    <div class="row end" style="margin-top:8px"><button class="btn ghost" id="edback2">${ic('back',14)} Back to editor</button><button class="btn primary" id="edsave">${ic('check',15)} Save to server</button></div>`;
  $('#edback2').onclick=()=>{r.classList.add('hidden');$('#edwrap').classList.remove('hidden');E.cm.refresh();E.cm.focus()};
  $('#edsave').onclick=e=>edSave(e.currentTarget,content)}
async function edSave(btn,content){const E=FB.ed;if(!E)return;
  await busy(btn,async()=>{try{const j=await api('/api/fwrite',{server:FB.server,path:E.path,content,sha:E.sha,backup:$('#edbak').checked});
      E.orig=content;E.sha=j.sha;toast(`Saved <b>${esc(E.name)}</b>`,'ok');
      $('#edrev').innerHTML=`<div class="note good">${ic('check',15)} <b>Saved</b> <code>${esc(FB.server)}:${esc(E.path)}</code>${j.sudo?' <span class="mut">(with sudo)</span>':''}
        ${j.backup?`<br><span class="mut small">Backup: <code>${esc(j.backup)}</code></span>`:''}</div>
        <div class="row" style="margin-top:8px"><button class="btn primary" id="edmore">${ic('edit',14)} Continue editing</button><button class="btn ghost" id="edfiles">${ic('back',14)} Back to files</button></div>`;
      $('#edmore').onclick=()=>{$('#edrev').classList.add('hidden');$('#edwrap').classList.remove('hidden');$('#edreview').disabled=true;E.cm.refresh();E.cm.focus();$('#ednote').innerHTML=''};
      $('#edfiles').onclick=()=>{FB.ed=null;openFiles(FB.server,E.dir)}}
    catch(e){$('#edrev').insertAdjacentHTML('afterbegin',`<div class="note bad" style="margin-bottom:8px">${esc(e.message)}</div>`)}})}
// live stats
async function fetchStats(servers){
  await Promise.all(servers.map(async s=>{try{const j=await raw('/api/stats',{server:s});if(j.ok)for(const[n,v] of Object.entries(j.stats))S.stats[s+'/'+n]=v}catch(e){}}));
}
function liveServers(){return S.fserver?[S.fserver]:[...new Set(S.containers.map(c=>c.server))]}
async function scanQuiet(server){   // live mode: pick up containers that started, stopped or restarted, without the spinner
  try{const j=await api('/api/scan',{server});if(!S.liveTimer||S.fserver!==server)return;mergeFleet(j.servers);S.containers=j.containers;
    const ks=new Set(S.containers.map(c=>c.server+'/'+c.name));[...S.picked].forEach(k=>ks.has(k)||S.picked.delete(k));renderLHead();
    if(S.sel&&S.sel.kind!=='pm2'){const c=S.containers.find(c=>c.server===S.sel.server&&c.name===S.sel.name);if(c){S.sel=c;renderHead()}}}catch(_){}}
async function liveTick(){if(S.mode==='pm2')return pm2Scan(true);S.liveN=(S.liveN||0)+1;
  await Promise.all([fetchStats(liveServers()),S.fserver&&S.liveN%2===0?scanQuiet(S.fserver):null]);if(S.view==='list'&&S.liveTimer){renderRows(true);updGauges()}}
function startLive(){stopLive();S.liveTimer=setInterval(liveTick,5000);renderRows();liveTick()}
function stopLive(){clearInterval(S.liveTimer);S.liveTimer=null}
$('#flive').checked=(()=>{try{return localStorage.getItem('dctl.live')!=='0'}catch(_){return true}})();
$('#flive').onchange=e=>{try{localStorage.setItem('dctl.live',e.target.checked?'1':'0')}catch(_){}if(e.target.checked){startLive();toast('Live stats on — refreshing every 5s','info',2500)}else{stopLive();renderRows()}};

// ---------- detail drawer ----------
function dirty(){return S.file&&edVal()!==null&&edVal()!==S.orig}
async function closeDetail(){if(!S.sel)return true;
  if(dirty()&&!await confirmBox('You have unsaved compose changes. Discard them?','Discard',true))return false;
  forceCloseDetail();return true}
function forceCloseDetail(){discardTmp();stopLogs();S.sel=null;S.file=null;S.cm=null;S.ta=null;$('#detail').classList.add('hidden');$('#scrim').classList.add('hidden');if(S.view==='list')renderRows()}
$('#scrim').onclick=()=>closeDetail();
async function select(c){
  if(S.sel&&!(S.sel.server===c.server&&S.sel.name===c.name)&&dirty()&&!await confirmBox('You have unsaved compose changes. Discard them?','Discard',true))return;
  discardTmp();stopLogs();S.sel=c;S.file=null;S.cm=null;S.ta=null;S.tab='overview';
  $('#detail').classList.remove('hidden');$('#scrim').classList.remove('hidden');renderRows();renderDetail();
  if(c.running&&!S.stats[c.server+'/'+c.name])fetchStats([c.server]).then(updGauges);
}
function renderDetail(){
  const c=S.sel,dis=c.files.length?'':'disabled title="not a docker compose container"';
  $('#detail').innerHTML=`<div class="dr-head"><div class="dr-ico ${c.running?'run':'stop'}">${ic('box',24)}</div><div class="grow" id="dh" style="min-width:0"></div>
      <button class="icon-btn" id="dclose" title="Close (Esc)">${ic('x',18)}</button></div>
    <div class="dr-actions">
      <button class="act" data-a="restart">${ic('restart',15)}<span>Restart</span></button>
      <button class="act danger" data-a="stop">${ic('stop',13)}<span>Stop</span></button>
      <button class="act" data-a="start">${ic('play',13)}<span>Start</span></button>
      <button class="act danger" id="crm" title="Delete this container">${ic('trash',14)}<span>Remove</span></button>
      <span class="vsep"></span>
      <button class="act accent" data-c="up" ${dis}>${ic('up',15)}<span>Compose up -d</span></button>
      <button class="act" data-c="pull" ${dis}>${ic('download',15)}<span>Pull &amp; up</span></button>
      <button class="act" data-c="ps" ${dis}>${ic('list',15)}<span>Compose ps</span></button>
      <span class="vsep"></span>
      <button class="act accent" id="cshell" ${c.running?'':'disabled title="container is not running"'}>${ic('term',15)}<span>Shell</span></button>
      <button class="act" id="cfiles" title="Browse files inside this container and download them">${ic('folder',15)}<span>Files</span></button>
    </div>
    <div class="dgauges" id="dg"></div>
    <pre id="out" class="term hidden"></pre>
    <nav class="tabs">${[['overview','info'],['compose','file'],['logs','logs'],['exec','term'],['inspect','code']].map(([t,i])=>`<a data-t="${t}" class="${t===S.tab?'on':''}">${ic(i,14)} ${t[0].toUpperCase()+t.slice(1)}</a>`).join('')}<i class="ink"></i></nav>
    <div id="tabbody"></div>`;
  renderHead();updGauges();
  $('#dclose').onclick=()=>closeDetail();
  $$('#detail [data-a]').forEach(b=>b.onclick=()=>doAction(b,b.dataset.a));
  $$('#detail [data-c]').forEach(b=>b.onclick=()=>doCompose(b,b.dataset.c));
  $('#cshell').onclick=()=>openTerm(c.server,{container:c.name});
  $('#crm').onclick=()=>removeC(c);$('#cfiles').onclick=()=>openFiles(c.server,null,{container:c.name,running:c.running});
  $$('#detail .tabs a').forEach(a=>a.onclick=async()=>{
    if(S.tab==='compose'&&a.dataset.t!=='compose'&&dirty()&&!await confirmBox('Leave the editor? Unsaved changes will be lost.','Leave',true))return;
    if(S.tab==='compose'&&a.dataset.t!=='compose'){discardTmp();S.file=null;S.cm=null;S.ta=null}
    stopLogs();S.tab=a.dataset.t;$$('#detail .tabs a').forEach(x=>x.classList.toggle('on',x===a));moveInk();renderTab()});
  requestAnimationFrame(moveInk);renderTab();
}
function moveInk(){const a=$('#detail .tabs a.on'),ink=$('#detail .ink');if(a&&ink){ink.style.left=a.offsetLeft+'px';ink.style.width=a.offsetWidth+'px'}}
function renderHead(){const c=S.sel,dh=$('#dh');if(!dh)return;const ss=shortStatus(c.status);
  dh.innerHTML=`<h2>${esc(c.name)}</h2><div class="dr-sub"><span class="spill ${c.running?'run':'stop'}"><i></i>${c.running?'RUNNING':'STOPPED'}</span>
    <span>${esc(ss.t)}</span><span>${ic('server',13)} ${esc(c.server)}</span>${c.project?`<span>${ic('box',13)} ${esc(c.project)}</span>`:''}</div>`;
  const ico=$('#detail .dr-ico');if(ico)ico.className='dr-ico '+(c.running?'run':'stop')}
function updGauges(){const c=S.sel,dg=$('#dg');if(!c||!dg)return;if(c.kind==='pm2')return updPGauges();
  const st=S.stats[c.server+'/'+c.name];
  if(!c.running){dg.innerHTML='';dg.style.display='none';return}dg.style.display='';
  const g=(l,v,suf,p)=>`<div class="g"><div class="l">${l}</div><div class="v">${v}<small>${suf}</small></div>${p!=null?`<div class="t"><i style="width:${clamp(p)}%;background:${pgrad(p)}"></i></div>`:''}</div>`;
  dg.innerHTML=st?g('CPU',st.cpu.toFixed(1),'%',st.cpu)+g('Memory',st.memp.toFixed(1),'%',st.memp)+g('Mem used',esc(st.mem.split('/')[0].trim()),'/ '+esc((st.mem.split('/')[1]||'').trim()))+g('Net I/O',esc(st.net.split('/')[0].trim()),'↓ '+esc((st.net.split('/')[1]||'').trim())+' ↑')
    :['CPU','Memory','Mem used','Net I/O'].map(l=>`<div class="g"><div class="l">${l}</div><div class="v"><span class="spin"></span></div></div>`).join('');
}
function out(html){const o=$('#out');o.classList.remove('hidden');o.innerHTML=html;o.scrollTop=o.scrollHeight}
async function doAction(btn,a){const c=S.sel;
  if(a!=='start'&&!await confirmBox(`<b>${a}</b> container <code>${esc(c.name)}</code> on <b>${esc(c.server)}</b>?`,a[0].toUpperCase()+a.slice(1),a==='stop'))return;
  await busy(btn,async()=>{try{const j=await api('/api/action',{server:c.server,name:c.name,action:a});
    out(`<span class="p">$ docker ${a} ${esc(c.name)}</span>\n${esc(j.output)}\n<span class="${j.rc?'e':'ok'}">${j.rc?'✗ failed':'✓ done'}</span>`);
    toast(`${esc(c.name)}: ${a} ${j.rc?'failed':'done'}`,j.rc?'bad':'ok')}catch(e){out(`<span class="e">${esc(e.message)}</span>`);toast(esc(e.message),'bad')}});
  await scan(c.server);delete S.stats[c.server+'/'+c.name];if(S.sel&&S.sel.running)fetchStats([c.server]).then(updGauges);updGauges()}
function removeC(c){
  modal(`<h3>${ic('alert',20)} Remove container</h3>
   <p>Delete <code>${esc(c.name)}</code> on <b>${esc(c.server)}</b>. This cannot be undone. The image and named volumes stay.</p>
   ${c.project?`<p class="dim">This container belongs to compose project <b>${esc(c.project)}</b>. Running <i>Compose up -d</i> later will create it again.</p>`:''}
   <div class="form">
    ${c.running?`<label style="display:flex!important;gap:8px;align-items:center"><input type="checkbox" id="rmf" style="width:auto!important"> It is <b>running</b>: force (stop + remove)</label>`:''}
    <label style="display:flex!important;gap:8px;align-items:center"><input type="checkbox" id="rmv" style="width:auto!important"> Also delete its anonymous volumes (<code>-v</code>)</label>
    <label>Type <code>${esc(c.name)}</code> to confirm</label><input id="rmn" autocomplete="off" spellcheck="false"></div>
   <div id="rmerr" class="err" style="margin-top:8px"></div>
   <div class="row end" style="margin-top:6px"><button class="btn ghost" id="mno">Cancel</button><button class="btn danger" id="myes" disabled>${ic('trash',14)} Remove</button></div>`);
  const ok=()=>{$('#myes').disabled=!($('#rmn').value===c.name&&(!c.running||$('#rmf').checked))};
  $('#rmn').oninput=ok;if($('#rmf'))$('#rmf').onchange=ok;$('#rmn').focus();
  $('#rmn').onkeydown=e=>{if(e.key==='Enter'&&!$('#myes').disabled)$('#myes').click()};
  $('#mno').onclick=closeModal;
  $('#myes').onclick=async()=>{const b=$('#myes');b.disabled=true;b.innerHTML='<span class="spin"></span> Removing…';
    const force=!!($('#rmf')&&$('#rmf').checked),volumes=$('#rmv').checked;
    try{const j=await api('/api/remove',{server:c.server,name:c.name,confirm:$('#rmn').value,force,volumes});
      if(j.rc){$('#rmerr').textContent=j.output||('failed (exit '+j.rc+')');b.disabled=false;b.innerHTML=ic('trash',14)+' Remove';return}
      closeModal();toast(`${esc(c.name)} removed from ${esc(c.server)}`,'ok');
      if(S.sel&&S.sel.server===c.server&&S.sel.name===c.name)closeDetail();await scan(c.server)}
    catch(e){$('#rmerr').textContent=e.message;b.disabled=false;b.innerHTML=ic('trash',14)+' Remove'}};
}
async function doCompose(btn,cmd){const c=S.sel;
  const label={up:'docker compose up -d',pull:'docker compose pull && up -d',ps:'docker compose ps'}[cmd];
  if(cmd!=='ps'&&!await confirmBox(`Run <code>${label}</code> for project <b>${esc(c.project)}</b> on <b>${esc(c.server)}</b>?<br><br><span class="dim">${esc(c.workdir)}<br>Only services whose config or image changed get recreated.</span>`,'Run'))return;
  const head=`<span class="p">$ ${label}</span>  <span class="ts">${esc(c.server)}:${esc(c.workdir)}</span>\n`;
  out(head+'<span class="spin"></span> running…');
  await busy(btn,async()=>{try{const j=await api('/api/compose',{server:c.server,name:c.name,cmd});
      out(head+colorize(j.output||'(no output)')+`\n<span class="${j.rc?'e':'ok'}">${j.rc?'✗ exit '+j.rc:'✓ done'}</span>`);
      if(cmd!=='ps')toast(`${esc(c.project)}: ${label} ${j.rc?'failed':'completed'}`,j.rc?'bad':'ok')}
    catch(e){out(head+`<span class="e">${esc(e.message)}</span>`);toast(esc(e.message),'bad')}});
  if(cmd!=='ps')scan(c.server)}
function colorize(t){return t.split('\n').map(l=>{const e=esc(l);
  if(/\b(error|fatal|panic|exception|failed|refused|denied)\b/i.test(l))return`<span class="e">${e}</span>`;
  if(/\b(warn|warning|deprecated)\b/i.test(l))return`<span class="w">${e}</span>`;
  return e.replace(/^(\d{4}-\d\d-\d\d[T ][\d:.,]+Z?)/,'<span class="ts">$1</span>')}).join('\n')}

function renderTab(){({overview,compose,logs,exec,inspect})[S.tab]($('#tabbody'),S.sel)}
function overview(b,c){
  const ssh=c.workdir?`ssh -t ${c.server} "cd '${c.workdir}' && exec \\$SHELL -l"`:`ssh ${c.server}`;
  const ports=portsOf(c.ports);
  const cp=v=>`<span class="copy" data-copy="${esc(v)}" title="copy">${ic('copy',13)}</span>`;
  b.innerHTML=`<div class="kv">
   <div>Image</div><div><code>${esc(c.image)}</code>${cp(c.image)}</div>
   <div>Status</div><div>${esc(c.status)}</div>
   <div>Ports</div><div class="ports">${ports.length?ports.map(p=>`<span class="port">${p}</span>`).join(''):`<span class="dim">${esc(c.ports||'none')}</span>`}</div>
   ${c.files.length?`<div>Project</div><div><b>${esc(c.project)}</b> <span class="dim">· service</span> ${esc(c.service)}</div>
   <div>Directory</div><div><code>${esc(c.workdir)}</code>${cp(c.workdir)}</div>
   <div>Compose file</div><div>${c.files.map(f=>`<code>${esc(f)}</code>${cp(f)}`).join('<br>')}</div>`
   :`<div>Compose</div><div class="dim">Not started by docker compose (plain docker run / Portainer) — no compose file to edit.</div>`}
   <div>Terminal</div><div><code>${esc(ssh)}</code>${cp(ssh)}</div>
  </div>
  ${c.files.length?`<div class="row"><button class="btn primary" id="goedit">${ic('edit',15)} Edit compose file</button><button class="btn" id="golog">${ic('logs',15)} View logs</button></div>`:''}`;
  b.querySelectorAll('[data-copy]').forEach(x=>x.onclick=()=>{navigator.clipboard?.writeText(x.dataset.copy).then(()=>toast('Copied to clipboard','ok',1500),()=>toast('Copy failed','bad'))});
  $('#goedit')&&($('#goedit').onclick=()=>$('#detail [data-t=compose]').click());
  $('#golog')&&($('#golog').onclick=()=>$('#detail [data-t=logs]').click());
}
// --- compose editor ---
function edVal(){return S.cm?S.cm.getValue():S.ta?S.ta.value:null}
function discardTmp(){if(S.tmp&&S.sel){raw('/api/discard',{server:S.sel.server,tmp:S.tmp}).catch(()=>{});S.tmp=null}}
function compose(b,c){
  if(!c.files.length){b.innerHTML='<div class="note warn">This container was not started with docker compose, so there is no compose file to edit.</div>';return}
  S.file=S.file||c.files[0];
  b.innerHTML=`<div class="row"><select id="fsel" style="max-width:100%;flex:1;font-family:var(--mono);font-size:12px">${c.files.map(f=>`<option ${f===S.file?'selected':''}>${esc(f)}</option>`).join('')}</select>
    <button class="btn" id="freload">${ic('restart',14)} Reload</button><button class="btn primary" id="freview" disabled>${ic('check',14)} Review &amp; save</button></div>
    <div id="fnote" class="dim small"></div>
    <div id="editor"><div class="sk" style="flex:1;min-height:360px"></div></div><div id="review" class="hidden" style="display:flex;flex-direction:column;gap:12px"></div>`;
  $('#fsel').onchange=async e=>{if(dirty()&&!await confirmBox('Discard unsaved changes?','Discard',true)){e.target.value=S.file;return}S.file=e.target.value;loadFile()};
  $('#freload').onclick=async()=>{if(dirty()&&!await confirmBox('Discard unsaved changes and reload from the server?','Reload',true))return;loadFile()};
  $('#freview').onclick=e=>review(e.currentTarget);
  loadFile();
}
async function loadFile(){const c=S.sel;discardTmp();S.cm=null;S.ta=null;
  $('#editor').innerHTML='<div class="sk" style="flex:1;min-height:360px"></div>';$('#review').classList.add('hidden');$('#editor').classList.remove('hidden');
  try{const j=await api('/api/file',{server:c.server,name:c.name,path:S.file});S.orig=j.content;S.sha=j.sha;
    $('#fnote').innerHTML=(j.elevated?`${ic('lock',12)} opened with sudo · `:'')+'<kbd>Ctrl</kbd> <kbd>S</kbd> to review · a backup is kept on every save';
    $('#editor').innerHTML='';
    if(window.CodeMirror){S.cm=CodeMirror($('#editor'),{value:j.content,mode:'yaml',theme:'material-darker',lineNumbers:true,indentUnit:2,tabSize:2,
      extraKeys:{Tab:cm=>cm.replaceSelection('  '),'Ctrl-S':()=>$('#freview').click(),'Cmd-S':()=>$('#freview').click()}});setTimeout(()=>S.cm&&S.cm.refresh(),20)}
    else{S.ta=document.createElement('textarea');S.ta.spellcheck=false;S.ta.value=j.content;$('#editor').appendChild(S.ta);
      S.ta.onkeydown=e=>{if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();$('#freview').click()}}}
    $('#freview').disabled=false;
  }catch(e){$('#editor').innerHTML=`<div class="note bad" style="flex:1">${esc(e.message)}</div>`}}
function diffHtml(d){return d.split('\n').map(l=>`<span class="${l.startsWith('+++')||l.startsWith('---')||l.startsWith('@@')?'h':l.startsWith('+')?'a':l.startsWith('-')?'d':''}">${esc(l)}</span>`).join('\n')}
async function review(btn){const c=S.sel,content=edVal();discardTmp();
  await busy(btn,async()=>{let j;try{j=await api('/api/validate',{server:c.server,name:c.name,path:S.file,content,orig:S.orig})}
    catch(e){toast(esc(e.message),'bad');return}
    if(!j.diff){toast('No changes to save','info',2000);return}
    S.tmp=j.tmp;
    $('#editor').classList.add('hidden');const r=$('#review');r.classList.remove('hidden');
    r.innerHTML=`<div class="note ${j.valid?'good':'bad'}">${j.valid?`${ic('check',15)} <b>Valid compose file</b> <span class="mut">— checked with docker compose config on ${esc(c.server)}</span>`:`${ic('alert',15)} <b>docker compose says this file is invalid</b>`}</div>
      ${j.output.trim()?`<pre class="term">${colorize(j.output.trim())}</pre>`:''}
      <div class="mut small" style="font-weight:600;letter-spacing:1px">CHANGES</div><pre class="term chrome diff">${diffHtml(j.diff)}</pre>
      ${j.valid?'':'<label class="toggle"><input type="checkbox" id="anyway"><span></span>Save anyway</label>'}
      <div class="row end"><button class="btn ghost" id="back">${ic('back',14)} Back to editor</button><button class="btn primary" id="dosave" ${j.valid?'':'disabled'}>${ic('check',15)} Save to server</button></div>`;
    $('#anyway')&&($('#anyway').onchange=e=>$('#dosave').disabled=!e.target.checked);
    $('#back').onclick=()=>{discardTmp();r.classList.add('hidden');$('#editor').classList.remove('hidden');S.cm&&S.cm.refresh()};
    $('#dosave').onclick=e=>save(e.currentTarget,content);
  })}
async function save(btn,content){const c=S.sel;
  await busy(btn,async()=>{try{const j=await api('/api/save',{server:c.server,name:c.name,path:S.file,tmp:S.tmp,sha:S.sha});
      S.tmp=null;S.orig=content;S.sha=j.sha;toast('Compose file saved','ok');
      $('#review').innerHTML=`<div class="note good">${ic('check',15)} <b>Saved</b> <code>${esc(c.server)}:${esc(S.file)}</code>${j.elevated?' <span class="mut">(with sudo)</span>':''}<br><span class="mut small">Backup: <code>${esc(j.backup)}</code></span></div>
        <div class="note warn">Changes go live only when containers are recreated.</div>
        <div class="row"><button class="btn primary" id="apply">${ic('up',15)} Apply now · compose up -d</button><button class="btn ghost" id="back2">Continue editing</button></div>`;
      $('#apply').onclick=e=>doCompose(e.currentTarget,'up');
      $('#back2').onclick=()=>{$('#review').classList.add('hidden');$('#editor').classList.remove('hidden');S.cm&&S.cm.refresh()};
    }catch(e){toast(esc(e.message),'bad',7000);$('#review').insertAdjacentHTML('afterbegin',`<div class="note bad">${esc(e.message)}</div>`)}})}
// --- logs ---
function stopLogs(){clearInterval(S.logTimer);S.logTimer=null}
function logs(b,c){
  b.innerHTML=`<div class="row"><select id="ltail"><option value="100">Last 100</option><option value="300" selected>Last 300</option><option value="1000">Last 1000</option><option value="5000">Last 5000</option></select>
   <div class="search" style="min-width:160px"><span>${ic('search',14)}</span><input id="lgrep" placeholder="Filter lines…"></div>
   <label class="toggle"><input type="checkbox" id="lauto"><span></span>Follow</label><button class="btn" id="lref">${ic('restart',14)}</button></div>
   <pre id="logs" class="term chrome" style="flex:1;min-height:340px"><span class="spin"></span></pre>`;
  let text='';
  const show=()=>{const g=$('#lgrep')?.value.toLowerCase(),el=$('#logs');if(!el)return;const atBottom=el.scrollHeight-el.scrollTop-el.clientHeight<60;
    const lines=g?text.split('\n').filter(l=>l.toLowerCase().includes(g)).join('\n'):text;el.innerHTML=colorHL(lines,$('#lgrep').value);
    if(atBottom||!el.dataset.init){el.scrollTop=el.scrollHeight;el.dataset.init=1}};
  const load=async()=>{try{const j=await api('/api/logs',{server:c.server,name:c.name,tail:$('#ltail').value});text=j.output||'(no logs)';show()}catch(e){text='ERROR: '+e.message;show()}};
  $('#lref').onclick=load;$('#ltail').onchange=load;$('#lgrep').oninput=show;
  $('#lauto').onchange=e=>{stopLogs();if(e.target.checked){S.logTimer=setInterval(load,3000);load()}};
  load();
}
// --- exec ---
function exec(b,c){
  b.innerHTML=`<div class="dim small">Runs a one-off command inside the container (non-interactive). For a full shell use <code>dctl sh ${esc(c.name)}</code> in a terminal.</div>
   <div class="search"><span class="p mono" style="color:#9d8bff">$</span><input id="xcmd" placeholder="env | sort      ls -la /app      cat /etc/hosts"><button class="btn primary" id="xrun" style="margin:4px -8px 4px 0;padding:6px 12px">${ic('play',12)} Run</button></div>
   <pre id="xout" class="term chrome" style="flex:1;min-height:260px"><span class="ts">output appears here</span></pre>`;
  const hist=[];
  const go=()=>busy($('#xrun'),async()=>{const cmd=$('#xcmd').value.trim();if(!cmd)return;
    try{const j=await api('/api/exec',{server:c.server,name:c.name,cmd});hist.push(`<span class="p">$ ${esc(cmd)}</span>\n${colorize(j.output)}${j.rc?`\n<span class="e">✗ exit ${j.rc}</span>`:''}`)}
    catch(e){hist.push(`<span class="p">$ ${esc(cmd)}</span>\n<span class="e">${esc(e.message)}</span>`)}
    $('#xout').innerHTML=hist.join('\n\n');$('#xout').scrollTop=1e9;$('#xcmd').select()});
  $('#xrun').onclick=go;$('#xcmd').onkeydown=e=>{if(e.key==='Enter')go()};$('#xcmd').focus();
}
// --- inspect ---
async function inspect(b,c){b.innerHTML='<pre id="ins" class="term" style="flex:1"><span class="spin"></span></pre>';
  try{const j=await api('/api/inspect',{server:c.server,name:c.name});
    $('#ins').innerHTML=esc(j.output).replace(/(&quot;[^&]*?&quot;)(\s*:)/g,'<span class="p">$1</span>$2').replace(/:\s(&quot;.*?&quot;)/g,': <span class="ok">$1</span>')}
  catch(e){$('#ins').textContent=e.message}}

// ---------- command palette ----------
function openPal(){$('#pal').classList.remove('hidden');$('#palq').value='';S.palI=0;palRender();$('#palq').focus();setTimeout(()=>$('#palq').focus(),20);
  if(!S.idx||Date.now()-S.idx.t>90000){S.palLoading=true;palRender();ensureIndex(false).then(()=>{S.palLoading=false;if(!$('#pal').classList.contains('hidden'))palRender()}).catch(()=>{S.palLoading=false;palRender()})}}
function closePal(){$('#pal').classList.add('hidden')}
function palRender(){
  const raw=$('#palq').value.trim(),q=raw.toLowerCase().split(/\s+/).filter(Boolean),hit=s=>q.every(w=>s.toLowerCase().includes(w));
  const port=/^:?\d{2,5}$/.test(raw)?raw.replace(':',''):null;
  const I=S.idx||{containers:S.containers,pm2:[],pages:{docker:[],pm2:[]},servers:S.fleet};
  const pageOf=n=>I.pages.pm2.includes(n)?'pm2':I.pages.docker.includes(n)?'docker':null;
  const items=[];
  (I.servers||[]).filter(s=>pageOf(s.name)&&hit(s.name+' '+(s.host||''))).forEach(s=>items.push({k:'s',s,label:s.name,sub:(s.host||'')+' · '+PGT[pageOf(s.name)]+' page',icon:'server'}));
  const cm=port?I.containers.filter(c=>new RegExp(`:${port}->|->${port}/`).test(c.ports)):I.containers.filter(c=>hit(c.name+' '+c.server+' '+c.image+' '+c.project+' '+c.ports));
  cm.slice(0,60).forEach(c=>{const pp=port?(portsOf(c.ports).find(x=>x.includes(port))||''):'';items.push({k:'c',c,label:c.name,sub:(pp?pp+' · ':'')+c.server+' · '+(c.project||'standalone')+' · '+c.image,icon:'box'})});
  if(!port)(I.pm2||[]).filter(p=>hit(p.name+' '+p.server+' '+p.user+' '+p.script+' '+p.cwd)).slice(0,40).forEach(p=>items.push({k:'p',p,label:p.name,sub:`${p.server} · user ${p.user} · ${p.status} · ${p.script}`,icon:'bolt'}));
  S.palItems=items.slice(0,80);S.palI=Math.min(S.palI,Math.max(0,S.palItems.length-1));
  const col=it=>it.k==='s'?'#9d8bff':it.k==='c'?(it.c.running?'var(--ok)':'var(--bad)'):(it.p.status==='online'?'var(--ok)':'var(--bad)');
  $('#palres').innerHTML=(S.palLoading?`<div class="palnote"><span class="spin"></span> indexing all servers…</div>`:'')+(port?`<div class="palnote">${ic('search',13)} containers publishing port <b>${esc(port)}</b></div>`:'')+
    S.palItems.map((it,i)=>`<div class="pi ${i===S.palI?'on':''}" data-i="${i}"><div class="pic" style="color:${col(it)}">${ic(it.icon,15)}</div>
    <div style="min-width:0;flex:1"><b>${esc(it.label)}</b><div class="sub" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(it.sub)}</div></div><span class="dim small">${{s:'server',c:'container',p:'pm2'}[it.k]}</span></div>`).join('')
    ||(S.palLoading?'':`<div class="empty" style="padding:30px">No matches</div>`);
  $('#palres .pi.on')?.scrollIntoView({block:'nearest'});
}
function setPage(pg){if(S.page!==pg){S.page=pg;try{localStorage.setItem('dctl.page',pg)}catch(_){}S.layout=null}renderPTabs();if(S.idx)S.fleet=S.idx.servers}
async function palGo(i){const it=S.palItems[i];if(!it)return;closePal();if(!await closeDetail())return;
  if(it.k==='s'){const I=S.idx;setPage(I&&I.pages.pm2.includes(it.s.name)?'pm2':'docker');
    if(S.page==='docker'&&it.s.state==='need_password'&&!it.s.unlocked&&!await askSudo(it.s.name))return;openServer(it.s.name)}
  else if(it.k==='c'){setPage('docker');openServer(it.c.server,it.c)}
  else{setPage('pm2');S.pendingP={user:it.p.user,name:it.p.name};S.fserver='';openServer(it.p.server)}}
$('#palq').oninput=()=>{S.palI=0;palRender()};
$('#palq').onkeydown=e=>{if(e.key==='ArrowDown'){e.preventDefault();S.palI=Math.min(S.palI+1,S.palItems.length-1);palRender()}
  else if(e.key==='ArrowUp'){e.preventDefault();S.palI=Math.max(S.palI-1,0);palRender()}else if(e.key==='Enter')palGo(S.palI)};
$('#palres').onclick=e=>{const p=e.target.closest('.pi');if(p)palGo(+p.dataset.i)};
$('#pal').onclick=e=>{if(e.target.id==='pal')closePal()};
$('#openpal').onclick=openPal;

document.addEventListener('keydown',e=>{
  if(document.activeElement&&document.activeElement.closest&&document.activeElement.closest('#termp'))return;
  const typing=/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)||document.activeElement.closest?.('.CodeMirror');
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#pal').classList.contains('hidden')?openPal():closePal();return}
  if(e.key==='Escape'){
    if(!$('#modal2').classList.contains('hidden')){$('#d2no')?.click();return}
    if(!$('#modal').classList.contains('hidden')){$('#mno')?.click();return}
    if(!$('#pal').classList.contains('hidden')){closePal();return}
    if(S.sel&&!document.activeElement.closest?.('.CodeMirror')){closeDetail();return}}
  if(e.key==='/'&&!typing&&S.view==='list'){e.preventDefault();$('#q').focus()}
});
window.addEventListener('resize',moveInk);
window.addEventListener('beforeunload',e=>{if(dirty()||edDirty()){e.preventDefault();e.returnValue=''}});
loadHome();
</script></body></html>"""


def _icon_path():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    return p if os.path.exists(p) else None


def _find_chromium():
    """A Chrome / Edge / Chromium / Brave binary, for its chromeless --app window."""
    import shutil, sys
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
              "microsoft-edge-stable", "brave-browser", "msedge", "chrome"):
        p = shutil.which(n)
        if p:
            return p
    cands = []
    if IS_WIN:
        for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")):
            cands += [os.path.join(base, r"Microsoft\Edge\Application\msedge.exe"),
                      os.path.join(base, r"Google\Chrome\Application\chrome.exe")]
    elif sys.platform == "darwin":
        cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium",
                 "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"]
    return next((c for c in cands if c and os.path.exists(c)), None)


def open_window(url):
    """Show the dashboard in its own application window and block until it is closed.
    Returns False if no window could be shown (caller falls back to the normal browser)."""
    import sys
    try:
        import webview                       # pywebview: native window (WebKitGTK / WebKit / Edge WebView2)
    except Exception:
        webview = None
    if webview is not None:
        try:
            if sys.platform.startswith("linux"):
                try:                         # so the taskbar matches dctl.desktop (StartupWMClass=dctl)
                    import gi
                    from gi.repository import GLib
                    GLib.set_prgname("dctl"); GLib.set_application_name("dctl")
                except Exception:
                    pass
            for k, v in (("ALLOW_DOWNLOADS", True), ("OPEN_EXTERNAL_LINKS_IN_BROWSER", True)):
                try:
                    webview.settings[k] = v
                except Exception:
                    pass
            webview.create_window("dctl", url, width=1440, height=920, min_size=(960, 620), text_select=True,
                                  background_color="#0b0d18")
            kw = {"private_mode": False, "storage_path": os.path.join(CONF, "webview")}
            if _icon_path():
                kw["icon"] = _icon_path()
            if IS_WIN:
                kw["gui"] = "edgechromium"
            try:
                webview.start(**kw)
            except TypeError:                # older pywebview without some options
                webview.start()
            return True
        except Exception as e:
            print(f"dctl-ui: native window not available ({e}); trying a browser app window", flush=True)
    exe = _find_chromium()
    if exe:
        prof = os.path.join(CONF, "appwindow")   # own profile, so the process lives exactly as long as the window
        t0 = time.time()
        try:
            subprocess.run([exe, f"--app={url}", f"--user-data-dir={prof}", "--class=dctl", "--window-size=1440,920",
                            "--no-first-run", "--no-default-browser-check"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return False
        if time.time() - t0 < 3:                 # browser handed the window to another process: keep serving
            print("dctl-ui: the app window runs in your browser; press Ctrl+C here to stop dctl", flush=True)
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
        return True
    return False


def main():
    import sys
    args = sys.argv[1:]
    if args[:1] in (["-V"], ["--version"]):
        print("dctl-ui", __import__("dctl").__version__); return
    if args[:1] in (["-h"], ["--help"]):
        print(__doc__.strip()); return
    mode = "window"
    if "--browser" in args or os.environ.get("DCTL_BROWSER"):
        mode = "browser"
    if "--server" in args or os.environ.get("DCTL_NO_BROWSER"):
        mode = "server"
    url = f"http://127.0.0.1:{PORT}/"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError as e:
        try:                       # already running (e.g. opened again from the app menu)? just show it
            import urllib.request
            if b"dctl" in urllib.request.urlopen(url, timeout=3).read(4096):
                print(f"dctl is already running at {url}", flush=True)
                if mode == "window" and not open_window(url):
                    webbrowser.open(url)
                elif mode == "browser":
                    webbrowser.open(url)
                return
        except Exception:
            pass
        raise SystemExit(f"dctl-ui: cannot listen on 127.0.0.1:{PORT} ({e.strerror}). "
                         f"Another program uses this port. Start on another one with DCTL_PORT=9000 dctl-ui")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"dctl running at {url}", flush=True)
    try:
        if mode == "window":
            if open_window(url):
                return             # window closed -> quit
            print("dctl-ui: no app window available, opening your browser", flush=True)
            mode = "browser"
        if mode == "browser":
            threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        print("Press Ctrl+C to stop.", flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
