"""Kubernetes page for dctl (Lens-like): clusters from this computer's kubeconfig and from servers
reached over SSH (kubectl / k3s / microk8s on the server). Nothing is installed anywhere: everything
runs through the kubectl (and helm) that already exist locally or on the server."""
import atexit
import calendar
import json
import os
import random
import re
import shlex
import shutil
import signal
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

U = None          # the dctl.ui module (set by init)


def init(ui):
    global U
    U = ui


def q(s):
    return shlex.quote(str(s))


class KErr(Exception):
    pass


# ---------------- config: SSH clusters, hidden contexts ----------------
def _cfg_path():
    return os.path.join(U.CONF, "k8s.json")


def load_cfg():
    try:
        c = json.load(open(_cfg_path()))
    except (OSError, ValueError):
        c = {}
    c.setdefault("ssh", [])
    c.setdefault("hidden", [])
    return c


def save_cfg(c):
    os.makedirs(U.CONF, exist_ok=True)
    tmp = _cfg_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(c, f, indent=2)
    os.replace(tmp, _cfg_path())


def kubectl_bin():
    return shutil.which("kubectl")


def helm_bin():
    return shutil.which("helm")


_CTX = {"t": 0, "v": None}


def contexts():
    """Contexts from the local kubeconfig (no secrets: kubectl redacts them)."""
    if _CTX["v"] is not None and time.time() - _CTX["t"] < 10:
        return _CTX["v"]
    kb = kubectl_bin()
    out = {"current": "", "list": []}
    if kb:
        try:
            p = subprocess.run([kb, "config", "view", "-o", "json"], capture_output=True, text=True, timeout=15)
            j = json.loads(p.stdout or "{}")
            out["current"] = j.get("current-context") or ""
            for c in j.get("contexts") or []:
                cx = c.get("context") or {}
                out["list"].append({"name": c.get("name", ""), "cluster": cx.get("cluster", ""),
                                    "user": cx.get("user", ""), "namespace": cx.get("namespace", "")})
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    _CTX.update(t=time.time(), v=out)
    return out


def parse_cid(cid):
    """cid is 'ctx:<context>' (local kubeconfig) or 'ssh:<server>'. Returns (kind, name) after checking it exists."""
    cid = str(cid or "")
    if cid.startswith("ctx:"):
        n = cid[4:]
        if not any(c["name"] == n for c in contexts()["list"]):
            raise KErr(f"unknown context: {n}")
        return "ctx", n
    if cid.startswith("ssh:"):
        n = cid[4:]
        if n not in load_cfg()["ssh"] or n not in U.servers():
            raise KErr(f"unknown cluster: {n}")
        return "ssh", n
    raise KErr("bad cluster id")


def gkey(cid):
    """Name used for protection (and typed by the user to confirm): the context or server name."""
    return parse_cid(cid)[1]


# ---------------- running kubectl ----------------
KMODE = {}        # server -> how kubectl is reached there
PFX = {"kubectl": "kubectl", "k3s": "k3s kubectl", "microk8s": "microk8s kubectl",
       "sudo-kubectl": "$S kubectl", "sudo-k3s": "$S k3s kubectl", "sudo-microk8s": "$S microk8s kubectl"}
HELM_PFX = {"kubectl": "helm", "k3s": "env KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm", "microk8s": "microk8s helm3",
            "sudo-kubectl": "$S helm", "sudo-k3s": "$S env KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm",
            "sudo-microk8s": "$S microk8s helm3"}
RPATH = "PATH=$PATH:/usr/local/bin:/snap/bin; export PATH; "

KPROBE = r'''v(){ "$@" get --raw /version --request-timeout=8s >/dev/null 2>&1; }
has(){ command -v "$1" >/dev/null 2>&1; }
if has kubectl && v kubectl; then echo @@K=kubectl
elif has k3s && v k3s kubectl; then echo @@K=k3s
elif has microk8s && v microk8s kubectl; then echo @@K=microk8s
elif [ -n "$S" ] && has k3s && v $S k3s kubectl; then echo @@K=sudo-k3s
elif [ -n "$S" ] && has microk8s && v $S microk8s kubectl; then echo @@K=sudo-microk8s
elif [ -n "$S" ] && has kubectl && v $S kubectl; then echo @@K=sudo-kubectl
elif has kubectl || has k3s || has microk8s; then echo @@K=noaccess
else echo @@K=none; fi'''


def _ssh_sh(server, script, lazy, stdin=None, timeout=60):
    pre, sin = U._sudo_pre(server, lazy)
    data = (sin or b"") + (stdin or b"")
    rc, out, err = U.ssh(server, "sh -c " + q(RPATH + pre + script), data or None, timeout)
    if b"@@SUDOFAIL" in out:
        U.SUDO_PW.pop(server, None)
        raise U.NeedSudo(server)
    return rc, out, err


def kmode(server):
    m = KMODE.get(server)
    if m:
        return m
    rc, out, err = _ssh_sh(server, KPROBE, lazy=True, timeout=40)
    mm = re.search(rb"@@K=(\S+)", out)
    if not mm:
        raise KErr(f"{server}: " + (err.strip().splitlines() or ["could not check for kubectl"])[-1])
    m = mm.group(1).decode()
    if m == "none":
        raise KErr(f"{server}: kubectl, k3s or microk8s not found on this server")
    if m == "noaccess":
        info = U.INFO.get(server, {})
        if info.get("sudo") == "pass" and server not in U.SUDO_PW:
            raise U.NeedSudo(server)
        raise KErr(f"{server}: kubectl is installed but cannot reach the cluster as your user "
                   "(no kubeconfig, and sudo did not help)")
    KMODE[server] = m
    return m


def _clean_err(err):
    lines = [l for l in str(err).strip().splitlines() if l.strip() and not re.match(r"^[EWI]\d{4} ", l)]
    return "\n".join(lines[-4:]) or "kubectl failed"


def kexec(cid, args, stdin=None, timeout=45, helm=False):
    """Run kubectl (or helm) for a cluster. Returns (rc, stdout bytes, stderr str)."""
    kind, name = parse_cid(cid)
    if kind == "ctx":
        b = helm_bin() if helm else kubectl_bin()
        if not b:
            raise KErr(("helm" if helm else "kubectl") + " is not installed on this computer")
        argv = [b, "--kube-context" if helm else "--context", name] + list(args)
        try:
            p = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout, stdin=None if stdin else subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise KErr(f"{name}: timed out after {timeout}s")
        return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")
    m = kmode(name)
    pfx = (HELM_PFX if helm else PFX)[m]
    script = pfx + " " + " ".join(q(a) for a in args)
    rc, out, err = _ssh_sh(name, script, lazy=not m.startswith("sudo"), stdin=stdin, timeout=timeout)
    if rc == 127 and helm:
        raise KErr(f"{name}: helm is not installed on this server")
    return rc, out, err


