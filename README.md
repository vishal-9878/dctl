# dctl

**Agentless dashboard and CLI for Docker and PM2 across many servers, using only SSH.**

dctl runs on *your* machine. The servers need nothing new: no agent, no daemon, no open ports, no package to install. If you can `ssh` into a server and it has Docker or PM2, dctl can manage it.

It is a good fit when:

- you look after many servers but are not allowed to install tools on them;
- you log in as a normal user and use `sudo` only when needed;
- PM2 processes run as different Linux users (root, app users), and you can only log in as yourself;
- some servers have restrictive sudoers rules, for example shells are not allowed through sudo.

It is two files: a Bash CLI (`dctl`) and a web UI (`dctl-ui`) written in Python using only the standard library.

---

## Features

### Docker
- **Containers across all servers** in one list: status, ports, uptime, live CPU and memory.
- **Start / stop / restart / remove**, for one container or many at once (checkbox selection).
- **Docker Compose:** finds the compose file of any container, lets you edit it in the browser with validation, a diff review and an automatic backup, then runs `up -d` / `pull` / `ps`.
- **Logs** (follow, filter, highlight), **exec**, **inspect**.
- **Interactive shell inside a container** (real terminal in the browser).
- **Files inside a container:** browse and download files, or whole folders as `.tar`. This also works for stopped containers.
- **Images, Volumes, Networks** tabs: see what each is used by, find unused items, delete one or many.

### PM2
- Finds **every PM2 daemon on the server, for every user** (root, app users, …) and shows their processes together.
- Runs PM2 commands as the process owner through `sudo -u`, so you can manage other users' PM2 while logged in as yourself.
- Restart / reload / stop / start / flush / save.
- **Logs**: per process, or **many processes together** with "errors only", search highlight and follow.
- Choose which users' PM2 to show per server.

### Servers
- **Web terminal**: a full SSH shell to any server in the browser, with tabs. Minimised terminals stay open.
- **Files**: browse any folder, then copy a file to the clipboard, view it, or download it. It uses sudo automatically for files you can't read.
- **Disk cleanup**: shows what is using space (Docker images, build cache, container logs, journald, rotated logs, PM2 logs, big folders) and cleans it with one click, showing how much space was freed.
- **systemd services**: running and failed services, restart / stop / start, status and journal logs.
- **Run a command on many servers at once**, with saved commands.
- **Global search** (`Ctrl K`) over servers, containers, PM2 processes and ports.
- **Pages and groups**: separate Docker and PM2 pages, with your own groups (Production, Staging, …).
- Add servers from the UI (host, user, port, key or one-time password), or just use your `~/.ssh/config`.

---

## Requirements

