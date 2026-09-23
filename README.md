# SYS.AGENT // SECURE_SHELL

A local, offline **AI security/coding agent**. It talks to an uncensored
Qwen2.5-Coder model running in [Ollama](https://ollama.com), generates Python/Bash,
and executes it inside a **hardened, throwaway Docker sandbox** — with a
human-in-the-loop approval gate on every run.

Two front-ends share one core:

| File | Role |
|------|------|
| `app.py` | Streamlit "hacker terminal" UI |
| `assistant.py` | CLI with approval + auto-healer |
| `agent_core.py` | Shared model access + hardened sandbox |
| `sessions.py` | Save/load engagements, transcript + report export |

## Prerequisites

1. **Docker Desktop** running.
2. **Ollama** running with the model pulled:
   ```bash
   ollama pull hf.co/bartowski/Qwen2.5-Coder-14B-Instruct-abliterated-GGUF:Q5_K_M
   ```
3. Python 3.10+.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then edit if needed
docker pull python:3.11-alpine
```

Optionally build the pre-baked tool image (nmap, curl, scapy, requests, …) so
the agent doesn't `pip install` on every run:

```bash
docker build -t sysagent-tools -f Dockerfile.tools .
# then set SANDBOX_IMAGE=sysagent-tools in .env
```

## Run

**UI:**
```bash
streamlit run app.py
```
**CLI:**
```bash
python assistant.py
```

## Features

- **Agent mode** — autonomously plans and executes step-by-step toward an
  objective (each step still passes the approval gate), with a step budget and a
  `TASK_COMPLETE` stop signal.
- **Streaming responses**, an Ollama **model picker**, and **retry/backoff** on the model.
- **Human-in-the-loop approval** — every code block is editable, shows a **diff vs
  the last run** (handy when the healer regenerates code), and only runs on approval.
- **Multi-block execution** — run each block the model emits, in order.
- **Live sandbox output** — optionally stream container stdout/stderr as the
  script runs (line-level secret redaction, same wall-clock timeout), instead of
  waiting for the batch result.
- **Auto-healer** — failed runs are fed back to the model to self-correct (still gated).
- **Hardened sandbox** — isolated network, RAM/PID/CPU caps, dropped Linux
  capabilities, `no-new-privileges`, wall-clock timeout, output truncation. Opt-in:
  **non-root user** and **read-only rootfs + tmpfs**.
- **Config profiles** (Standard / Recon / CTF / Offline) set network + hardening together.
- **Sandbox controls** — network (`bridge`/`none`), read-only workspace, secret redaction.
- **Findings tracker** — record severity-ranked findings that feed the report.
- **Engagements** — save/load sessions, export a Markdown transcript, findings report,
  or the **JSON-lines audit log** of every sandbox run.
- **Context trimming** so long sessions don't overflow the model's window.
- **Authorized-scope guardrail** — constrains the agent to declared in-scope targets.
- **Command presets** and **Docker/Ollama health badges**.
- **Rotating audit log** — `audit.log` rotates once it crosses `AUDIT_LOG_MAX_BYTES`
  (default 10MB, `AUDIT_LOG_BACKUPS` backups kept) instead of growing unbounded.
- **Optional session encryption-at-rest** — set `SESSIONS_KEY` to encrypt saved
  engagements/findings on disk (they may contain live recon data); unset = plaintext JSON.
- **Wipe confirmation** — clearing the workspace requires a second, explicit confirm click.
- **Runtime model-safety banner** — both front-ends print/display a warning that
  the bundled "abliterated" model has no built-in guardrails; only the
  approval gate and prompt-level scope enforce safety.

## Docker deployment

```bash
docker compose up --build      # app on http://127.0.0.1:8501 (localhost-only by default)
```

`docker-compose.yml` binds the app to `127.0.0.1` and mounts the Docker socket
(required so the app can spawn sandbox containers via the host daemon — this
is effectively root on the host, see the comments in that file). Don't
publish this port beyond localhost, and don't run it on a shared or
internet-facing machine without a reverse proxy with authentication in front.

## CI

`.github/workflows/tests.yml` compiles the modules and runs `test_core.py` on every push/PR.

## Configuration

All tunables live in `.env` (see `.env.example`): model + endpoint, sandbox
limits (`SANDBOX_TIMEOUT`, `SANDBOX_MEM_LIMIT`, `SANDBOX_NETWORK`, …),
`MAX_HEAL_RETRIES`, audit log rotation, and session encryption (`SESSIONS_KEY`).

The default sandbox image is pinned to a digest (not just the `3.11-alpine`
tag) so an upstream retag can't silently change what runs in the sandbox. If
you build your own tool image, repin `SANDBOX_IMAGE` to its digest too:
```bash
docker inspect --format='{{index .RepoDigests 0}}' sysagent-tools
```

## Tests

```bash
python test_core.py
```

## Safety

Only use against systems you are **authorized** to test. The sandbox limits what
a single script can do to the host, but the model is uncensored — review every
command at the approval gate before running it.