def kcall(cid, args, stdin=None, timeout=45, helm=False):
    rc, out, err = kexec(cid, args, stdin, timeout, helm)
    if rc != 0:
        raise KErr(_clean_err(err or out.decode("utf-8", "replace")))
    return out.decode("utf-8", "replace")


def kjson(cid, args, timeout=45):
    out = kcall(cid, list(args) + ["-o", "json"], timeout=timeout)
    try:
        return json.loads(out)
    except ValueError:
        raise KErr("kubectl returned something that is not JSON")


# ---------------- kinds ----------------
# key -> (kubectl resource, namespaced, title)
KINDS = {
    "nodes": ("nodes", False, "Nodes"), "namespaces": ("namespaces", False, "Namespaces"), "events": ("events", True, "Events"),
    "pods": ("pods", True, "Pods"), "deployments": ("deployments.apps", True, "Deployments"),
    "daemonsets": ("daemonsets.apps", True, "DaemonSets"), "statefulsets": ("statefulsets.apps", True, "StatefulSets"),
    "replicasets": ("replicasets.apps", True, "ReplicaSets"), "jobs": ("jobs.batch", True, "Jobs"),
    "cronjobs": ("cronjobs.batch", True, "CronJobs"),
    "configmaps": ("configmaps", True, "ConfigMaps"), "secrets": ("secrets", True, "Secrets"),
    "resourcequotas": ("resourcequotas", True, "Resource Quotas"), "limitranges": ("limitranges", True, "Limit Ranges"),
    "hpa": ("horizontalpodautoscalers.autoscaling", True, "HPA"), "pdb": ("poddisruptionbudgets.policy", True, "Pod Disruption Budgets"),
    "priorityclasses": ("priorityclasses.scheduling.k8s.io", False, "Priority Classes"),
    "services": ("services", True, "Services"), "endpoints": ("endpoints", True, "Endpoints"),
    "ingresses": ("ingresses.networking.k8s.io", True, "Ingresses"), "ingressclasses": ("ingressclasses.networking.k8s.io", False, "Ingress Classes"),
    "networkpolicies": ("networkpolicies.networking.k8s.io", True, "Network Policies"),
    "pvc": ("persistentvolumeclaims", True, "Persistent Volume Claims"), "pv": ("persistentvolumes", False, "Persistent Volumes"),
    "storageclasses": ("storageclasses.storage.k8s.io", False, "Storage Classes"),
    "serviceaccounts": ("serviceaccounts", True, "Service Accounts"), "roles": ("roles.rbac.authorization.k8s.io", True, "Roles"),
    "clusterroles": ("clusterroles.rbac.authorization.k8s.io", False, "Cluster Roles"),
    "rolebindings": ("rolebindings.rbac.authorization.k8s.io", True, "Role Bindings"),
    "clusterrolebindings": ("clusterrolebindings.rbac.authorization.k8s.io", False, "Cluster Role Bindings"),
    "crds": ("customresourcedefinitions.apiextensions.k8s.io", False, "Custom Resource Definitions"),
}
NS_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,252}")
CR_RE = re.compile(r"cr:([a-z0-9]([a-z0-9-]*[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")


def resolve(kind, namespaced=None):
    """kind key -> (kubectl resource, namespaced)."""
    if kind in KINDS:
        return KINDS[kind][0], KINDS[kind][1]
    if CR_RE.fullmatch(kind or ""):
        return kind[3:], bool(namespaced)
    raise KErr(f"unknown kind: {kind}")


def chk_ns(ns, allow_empty=True):
    if not ns and allow_empty:
        return ""
    if not NS_RE.fullmatch(ns or ""):
        raise KErr("bad namespace")
    return ns


def chk_name(n):
    if not NAME_RE.fullmatch(n or ""):
        raise KErr("bad name")
    return n


# ---------------- small parsers ----------------
def ts(s):
    if not s:
        return None
    try:
        return calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def cpu_m(v):
    v = str(v or "0")
    try:
        if v.endswith("m"):
            return float(v[:-1])
        if v.endswith("n"):
            return float(v[:-1]) / 1e6
        if v.endswith("u"):
            return float(v[:-1]) / 1e3
        return float(v) * 1000
    except ValueError:
        return 0.0