**On your machine**
- Linux, macOS, or Windows 10/11 (WSL recommended, see below)
- Python **3.8+** (standard library only, no other dependencies)
- OpenSSH client (8.4+ if you want "add server with password", which needs Linux or macOS)
- For the `dctl` CLI: Bash, and optionally [`fzf`](https://github.com/junegunn/fzf) for the interactive picker
- A modern browser. The web terminal loads `xterm.js` from `cdn.jsdelivr.net`.

**On each server**
- `ssh` access
- Docker and/or PM2, as they already are
- `sudo` is optional; it is used only when your user can't do something directly

---

## Install

You get two commands: `dctl-ui` (web dashboard) and `dctl` (terminal CLI).

### Download an installer

Get the latest file from the **[Releases page](https://github.com/vishal-9878/dctl/releases/latest)**:

| OS | File | How to install |
|---|---|---|
| Ubuntu / Debian | `dctl_<version>_all.deb` | `sudo apt install ./dctl_<version>_all.deb` |
| Windows 10/11 | `dctl-<version>-windows-setup.exe` | Double-click. No admin rights needed. |
| macOS (Apple Silicon) | `dctl-<version>-macos-arm64.pkg` | Double-click. |
| macOS (Intel) | `dctl-<version>-macos-x86_64.pkg` | Double-click. |

After installing, open **dctl** from your applications menu / Start menu / Applications folder, or run `dctl-ui` in a terminal. A window shows the dashboard address; close it to stop dctl.

The Windows and macOS installers include their own Python, so nothing else is needed. The `.deb` uses the system `python3`.

> **First launch warning:** the installers are not code-signed yet.
> - **Windows** may show "Windows protected your PC". Click **More info → Run anyway**.
> - **macOS** may say the package "cannot be opened". **Right-click the .pkg → Open**, or go to **System Settings → Privacy & Security → Open Anyway**.

### Install script (no sudo / admin)

#### Linux and macOS

```bash
curl -fsSL https://raw.githubusercontent.com/vishal-9878/dctl/main/install.sh | sh
```

#### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/vishal-9878/dctl/main/install.ps1 | iex
```

> On native Windows the **web terminal** and the bash **`dctl` CLI** are not available, and each action opens a new SSH connection (Windows OpenSSH has no connection sharing). Everything else in the dashboard works.
> For the full experience, install inside **WSL** with the Linux command above.

#### With pipx (any OS)

```bash
pipx install "https://github.com/vishal-9878/dctl/archive/refs/heads/main.tar.gz"
```

The installers use pipx when it is available. Otherwise they create a private virtualenv (`~/.local/share/dctl` or `%LOCALAPPDATA%\dctl`), so your system Python is not touched.

### Update / uninstall

To update, install the newer release file (or run the install command again).

To uninstall:

| Installed with | Uninstall |
|---|---|
| `.deb` | `sudo apt remove dctl` |
| Windows setup `.exe` | Settings → Apps → **dctl** → Uninstall |
| macOS `.pkg` | `sudo rm -rf /Applications/dctl.app /usr/local/lib/dctl /usr/local/bin/dctl /usr/local/bin/dctl-ui && sudo pkgutil --forget io.github.vishal-9878.dctl` |
| pipx | `pipx uninstall dctl` |
| install script (Linux/macOS) | `rm -rf ~/.local/share/dctl ~/.local/bin/dctl ~/.local/bin/dctl-ui` |
| install script (Windows) | delete `%LOCALAPPDATA%\dctl` and remove its `bin` folder from your user PATH |

Your settings in `~/.config/dctl` are kept; delete that folder too for a clean removal.

### Run from a clone (for development)

```bash
git clone https://github.com/vishal-9878/dctl.git
cd dctl
./dctl-ui        # or: ./dctl ls
```

## Quick start

1. Make sure you can `ssh <server>` without typing a password (SSH keys).
   Servers are read from `~/.ssh/config` (every `Host` entry without wildcards).
2. Start the web UI:

   ```bash
   dctl-ui
   ```

   It opens `http://127.0.0.1:8765` in your browser.
3. Click **Add server**, or pick servers you already have in `~/.ssh/config`.

### Choosing servers

dctl looks for servers in this order:

1. `DCTL_SERVERS` environment variable, e.g. `DCTL_SERVERS="web1 web2 db1"`
2. `~/.config/dctl/servers`: one SSH host name per line
3. `~/.ssh/config`: every `Host` entry without wildcards

Servers added from the UI are stored in `~/.config/dctl/servers.json` and are always included.

---

## CLI

```text
dctl                  interactive picker (all containers on all servers, needs fzf)
dctl ls [text]        list containers on all servers (optionally filtered)
dctl where <name>     show server + compose file path of a container
dctl edit <name>      edit its compose file (download -> $EDITOR -> diff -> validate -> backup -> upload)
dctl up <name>        docker compose up -d for its project
dctl logs <name>      tail + follow logs
dctl sh <name>        shell inside the container
dctl cd <name>        ssh into the server, inside the compose directory
dctl inspect <name>   docker inspect
dctl restart|stop|start <name>
dctl ps <name>        docker compose ps for its project
dctl servers          show configured servers
dctl web              open the web UI
```

`<name>` can be partial (`api`), or `server:name` to be exact.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DCTL_PORT` | `8765` | Port of the web UI |
| `DCTL_NO_BROWSER` | unset | Set to `1` to not open a browser on start |
| `DCTL_SERVERS` | unset | Space- or comma-separated server list (overrides files) |
| `DCTL_CONF` | `~/.config/dctl` | Config folder |
| `DCTL_TAIL` | `200` | Default number of log lines in the CLI |

Files in `~/.config/dctl/`:

| File | What it holds |
|---|---|
| `servers` | Optional list of SSH hosts, one per line |
| `servers.json` | Servers added from the UI (mode `600`) |
| `layout.json` | Which server is on which page and group, and PM2 user choices |
| `commands.json` | Saved commands for "Run on many servers" |

---

## How it works

- Every action is a short POSIX `sh` script sent over SSH. SSH connection sharing (`ControlMaster`) keeps it fast.
- **Docker:** uses `docker` directly if your user is allowed to, otherwise `sudo docker`. If the server's sudoers forbids shells through sudo (`!/bin/sh`), dctl puts `sudo` on each single command instead.
- **PM2:** finds each PM2 "God Daemon" with `ps`, resolves that daemon's own `node` and `pm2` binaries, and runs `pm2` as the owning user with `sudo -u`. Process environment variables are stripped on the server, so secrets never leave it.
- **Web terminal:** a small WebSocket server in the standard library connects your browser to `ssh -tt` running in a local pseudo-terminal.

## Security

- The web UI listens on **127.0.0.1 only** and every request needs a random token that is created at each start. Requests from other origins are rejected.
- **sudo and SSH passwords are kept in memory only** and are never written to disk. They are forgotten when dctl-ui stops.
- Nothing is installed or left running on your servers.
- Destructive actions ask for confirmation. Deleting volumes, or many items at once, requires typing a word or the item's name.
- dctl can do anything your SSH user (and its sudo rights) can do. Treat access to your machine running dctl-ui like access to your servers.

To use the UI from another computer, use an SSH tunnel instead of exposing the port:

```bash
ssh -L 8765:127.0.0.1:8765 your-workstation
```

---

## Troubleshooting

| Problem | What to do |
|---|---|
| Server shows "no access" | `ssh <server>` must work without a password prompt. Check your key and `~/.ssh/config`. |
| Asked for a sudo password | Your user needs sudo for Docker there. Enter it once; it stays in memory until dctl-ui stops. |
| "sudo here does not allow running a shell" | The server forbids shells through sudo. dctl works around it; use `sudo` inside commands you run yourself. |
| Terminal doesn't open | The browser must be able to reach `cdn.jsdelivr.net` to load xterm.js. |
| PM2 of another user is locked | Your user needs `sudo -u <that user>` rights, or the sudo password. |

---

## Building the installers

GitHub Actions builds all installers (`.github/workflows/release.yml`). To publish a release:

1. Set the new version in `src/dctl/__init__.py`, then commit.
2. Tag and push:

   ```bash
   git tag v0.1.0
   git push origin main v0.1.0
   ```

The workflow builds the `.deb` on Ubuntu, the setup `.exe` on Windows (PyInstaller + Inno Setup) and the `.pkg` on macOS (arm64 and Intel), then attaches them to a GitHub Release.
You can also start it by hand from the **Actions** tab ("Run workflow") to get the files as artifacts without making a release.

To build the `.deb` locally: `packaging/build-deb.sh` (writes to `dist/`).

---

## License

[MIT](LICENSE)