MEMU = {"Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30, "Ti": 2 ** 40, "Pi": 2 ** 50, "Ei": 2 ** 60,
        "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18, "m": 1e-3, "": 1}


def mem_b(v):
    m = re.fullmatch(r"([0-9.]+(?:[eE][0-9]+)?)([A-Za-z]*)", str(v or "0"))
    if not m:
        return 0.0
    return float(m[1]) * MEMU.get(m[2], 1)


def fmt_cpu(m):
    return f"{m:.0f}m" if m < 1000 else f"{m / 1000:.2f}".rstrip("0").rstrip(".")


def fmt_mem(b):
    for u, n in (("Gi", 2 ** 30), ("Mi", 2 ** 20), ("Ki", 2 ** 10)):
        if b >= n:
            return f"{b / n:.1f}{u}" if u == "Gi" else f"{b / n:.0f}{u}"
    return f"{b:.0f}"


def owner(o):
    r = (o.get("metadata", {}).get("ownerReferences") or [{}])[0]
    return f'{r.get("kind", "")}/{r.get("name", "")}' if r.get("kind") else ""


def images(spec):
    return ", ".join(c.get("image", "").split("/")[-1] for c in (spec or {}).get("containers") or [])


def sel_str(d):
    return ", ".join(f"{k}={v}" for k, v in (d or {}).items())


def C(v, c=""):
    return {"v": v, "c": c} if c else v


# ---------------- rows per kind ----------------
def pod_state(p):
    st, meta = p.get("status") or {}, p.get("metadata") or {}
    spec = p.get("spec") or {}
    cs = st.get("containerStatuses") or []
    total = len(spec.get("containers") or [])
    ready = sum(1 for c in cs if c.get("ready"))
    restarts = sum(c.get("restartCount", 0) for c in cs)
    reason = st.get("reason") or st.get("phase") or "Unknown"
    init = st.get("initContainerStatuses") or []
    for i, c in enumerate(init):
        s = c.get("state") or {}
        t = s.get("terminated")
        if t and t.get("exitCode") == 0:
            continue
        if t:
            reason = "Init:" + (t.get("reason") or f"ExitCode:{t.get('exitCode')}")
        elif s.get("waiting", {}).get("reason") not in (None, "PodInitializing"):
            reason = "Init:" + s["waiting"]["reason"]
        else:
            reason = f"Init:{i}/{len(init)}"
        break
    else:
        for c in reversed(cs):
            s = c.get("state") or {}
            if s.get("waiting", {}).get("reason"):
                reason = s["waiting"]["reason"]
            elif s.get("terminated", {}).get("reason"):
                reason = s["terminated"]["reason"]
            elif s.get("terminated"):
                reason = "ExitCode:" + str(s["terminated"].get("exitCode"))
    if meta.get("deletionTimestamp"):
        reason = "Terminating"
    if reason == "Running" and ready == total:
        cls = "ok"
    elif reason in ("Completed", "Succeeded"):
        cls = "dim"
    elif reason in ("Pending", "ContainerCreating", "PodInitializing", "Terminating", "Running") or reason.startswith("Init:") and reason[5:6].isdigit():
        cls = "warn"
    else:
        cls = "bad"
    return reason, cls, ready, total, restarts


def row_pods(p, top):
    meta, spec, st = p["metadata"], p.get("spec") or {}, p.get("status") or {}
    reason, cls, ready, total, restarts = pod_state(p)
    u = top.get((meta.get("namespace"), meta["name"])) if top else None
    return [C(f"{ready}/{total}", "ok" if ready == total and total else "warn"), C(reason, cls),
            C(str(restarts), "warn" if restarts else ""), fmt_cpu(u[0]) if u else "", fmt_mem(u[1]) if u else "",
            spec.get("nodeName", ""), st.get("podIP", ""), owner(p)], cls, {
        "containers": [c["name"] for c in spec.get("containers") or []],
        "init": [c["name"] for c in spec.get("initContainers") or []],
        "owner": owner(p), "ports": [pt.get("containerPort") for c in spec.get("containers") or [] for pt in c.get("ports") or []]}


def _replicas(o, ready_key="readyReplicas"):
    want = (o.get("spec") or {}).get("replicas", 1)
    st = o.get("status") or {}
    rd = st.get(ready_key, 0) or 0
    return want, rd, st


def row_deployments(o, top):
    want, rd, st = _replicas(o)
    cls = "ok" if rd >= want else ("bad" if want and not st.get("availableReplicas") else "warn")
    return [C(f"{rd}/{want}", cls), str(st.get("updatedReplicas", 0) or 0), str(st.get("availableReplicas", 0) or 0),
            images(o["spec"]["template"]["spec"])], cls, {"replicas": want}


def row_statefulsets(o, top):
    want, rd, st = _replicas(o)
    cls = "ok" if rd >= want else "warn"
    return [C(f"{rd}/{want}", cls), images(o["spec"]["template"]["spec"])], cls, {"replicas": want}


def row_daemonsets(o, top):
    st = o.get("status") or {}
    want, rd = st.get("desiredNumberScheduled", 0), st.get("numberReady", 0)
    cls = "ok" if rd >= want else "warn"
    return [str(want), str(st.get("currentNumberScheduled", 0)), C(str(rd), cls), str(st.get("updatedNumberScheduled", 0)),
            str(st.get("numberAvailable", 0)), sel_str(o["spec"]["template"]["spec"].get("nodeSelector"))], cls, {}


def row_replicasets(o, top):
    want, rd, st = _replicas(o)
    cls = "ok" if rd >= want else "warn"
    return [str(want), str(st.get("replicas", 0) or 0), C(str(rd), cls), owner(o)], cls if want else "dim", {"replicas": want}


def row_jobs(o, top):
    spec, st = o.get("spec") or {}, o.get("status") or {}
    comp = spec.get("completions", 1)
    conds = {c["type"]: c.get("status") for c in st.get("conditions") or []}
    status, cls = ("Complete", "ok") if conds.get("Complete") == "True" else ("Failed", "bad") if conds.get("Failed") == "True" \
        else ("Suspended", "dim") if spec.get("suspend") else ("Running", "warn")
    a, b = ts(st.get("startTime")), ts(st.get("completionTime"))
    return [f"{st.get('succeeded', 0) or 0}/{comp}", {"dur": (b or time.time()) - a} if a else "", C(status, cls), owner(o)], cls, {}


def row_cronjobs(o, top):
    spec, st = o.get("spec") or {}, o.get("status") or {}
    sus = bool(spec.get("suspend"))
    return [spec.get("schedule", ""), C("Yes", "warn") if sus else "No", str(len(st.get("active") or [])),
            {"t": ts(st.get("lastScheduleTime"))} if st.get("lastScheduleTime") else ""], "dim" if sus else "", {"suspend": sus}


def _ports(sp):
    out = []
    for p in sp.get("ports") or []:
        s = str(p.get("port"))
        if p.get("nodePort"):
            s += f":{p['nodePort']}"
        out.append(s + "/" + p.get("protocol", "TCP"))
    return ", ".join(out)


def row_services(o, top):
    sp, st = o.get("spec") or {}, o.get("status") or {}
    ext = [i.get("ip") or i.get("hostname") for i in (st.get("loadBalancer") or {}).get("ingress") or []] + (sp.get("externalIPs") or [])
    if sp.get("type") == "ExternalName":
        ext = [sp.get("externalName", "")]
    return [sp.get("type", ""), sp.get("clusterIP", ""), ", ".join(x for x in ext if x) or ("<pending>" if sp.get("type") == "LoadBalancer" else ""),
            _ports(sp), sel_str(sp.get("selector"))], "", {"ports": [p.get("port") for p in sp.get("ports") or []]}


def row_endpoints(o, top):
    eps = []
    for s in o.get("subsets") or []:
        for a in s.get("addresses") or []:
            for p in s.get("ports") or [{}]:
                eps.append(f"{a.get('ip')}:{p.get('port', '')}".rstrip(":"))
    return [", ".join(eps[:4]) + (f" +{len(eps) - 4} more" if len(eps) > 4 else "") or C("<none>", "warn")], "", {}


def row_ingresses(o, top):
    sp, st = o.get("spec") or {}, o.get("status") or {}
    hosts = [r.get("host", "*") for r in sp.get("rules") or []]
    addr = [i.get("ip") or i.get("hostname") for i in (st.get("loadBalancer") or {}).get("ingress") or []]
    return [sp.get("ingressClassName") or (o["metadata"].get("annotations") or {}).get("kubernetes.io/ingress.class", ""),
            ", ".join(hosts), ", ".join(a for a in addr if a), "80, 443" if sp.get("tls") else "80"], "", {"hosts": hosts, "tls": bool(sp.get("tls"))}


def row_networkpolicies(o, top):
    sp = o.get("spec") or {}
    return [sel_str((sp.get("podSelector") or {}).get("matchLabels")) or "(all pods)", ", ".join(sp.get("policyTypes") or [])], "", {}


def row_configmaps(o, top):
    keys = list((o.get("data") or {}).keys()) + list((o.get("binaryData") or {}).keys())
    return [", ".join(keys[:6]) + (f" +{len(keys) - 6}" if len(keys) > 6 else "")], "", {}


def row_secrets(o, top):
    return [o.get("type", ""), str(len(o.get("data") or {}))], "", {}


def row_hpa(o, top):
    sp, st = o.get("spec") or {}, o.get("status") or {}
    ref = sp.get("scaleTargetRef") or {}
    tg = []
    for m in sp.get("metrics") or []:
        r = (m.get("resource") or {})
        t = (r.get("target") or {}).get("averageUtilization")
        cur = next(((c.get("resource") or {}).get("current", {}).get("averageUtilization") for c in st.get("currentMetrics") or []
                    if (c.get("resource") or {}).get("name") == r.get("name")), None)
        if t is not None:
            tg.append(f"{r.get('name')}: {'?' if cur is None else cur}%/{t}%")
    if sp.get("targetCPUUtilizationPercentage"):
        tg.append(f"cpu: {st.get('currentCPUUtilizationPercentage', '?')}%/{sp['targetCPUUtilizationPercentage']}%")
    return [f"{ref.get('kind', '')}/{ref.get('name', '')}", ", ".join(tg), str(sp.get("minReplicas", 1)), str(sp.get("maxReplicas", "")),
            str(st.get("currentReplicas", 0))], "", {}


def row_pdb(o, top):
    sp, st = o.get("spec") or {}, o.get("status") or {}
    al = st.get("disruptionsAllowed", 0)
    return [str(sp.get("minAvailable", "")), str(sp.get("maxUnavailable", "")), C(str(al), "" if al else "warn")], "", {}


def row_priorityclasses(o, top):
    return [str(o.get("value", "")), "Yes" if o.get("globalDefault") else ""], "", {}


def row_pvc(o, top):
    sp, st = o.get("spec") or {}, o.get("status") or {}
    ph = st.get("phase", "")
    cls = {"Bound": "ok", "Pending": "warn", "Lost": "bad"}.get(ph, "")
    return [C(ph, cls), sp.get("volumeName", ""), (st.get("capacity") or {}).get("storage", ""), ", ".join(sp.get("accessModes") or []),
            sp.get("storageClassName") or ""], cls, {}


def row_pv(o, top):
    sp, st = o.get("spec") or {}, o.get("status") or {}
    ph = st.get("phase", "")
    cr = sp.get("claimRef") or {}
    cls = {"Bound": "ok", "Available": "", "Released": "warn", "Failed": "bad"}.get(ph, "")
    return [(sp.get("capacity") or {}).get("storage", ""), ", ".join(sp.get("accessModes") or []), sp.get("persistentVolumeReclaimPolicy", ""),
            C(ph, cls), f"{cr.get('namespace')}/{cr.get('name')}" if cr else "", sp.get("storageClassName") or ""], cls, {}


def row_storageclasses(o, top):
    ann = o["metadata"].get("annotations") or {}
    dflt = ann.get("storageclass.kubernetes.io/is-default-class") == "true"
    return [o.get("provisioner", ""), o.get("reclaimPolicy", ""), o.get("volumeBindingMode", ""), C("default", "ok") if dflt else ""], "", {}


def row_ingressclasses(o, top):
    return [(o.get("spec") or {}).get("controller", "")], "", {}


def row_nodes(o, top):
    meta, sp, st = o["metadata"], o.get("spec") or {}, o.get("status") or {}
    conds = {c["type"]: c.get("status") for c in st.get("conditions") or []}
    ready = conds.get("Ready") == "True"
    status = "Ready" if ready else "NotReady"
    if sp.get("unschedulable"):
        status += ", SchedulingDisabled"
    cls = ("warn" if sp.get("unschedulable") else "ok") if ready else "bad"
    roles = [k.split("/", 1)[1] for k in (meta.get("labels") or {}) if k.startswith("node-role.kubernetes.io/")] or ["<none>"]
    ip = next((a["address"] for a in st.get("addresses") or [] if a.get("type") == "InternalIP"), "")
    alloc = st.get("allocatable") or {}
    u = top.get(meta["name"]) if top else None
    cpu_cap, mem_cap = cpu_m(alloc.get("cpu")), mem_b(alloc.get("memory"))
    cpu = f"{fmt_cpu(u[0])} / {fmt_cpu(cpu_cap)}" if u else fmt_cpu(cpu_cap)
    mem = f"{fmt_mem(u[1])} / {fmt_mem(mem_cap)}" if u else fmt_mem(mem_cap)
    ni = st.get("nodeInfo") or {}
    return [C(status, cls), ", ".join(roles), ni.get("kubeletVersion", ""), ip,
            {"bar": round(u[0] / cpu_cap * 100) if u and cpu_cap else None, "v": cpu},
            {"bar": round(u[1] / mem_cap * 100) if u and mem_cap else None, "v": mem},
            str(len(sp.get("taints") or [])), f"{ni.get('osImage', '')}"], cls, {"unschedulable": bool(sp.get("unschedulable"))}


def row_namespaces(o, top):
    ph = (o.get("status") or {}).get("phase", "")
    return [C(ph, "ok" if ph == "Active" else "warn")], "", {}


def ev_time(o):
    return ts(o.get("lastTimestamp")) or ts(o.get("eventTime")) or ts((o.get("series") or {}).get("lastObservedTime")) \
        or ts(o["metadata"].get("creationTimestamp"))


def row_events(o, top):
    io = o.get("involvedObject") or {}
    warn = o.get("type") == "Warning"
    return [C(o.get("type", ""), "warn" if warn else ""), o.get("reason", ""), f"{io.get('kind', '')}/{io.get('name', '')}",
            o.get("message", "").strip(), str(o.get("count") or (o.get("series") or {}).get("count") or 1),
            {"t": ev_time(o)}], "warn" if warn else "", {"obj": {"kind": io.get("kind"), "name": io.get("name"), "ns": io.get("namespace")}}


def row_serviceaccounts(o, top):
    return [str(len(o.get("secrets") or []))], "", {}


def row_roles(o, top):
    return [str(len(o.get("rules") or []))], "", {}


def row_bindings(o, top):
    r = o.get("roleRef") or {}
    subj = [f"{s.get('kind')}:{s.get('name')}" for s in o.get("subjects") or []]
    return [f"{r.get('kind', '')}/{r.get('name', '')}", ", ".join(subj[:3]) + (f" +{len(subj) - 3}" if len(subj) > 3 else "")], "", {}


def row_crds(o, top):
    sp = o.get("spec") or {}
    vers = [v["name"] for v in sp.get("versions") or [] if v.get("served")]
    names = sp.get("names") or {}
    return [sp.get("group", ""), ", ".join(vers), sp.get("scope", ""), names.get("kind", "")], "", {
        "resource": f"{names.get('plural')}.{sp.get('group')}", "namespaced": sp.get("scope") == "Namespaced", "kind": names.get("kind")}


def row_generic(o, top):
    return [], "", {}


COLS = {
    "pods": ["Ready", "Status", "Restarts", "CPU", "Memory", "Node", "IP", "Controlled by"],
    "deployments": ["Ready", "Up-to-date", "Available", "Images"], "statefulsets": ["Ready", "Images"],
    "daemonsets": ["Desired", "Current", "Ready", "Up-to-date", "Available", "Node selector"],
    "replicasets": ["Desired", "Current", "Ready", "Controlled by"], "jobs": ["Completions", "Duration", "Status", "Controlled by"],
    "cronjobs": ["Schedule", "Suspended", "Active", "Last schedule"],
    "services": ["Type", "Cluster IP", "External IP", "Ports", "Selector"], "endpoints": ["Endpoints"],
    "ingresses": ["Class", "Hosts", "Address", "Ports"], "ingressclasses": ["Controller"],
    "networkpolicies": ["Pod selector", "Policy types"], "configmaps": ["Keys"], "secrets": ["Type", "Keys"],
    "hpa": ["Reference", "Targets", "Min", "Max", "Replicas"], "pdb": ["Min available", "Max unavailable", "Allowed disruptions"],
    "priorityclasses": ["Value", "Global default"],
    "pvc": ["Status", "Volume", "Capacity", "Access modes", "Storage class"],
    "pv": ["Capacity", "Access modes", "Reclaim", "Status", "Claim", "Storage class"],
    "storageclasses": ["Provisioner", "Reclaim", "Binding mode", "Default"],
    "nodes": ["Status", "Roles", "Version", "Internal IP", "CPU", "Memory", "Taints", "OS"],
    "namespaces": ["Status"], "events": ["Type", "Reason", "Object", "Message", "Count", "Last seen"],
    "serviceaccounts": ["Secrets"], "roles": ["Rules"], "clusterroles": ["Rules"],
    "rolebindings": ["Role", "Subjects"], "clusterrolebindings": ["Role", "Subjects"], "crds": ["Group", "Versions", "Scope", "Kind"],
}
ROWS = {"pods": row_pods, "deployments": row_deployments, "statefulsets": row_statefulsets, "daemonsets": row_daemonsets,
        "replicasets": row_replicasets, "jobs": row_jobs, "cronjobs": row_cronjobs, "services": row_services,
        "endpoints": row_endpoints, "ingresses": row_ingresses, "ingressclasses": row_ingressclasses,
        "networkpolicies": row_networkpolicies, "configmaps": row_configmaps, "secrets": row_secrets, "hpa": row_hpa,
        "pdb": row_pdb, "priorityclasses": row_priorityclasses, "pvc": row_pvc, "pv": row_pv,
        "storageclasses": row_storageclasses, "nodes": row_nodes, "namespaces": row_namespaces, "events": row_events,
        "serviceaccounts": row_serviceaccounts, "roles": row_roles, "clusterroles": row_roles, "rolebindings": row_bindings,
        "clusterrolebindings": row_bindings, "crds": row_crds}

# ---------------- metrics (metrics-server, optional) ----------------
_NOMET = {}       # cid -> time metrics were found missing


def top(cid, what, ns=""):
    if time.time() - _NOMET.get(cid, 0) < 120:
        return None
    args = ["top", what, "--no-headers"] + ((["-n", ns] if ns else ["-A"]) if what == "pods" else [])
    try:
        rc, out, err = kexec(cid, args, timeout=20)
    except (KErr, U.RemoteError):
        rc, out = 1, b""
    if rc != 0:
        _NOMET[cid] = time.time()
        return None
    res = {}
    for l in out.decode("utf-8", "replace").splitlines():
        p = l.split()
        if what == "pods":
            if ns and len(p) >= 3:
                res[(ns, p[0])] = (cpu_m(p[1]), mem_b(p[2]))
            elif len(p) >= 4:
                res[(p[0], p[1])] = (cpu_m(p[2]), mem_b(p[3]))
        elif len(p) >= 4:
            res[p[0]] = (cpu_m(p[1]), mem_b(p[3]))
    return res


# ---------------- API ----------------
def r_clusters(d):
    cfg = load_cfg()
    prot = U.protected()
    cx = contexts()
    out = []
    for c in cx["list"]:
        cid = "ctx:" + c["name"]
        out.append({"id": cid, "name": c["name"], "type": "ctx", "sub": c["cluster"] + (f" · {c['user']}" if c["user"] else ""),
                    "namespace": c["namespace"], "current": c["name"] == cx["current"], "hidden": cid in cfg["hidden"],
                    "protected": c["name"] in prot})
    srv = U.servers()
    for s in cfg["ssh"]:
        if s in srv:
            if s not in U.HOSTS:
                U.HOSTS[s] = U.host_of(s)
            out.append({"id": "ssh:" + s, "name": s, "type": "ssh", "sub": "via SSH · " + U.HOSTS.get(s, s), "namespace": "",
                        "current": False, "hidden": False, "protected": s in prot})
    known = [{"name": s, "host": U.HOSTS.get(s) or s} for s in srv if s not in cfg["ssh"]]
    return {"clusters": out, "kubectl": bool(kubectl_bin()), "helm": bool(helm_bin()), "known": known}


def r_cfg(d):
    op = d.get("op")
    cfg = load_cfg()
    if op == "add_ssh":
        s = d.get("server", "")
        if s not in U.servers():
            raise KErr("unknown server")
        KMODE.pop(s, None)
        kmode(s)                                  # checks kubectl works there (may ask for sudo)
        if s not in cfg["ssh"]:
            cfg["ssh"].append(s)
    elif op == "remove_ssh":
        cfg["ssh"] = [x for x in cfg["ssh"] if x != d.get("server")]
        KMODE.pop(d.get("server"), None)
    elif op in ("hide", "unhide"):
        cid = d.get("cid", "")
        cfg["hidden"] = [x for x in cfg["hidden"] if x != cid] + ([cid] if op == "hide" else [])
    elif op == "protect":
        key = gkey(d.get("cid"))
        p = U.protected()
        on = bool(d.get("on"))
        if not on and key in p:
            U.guard(d, key, "turn off protection")
        (p.add if on else p.discard)(key)
        U.save_protected(p)
    else:
        raise KErr("bad op")
    save_cfg(cfg)
    return {}


def r_info(d):
    cid = d.get("cid")
    kind, name = parse_cid(cid)
    with ThreadPoolExecutor(2) as ex:
        fv = ex.submit(kcall, cid, ["version", "-o", "json"], None, 25)
        fn = ex.submit(kjson, cid, ["get", "nodes"], 25)
        try:
            v = json.loads(fv.result()).get("serverVersion", {}).get("gitVersion", "")
        except ValueError:
            v = ""
        nodes = fn.result().get("items", [])
    ready = sum(1 for n in nodes if any(c["type"] == "Ready" and c["status"] == "True" for c in n.get("status", {}).get("conditions") or []))
    return {"version": v, "nodes": len(nodes), "ready": ready, "mode": KMODE.get(name, "") if kind == "ssh" else ""}


def r_ns(d):
    j = kjson(d.get("cid"), ["get", "namespaces"], timeout=25)
    return {"namespaces": sorted(i["metadata"]["name"] for i in j.get("items", []))}


def r_list(d):
    cid, kind = d.get("cid"), d.get("kind", "")
    res, nsd = resolve(kind, d.get("namespaced"))
    ns = chk_ns(d.get("ns", "")) if nsd else ""
    args = ["get", res] + ((["-n", ns] if ns else ["-A"]) if nsd else [])
    with ThreadPoolExecutor(2) as ex:
        fl = ex.submit(kjson, cid, args, 60)
        ft = ex.submit(top, cid, "pods", ns) if kind == "pods" else ex.submit(top, cid, "nodes") if kind == "nodes" else None
        items = fl.result().get("items", [])
        tp = ft.result() if ft else None
    fn = ROWS.get(kind, row_generic)
    rows = []
    for o in items:
        m = o.get("metadata") or {}
        try:
            cells, cls, x = fn(o, tp)
        except Exception:                          # an odd object must not break the whole list
            cells, cls, x = [""] * len(COLS.get(kind, [])), "", {}
        rows.append({"name": m.get("name", ""), "ns": m.get("namespace", ""), "created": ts(m.get("creationTimestamp")),
                     "uid": m.get("uid", ""), "cells": cells, "st": cls, "x": x})
    if kind == "events":
        rows.sort(key=lambda r: -(r["cells"][-1].get("t") or 0))
    return {"cols": COLS.get(kind, []), "rows": rows, "namespaced": nsd, "metrics": tp is not None if kind in ("pods", "nodes") else None}


def r_overview(d):
    cid = d.get("cid")
    ns = chk_ns(d.get("ns", ""))
    sc = ["-n", ns] if ns else ["-A"]
    with ThreadPoolExecutor(5) as ex:
        fn = ex.submit(kjson, cid, ["get", "nodes"], 40)
        fp = ex.submit(kjson, cid, ["get", "pods"] + sc, 60)
        fe = ex.submit(kjson, cid, ["get", "events", "--field-selector", "type=Warning"] + sc, 40)
        fw = ex.submit(kjson, cid, ["get", "deployments.apps,statefulsets.apps,daemonsets.apps,jobs.batch,cronjobs.batch"] + sc, 40)
        ft = ex.submit(top, cid, "nodes")
        nodes, pods = fn.result().get("items", []), fp.result().get("items", [])
        try:
            evs = fe.result().get("items", [])
        except KErr:
            evs = []
        try:
            wl = fw.result().get("items", [])
        except KErr:
            wl = []
        tp = ft.result()
    alloc_cpu = sum(cpu_m((n.get("status") or {}).get("allocatable", {}).get("cpu")) for n in nodes)
    alloc_mem = sum(mem_b((n.get("status") or {}).get("allocatable", {}).get("memory")) for n in nodes)
    cap_pods = sum(int((n.get("status") or {}).get("allocatable", {}).get("pods", 0) or 0) for n in nodes)
    req_cpu = req_mem = lim_cpu = lim_mem = 0.0
    phase = {"ok": 0, "warn": 0, "bad": 0, "dim": 0}
    for p in pods:
        reason, cls, *_ = pod_state(p)
        phase[cls] = phase.get(cls, 0) + 1
        if (p.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
            continue
        for c in (p.get("spec") or {}).get("containers") or []:
            r = c.get("resources") or {}
            req_cpu += cpu_m((r.get("requests") or {}).get("cpu"))
            req_mem += mem_b((r.get("requests") or {}).get("memory"))
            lim_cpu += cpu_m((r.get("limits") or {}).get("cpu"))
            lim_mem += mem_b((r.get("limits") or {}).get("memory"))
    ready = sum(1 for n in nodes if any(c["type"] == "Ready" and c["status"] == "True" for c in (n.get("status") or {}).get("conditions") or []))
    use_cpu = sum(v[0] for v in tp.values()) if tp else None
    use_mem = sum(v[1] for v in tp.values()) if tp else None
    wk = {}
    for o in wl:
        k = o.get("kind", "")
        fn_ = {"Deployment": row_deployments, "StatefulSet": row_statefulsets, "DaemonSet": row_daemonsets, "Job": row_jobs, "CronJob": row_cronjobs}.get(k)
        cls = fn_(o, None)[1] if fn_ else ""
        e = wk.setdefault(k, {"total": 0, "ok": 0, "warn": 0, "bad": 0})
        e["total"] += 1
        if cls in ("ok", "warn", "bad"):
            e[cls] += 1
    evs.sort(key=lambda o: -(ev_time(o) or 0))
    return {"nodes": len(nodes), "ready": ready, "pods": len(pods), "podcap": cap_pods, "phase": phase, "workloads": wk,
            "cpu": {"alloc": alloc_cpu, "req": req_cpu, "lim": lim_cpu, "use": use_cpu},
            "mem": {"alloc": alloc_mem, "req": req_mem, "lim": lim_mem, "use": use_mem},
            "metrics": tp is not None,
            "warnings": [{"ns": o["metadata"].get("namespace", ""), "reason": o.get("reason", ""), "msg": (o.get("message") or "").strip(),
                          "obj": f"{(o.get('involvedObject') or {}).get('kind', '')}/{(o.get('involvedObject') or {}).get('name', '')}",
                          "count": o.get("count") or 1, "t": ev_time(o)} for o in evs[:40]]}


def _obj(d):
    res, nsd = resolve(d.get("kind", ""), d.get("namespaced"))
    ns = chk_ns(d.get("ns", ""), allow_empty=not nsd) if nsd else ""
    if nsd and not ns:
        raise KErr("namespace missing")
    return res, nsd, ns, chk_name(d.get("name", ""))


def r_yaml(d):
    res, nsd, ns, name = _obj(d)
    return {"yaml": kcall(d.get("cid"), ["get", res, name] + (["-n", ns] if ns else []) + ["-o", "yaml"])}


def r_describe(d):
    res, nsd, ns, name = _obj(d)
    return {"text": kcall(d.get("cid"), ["describe", res, name] + (["-n", ns] if ns else []), timeout=60)}


def r_events(d):
    res, nsd, ns, name = _obj(d)
    kind = d.get("objkind") or ""
    fs = f"involvedObject.name={name}" + (f",involvedObject.kind={kind}" if re.fullmatch(r"[A-Za-z]+", kind) else "")
    j = kjson(d.get("cid"), ["get", "events", "--field-selector", fs] + (["-n", ns] if ns else ["-A"]))
    evs = sorted(j.get("items", []), key=lambda o: -(ev_time(o) or 0))
    return {"events": [{"type": o.get("type", ""), "reason": o.get("reason", ""), "msg": (o.get("message") or "").strip(),
                        "count": o.get("count") or 1, "t": ev_time(o), "src": (o.get("source") or {}).get("component", "")
                        or o.get("reportingComponent", "")} for o in evs]}


def r_apply(d):
    """Save edited YAML (replace: exact edit, fails if someone changed it meanwhile) or create from YAML (apply)."""
    cid = d.get("cid")
    U.guard(d, gkey(cid), "change resources in this cluster")
    y = d.get("yaml") or ""
    if not y.strip():
        raise KErr("empty YAML")
    mode = "replace" if d.get("mode") == "replace" else "apply"
    ns = chk_ns(d.get("ns", ""))
    args = [mode, "-f", "-"] + (["-n", ns] if ns and mode == "apply" else [])
    return {"output": kcall(cid, args, stdin=y.encode(), timeout=60).strip()}


def r_action(d):
    cid, op = d.get("cid"), d.get("op", "")
    kind = d.get("kind", "")
    res, nsd = resolve(kind, d.get("namespaced"))
    items = d.get("items") or [{"ns": d.get("ns", ""), "name": d.get("name", "")}]
    for it in items:
        chk_name(it.get("name", ""))
        if nsd:
            chk_ns(it.get("ns", ""), allow_empty=False)
    what = {"delete": f"delete {len(items)} {KINDS.get(kind, (kind,))[0] if len(items) > 1 else items[0]['name']}",
            "scale": f"scale {items[0]['name']}", "restart": f"restart {items[0]['name']}", "trigger": f"run job from {items[0]['name']}",
            "suspend": f"suspend {items[0]['name']}", "resume": f"resume {items[0]['name']}", "cordon": f"cordon node {items[0]['name']}",
            "uncordon": f"uncordon node {items[0]['name']}", "drain": f"drain node {items[0]['name']}"}.get(op)
    if not what:
        raise KErr("bad action")
    U.guard(d, gkey(cid), what)
    it = items[0]
    nsa = ["-n", it["ns"]] if nsd else []
    if op == "delete":
        by = {}
        for i in items:
            by.setdefault(i.get("ns", ""), []).append(i["name"])
        out = []
        for ns, names in by.items():
            out.append(kcall(cid, ["delete", res] + names + (["-n", ns] if nsd else []) + ["--wait=false"], timeout=60))
        return {"output": "".join(out).strip()}
    if op == "scale":
        n = int(d.get("replicas", -1))
        if not 0 <= n <= 1000:
            raise KErr("replicas: 0-1000")
        return {"output": kcall(cid, ["scale", res, it["name"], f"--replicas={n}"] + nsa).strip()}
    if op == "restart":
        return {"output": kcall(cid, ["rollout", "restart", res, it["name"]] + nsa).strip()}
    if op == "trigger":
        jn = (it["name"][:40] + "-manual-" + time.strftime("%H%M%S")).lower()
        return {"output": kcall(cid, ["create", "job", jn, f"--from=cronjob/{it['name']}"] + nsa).strip()}
    if op in ("suspend", "resume"):
        return {"output": kcall(cid, ["patch", res, it["name"], "--type=merge", "-p",
                                      json.dumps({"spec": {"suspend": op == "suspend"}})] + nsa).strip()}
    if op in ("cordon", "uncordon"):
        return {"output": kcall(cid, [op, it["name"]]).strip()}
    if op == "drain":
        return {"output": kcall(cid, ["drain", it["name"], "--ignore-daemonsets", "--delete-emptydir-data", "--timeout=120s"],
                                timeout=150).strip()}


def r_logs(d):
    cid = d.get("cid")
    ns, pod = chk_ns(d.get("ns", ""), allow_empty=False), chk_name(d.get("name", ""))
    tail = max(10, min(int(d.get("tail", 500)), 20000))
    args = ["logs", pod, "-n", ns, f"--tail={tail}"]
    c = d.get("container", "")
    if c == "*":
        args += ["--all-containers", "--prefix"]
    elif c:
        args += ["-c", chk_name(c)]
    if d.get("previous"):
        args.append("--previous")
    if d.get("timestamps"):
        args.append("--timestamps")
    rc, out, err = kexec(cid, args, timeout=60)
    if rc != 0:
        raise KErr(_clean_err(err or out.decode("utf-8", "replace")))
    return {"text": U.text(out)}


def r_secret(d):
    """Secret values (base64) - only fetched when the user presses Reveal."""
    ns, name = chk_ns(d.get("ns", ""), allow_empty=False), chk_name(d.get("name", ""))
    j = kjson(d.get("cid"), ["get", "secret", name, "-n", ns])
    return {"data": j.get("data") or {}, "type": j.get("type", "")}


def r_helm(d):
    cid, op = d.get("cid"), d.get("op", "list")
    if op == "list":
        out = kcall(cid, ["list", "-A", "-a", "-o", "json"], helm=True, timeout=60)
        try:
            rel = json.loads(out or "[]")
        except ValueError:
            rel = []
        return {"releases": rel}
    ns, name = chk_ns(d.get("ns", ""), allow_empty=False), chk_name(d.get("name", ""))
    if op == "history":
        return {"history": json.loads(kcall(cid, ["history", name, "-n", ns, "-o", "json"], helm=True) or "[]")}
    if op == "values":
        return {"text": kcall(cid, ["get", "values", name, "-n", ns] + (["--all"] if d.get("all") else []), helm=True)}
    if op == "manifest":
        return {"text": kcall(cid, ["get", "manifest", name, "-n", ns], helm=True)}
    if op == "notes":
        return {"text": kcall(cid, ["get", "notes", name, "-n", ns], helm=True)}
    if op == "rollback":
        rev = int(d.get("revision", 0))
        U.guard(d, gkey(cid), f"roll back {name} to revision {rev}")
        return {"output": kcall(cid, ["rollback", name, str(rev), "-n", ns, "--wait=false"], helm=True, timeout=120)}
    if op == "uninstall":
        U.guard(d, gkey(cid), f"uninstall {name}")
        return {"output": kcall(cid, ["uninstall", name, "-n", ns], helm=True, timeout=120)}
    raise KErr("bad op")


# ---------------- port forwarding ----------------
PF = {}
PF_LOCK = threading.Lock()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _port_free(p):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _ssh_nomux():
    out, skip = [], False
    for i, x in enumerate(U.SSH):
        if skip:
            skip = False
            continue
        if x == "-o" and i + 1 < len(U.SSH) and U.SSH[i + 1].split("=")[0] in ("ControlMaster", "ControlPath", "ControlPersist"):
            skip = True
            continue
        out.append(x)
    return out + ["-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ExitOnForwardFailure=yes"]


def _kill(p):
    try:
        os.killpg(p.pid, signal.SIGTERM) if not U.IS_WIN else p.terminate()
    except OSError:
        pass


def pf_start(d):
    cid = d.get("cid")
    kind, cname = parse_cid(cid)
    rk = d.get("kind", "")
    if rk not in ("pods", "services", "deployments"):
        raise KErr("port forwarding works for pods, services and deployments")
    ns, name = chk_ns(d.get("ns", ""), allow_empty=False), chk_name(d.get("name", ""))
    rport = int(d.get("port", 0))
    if not 0 < rport < 65536:
        raise KErr("port: 1-65535")
    lport = int(d.get("local") or 0) or _free_port()
    if not 1024 <= lport < 65536 or not _port_free(lport):
        raise KErr(f"local port {lport} is in use - pick another")
    target = {"pods": "pod", "services": "svc", "deployments": "deploy"}[rk] + "/" + name
    sin = None
    if kind == "ctx":
        kb = kubectl_bin()
        if not kb:
            raise KErr("kubectl is not installed on this computer")
        argv = [kb, "--context", cname, "port-forward", "-n", ns, target, f"{lport}:{rport}", "--address", "127.0.0.1"]
    else:
        m = kmode(cname)
        rp2 = random.randint(30000, 60000)
        pre, sin = U._sudo_pre(cname, lazy=not m.startswith("sudo"))
        script = RPATH + pre + "exec " + PFX[m] + " " + " ".join(q(a) for a in
                 ["port-forward", "--address", "127.0.0.1", "-n", ns, target, f"{rp2}:{rport}"])
        argv = _ssh_nomux() + ["-L", f"127.0.0.1:{lport}:127.0.0.1:{rp2}"] + U.target(cname) + ["sh -c " + q(script)]
    kw = {"start_new_session": True} if not U.IS_WIN else {}
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    if sin:
        try:
            p.stdin.write(sin)
            p.stdin.flush()
        except OSError:
            pass
    ok, t0 = False, time.time()
    buf = b""
    import select as _sel
    while time.time() - t0 < 15:
        if p.poll() is not None:
            break
        r, _, _ = _sel.select([p.stdout], [], [], 0.3) if not U.IS_WIN else ([p.stdout], [], [])
        if r:
            line = p.stdout.readline()
            buf += line
            if b"Forwarding from" in line:
                ok = True
                break
    if not ok:
        _kill(p)
        err = (p.stderr.read() if p.poll() is not None else b"").decode("utf-8", "replace")
        raise KErr("port-forward failed: " + _clean_err(err or buf.decode("utf-8", "replace") or "no answer in 15s"))
    threading.Thread(target=lambda: [None for _ in iter(p.stdout.readline, b"")], daemon=True).start()   # keep pipe drained
    threading.Thread(target=lambda: [None for _ in iter(p.stderr.readline, b"")], daemon=True).start()
    pid = f"pf{int(time.time() * 1000) % 10 ** 9}{random.randint(10, 99)}"
    with PF_LOCK:
        PF[pid] = {"id": pid, "cid": cid, "kind": rk, "ns": ns, "name": name, "port": rport, "local": lport, "proc": p, "t": int(time.time())}
    return {"id": pid, "local": lport}


def pf_list():
    with PF_LOCK:
        for k in [k for k, v in PF.items() if v["proc"].poll() is not None]:
            PF[k]["dead"] = True
        return [{k: v for k, v in e.items() if k != "proc"} for e in PF.values()]


def r_pf(d):
    op = d.get("op", "list")
    if op == "start":
        r = pf_start(d)
        return {**r, "forwards": pf_list()}
    if op == "stop":
        with PF_LOCK:
            e = PF.pop(d.get("id", ""), None)
        if e:
            _kill(e["proc"])
    return {"forwards": pf_list()}


@atexit.register
def _stop_all():
    for e in list(PF.values()):
        _kill(e["proc"])


# ---------------- shell into a pod (used by the web terminal) ----------------
SHELL = "command -v bash >/dev/null && exec bash || exec sh"


def exec_argv(cid, ns, pod, container):
    kind, name = parse_cid(cid)
    ns, pod = chk_ns(ns, allow_empty=False), chk_name(pod)
    cargs = ["-c", chk_name(container)] if container else []
    if kind == "ctx":
        kb = kubectl_bin()
        if not kb:
            raise KErr("kubectl is not installed on this computer")
        return [kb, "--context", name, "exec", "-it", "-n", ns, pod] + cargs + ["--", "sh", "-c", SHELL]
    m = kmode(name)
    pfx = PFX[m].replace("$S", "sudo")                 # sudo asks its password in the terminal if needed
    remote = RPATH + pfx + " " + " ".join(q(a) for a in ["exec", "-it", "-n", ns, pod] + cargs + ["--", "sh", "-c", SHELL])
    cmd = U.SSH[:]
    i = cmd.index("BatchMode=yes")
    del cmd[i - 1:i + 1]
    return cmd + ["-tt"] + U.target(name) + [remote]


ROUTES = {"/api/k8s_clusters": r_clusters, "/api/k8s_cfg": r_cfg, "/api/k8s_info": r_info, "/api/k8s_ns": r_ns,
          "/api/k8s_list": r_list, "/api/k8s_overview": r_overview, "/api/k8s_yaml": r_yaml, "/api/k8s_describe": r_describe,
          "/api/k8s_events": r_events, "/api/k8s_apply": r_apply, "/api/k8s_action": r_action, "/api/k8s_logs": r_logs,
          "/api/k8s_secret": r_secret, "/api/k8s_helm": r_helm, "/api/k8s_pf": r_pf}
