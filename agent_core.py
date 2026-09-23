"""
agent_core.py — shared logic for the SYS.AGENT tools.

Both the Streamlit UI (app.py) and the CLI (assistant.py) import from here so
that model access, code extraction, and — most importantly — the *hardened
Docker sandbox* stay identical instead of drifting apart.
"""
import hashlib
import json
import os
import re
import threading
import time

import docker
import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Signal the agent emits when it considers the objective finished.
TASK_DONE_MARKER = "TASK_COMPLETE"

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
AUDIT_LOG = os.path.join(LOG_DIR, "audit.log")
AUDIT_LOG_MAX_BYTES = int(os.getenv("AUDIT_LOG_MAX_BYTES", str(10 * 1024 * 1024)))  # 10MB
AUDIT_LOG_BACKUPS = int(os.getenv("AUDIT_LOG_BACKUPS", "5"))

# --- Model ---
DEFAULT_MODEL = os.getenv(
    "MODEL_NAME",
    "hf.co/bartowski/Qwen2.5-Coder-14B-Instruct-abliterated-GGUF:Q5_K_M",
)
LOCAL_API_URL = os.getenv("LOCAL_API_URL", "http://localhost:11434/v1")
LOCAL_API_KEY = os.getenv("LOCAL_API_KEY", "ollama")

# The bundled model is an "abliterated" (uncensored) build: its refusal
# behavior is stripped at the weights level, so nothing about the model
# itself enforces scope or safety — only the human-in-the-loop approval
# gate and prompt-level instructions do. Surfaced as a runtime banner in
# both front-ends so this is never just doc text nobody reads.
MODEL_SAFETY_WARNING = (
    "⚠️  UNCENSORED MODEL — guardrails are PROMPT-LEVEL ONLY, not model-level. "
    "Review every command at the approval gate before running it."
)

# --- Sandbox hardening config ---
# These bound what a single model-generated script can do to the host.
# Pinned to a digest (not just a tag) so a repointed upstream tag can't
# silently change what runs in the sandbox; override via SANDBOX_IMAGE if
# you rebuild/repin.
SANDBOX_IMAGE = os.getenv(
    "SANDBOX_IMAGE",
    "python:3.11-alpine@sha256:cd04730b8511def3fbf14204d66a0c1536f290b8e896ed5a94cd64cb15ac1356",
)
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "60"))        # seconds before kill
SANDBOX_MEM_LIMIT = os.getenv("SANDBOX_MEM_LIMIT", "256m")        # hard RAM cap
SANDBOX_PIDS_LIMIT = int(os.getenv("SANDBOX_PIDS_LIMIT", "128"))  # blocks fork bombs
SANDBOX_CPU_QUOTA = int(os.getenv("SANDBOX_CPU_QUOTA", "50000"))  # 50% of one core
# "bridge" = normal outbound internet, isolated from the host stack (unlike "host").
# Set SANDBOX_NETWORK=none to fully cut network access.
SANDBOX_NETWORK = os.getenv("SANDBOX_NETWORK", "bridge")
SANDBOX_OUTPUT_LIMIT = int(os.getenv("SANDBOX_OUTPUT_LIMIT", "20000"))  # chars returned


# ----------------------------------------------------------------------------
# Model access
# ----------------------------------------------------------------------------
def get_llm_client():
    """OpenAI-compatible client pointed at the local Ollama endpoint."""
    return OpenAI(base_url=LOCAL_API_URL, api_key=LOCAL_API_KEY)


def list_models():
    """Return available model names from the local Ollama server (best effort)."""
    base = LOCAL_API_URL.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        resp = requests.get(f"{base}/api/tags", timeout=5)
        resp.raise_for_status()
        names = [m["name"] for m in resp.json().get("models", [])]
        return names or [DEFAULT_MODEL]
    except Exception:
        return [DEFAULT_MODEL]


def ask_model(client, model, messages, temperature=0.1, retries=2):
    """Call the model with retry/backoff, returning text or a CONNECTION_ERROR string."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))  # linear backoff
    return f"CONNECTION_ERROR: Could not reach the local model after {retries + 1} tries.\n{last_err}"


def stream_model(client, model, messages, temperature=0.1, retries=2):
    """Yield response text chunks as they arrive, retrying before the first token."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            stream = client.chat.completions.create(
                model=model, messages=messages, temperature=temperature, stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
            return
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    yield f"CONNECTION_ERROR: Could not reach the local model after {retries + 1} tries.\n{last_err}"


def trim_history(messages, max_msgs=40):
    """Keep the system prompt + the most recent messages to bound context growth.

    Returns a new list; never drops the leading system message.
    """
    if len(messages) <= max_msgs:
        return list(messages)
    system = messages[:1] if messages and messages[0]["role"] == "system" else []
    keep = max_msgs - len(system)
    return system + messages[-keep:]


def _rotate_audit_log_if_needed():
    """Rotate audit.log -> audit.log.1 -> ... once it crosses AUDIT_LOG_MAX_BYTES."""
    try:
        if os.path.getsize(AUDIT_LOG) < AUDIT_LOG_MAX_BYTES:
            return
    except OSError:
        return
    oldest = f"{AUDIT_LOG}.{AUDIT_LOG_BACKUPS}"
    if os.path.exists(oldest):
        os.remove(oldest)
    for i in range(AUDIT_LOG_BACKUPS - 1, 0, -1):
        src = f"{AUDIT_LOG}.{i}"
        if os.path.exists(src):
            os.replace(src, f"{AUDIT_LOG}.{i + 1}")
    os.replace(AUDIT_LOG, f"{AUDIT_LOG}.1")


def audit_log(event):
    """Append a JSON-line audit record. Best effort; never raises to the caller."""
    try:
        _rotate_audit_log_if_needed()
        event = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **event}
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ----------------------------------------------------------------------------
# System prompt (shared by app.py and assistant.py so they can't drift)
# ----------------------------------------------------------------------------
BASE_SYSTEM_PROMPT = """You are an elite Security Architect in a Docker sandbox with network access.
1. WORKSPACE: Use `/workspace/` for all file operations.
2. TOOLS: You can write Python or Bash.
3. NETWORK: You can use curl, wget, or pip to fetch data or libraries.
4. FORMAT: Output ONLY code blocks (```python or ```bash)."""

AGENT_INSTRUCTIONS = (
    "\n\nAGENT MODE: Work toward the objective step by step. After each result you "
    "will be asked to continue. Emit the next single step as a code block. When the "
    f"objective is fully achieved, reply with the exact token {TASK_DONE_MARKER} "
    "and no code."
)


def build_system_prompt(scope=None, agent=False):
    """Compose the system prompt from the shared base + optional scope guardrail
    and agent-mode instructions. Used by both app.py and assistant.py."""
    prompt = BASE_SYSTEM_PROMPT
    if scope and scope.strip():
        prompt += ("\n\nAUTHORIZED SCOPE: You are ONLY permitted to act against the following "
                   f"in-scope targets: {scope.strip()}. Refuse anything outside this scope and say why.")
    if agent:
        prompt += AGENT_INSTRUCTIONS
    return prompt


# ----------------------------------------------------------------------------
# Code extraction / classification
# ----------------------------------------------------------------------------
_BLOCK_RE = re.compile(r"```(python|bash|sh)\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def extract_all_code(text):
    """Return a list of {'code', 'lang'} for every python/bash block, in order."""
    blocks = []
    for lang, body in _BLOCK_RE.findall(text or ""):
        lang = "python" if lang.lower() == "python" else "bash"
        code = body.strip()
        if code:
            blocks.append({"code": code, "lang": lang})
    return blocks


def extract_code(text):
    """Return (code, lang) for the first block, else (None, None). Back-compat helper."""
    blocks = extract_all_code(text)
    if blocks:
        return blocks[0]["code"], blocks[0]["lang"]
    return None, None


def looks_like_error(output):
    """Heuristic: did a sandbox run fail? Used to trigger the auto-healer."""
    if not output:
        return False
    markers = ("RUNTIME_ERROR", "Traceback (most recent call last)", "[exit code")
    return any(m in output for m in markers)


# ----------------------------------------------------------------------------
# Secret redaction (before output re-enters the model context / UI)
# ----------------------------------------------------------------------------
_SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|bearer)\b\s*[:=]\s*\S+"),
     r"\1=[REDACTED]"),
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
     "[REDACTED_JWT]"),
]


def redact_secrets(text):
    """Mask obvious credentials in sandbox output. Best effort, not exhaustive."""
    if not text:
        return text
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ----------------------------------------------------------------------------
# Docker sandbox
# ----------------------------------------------------------------------------
def get_docker_client():
    try:
        return docker.from_env()
    except Exception:
        return None


def docker_ok():
    """Lightweight health probe for the Docker daemon."""
    cli = get_docker_client()
    if not cli:
        return False
    try:
        cli.ping()
        return True
    except Exception:
        return False


def ollama_ok():
    """Lightweight health probe for the local model server."""
    base = LOCAL_API_URL.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        requests.get(f"{base}/api/tags", timeout=3).raise_for_status()
        return True
    except Exception:
        return False


def run_container(code, lang, workspace_dir, network=None, read_only=False,
                  image=None, redact=True, user=None, hardened_rootfs=False):
    """Execute model-generated code in a hardened, time-boxed Docker container.

    Returns (output_str, meta) where meta = {duration, exit_code, timed_out, ok}.

    Isolated network (bridge/none), capped RAM/PIDs/CPU, dropped Linux
    capabilities, and a wall-clock timeout keep an untrusted script boxed in.
    Opt-in extras (defaults preserve working behavior):
      user            — run as e.g. "1000:1000" or "nobody" instead of root.
      hardened_rootfs — read-only container filesystem with a writable /tmp tmpfs
                        (note: this blocks pip installs into site-packages).
    """
    meta = {"duration": 0.0, "exit_code": None, "timed_out": False, "ok": False}
    docker_cli = get_docker_client()
    if not docker_cli:
        return ("RUNTIME_ERROR: Docker client is not initialized. "
                "Please make sure Docker Desktop is running.", meta)

    net = network or SANDBOX_NETWORK
    started = time.monotonic()
    kwargs = _sandbox_kwargs(code, lang, workspace_dir, net, read_only, image,
                             user, hardened_rootfs)

    container = None
    try:
        container = docker_cli.containers.run(**kwargs)
        try:
            result = container.wait(timeout=SANDBOX_TIMEOUT)
        except Exception:
            try:
                container.kill()
            except Exception:
                pass
            meta["timed_out"] = True
            meta["duration"] = round(time.monotonic() - started, 2)
            partial = _finalize(_safe_logs(container), redact)
            _audit(code, lang, meta, net)
            return (f"RUNTIME_ERROR: Execution exceeded {SANDBOX_TIMEOUT}s and was terminated.\n"
                    f"--- partial output ---\n{partial}", meta)

        logs = _finalize(_safe_logs(container), redact)
        exit_code = result.get("StatusCode", 0) if isinstance(result, dict) else result
        meta["exit_code"] = exit_code
        meta["ok"] = exit_code in (0, None)
        meta["duration"] = round(time.monotonic() - started, 2)
        if exit_code not in (0, None):
            logs = f"[exit code {exit_code}]\n{logs}"
        _audit(code, lang, meta, net)
        return logs, meta
    except Exception as e:
        meta["duration"] = round(time.monotonic() - started, 2)
        _audit(code, lang, meta, net, error=str(e))
        return f"RUNTIME_ERROR: {str(e)}", meta
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


def _sandbox_kwargs(code, lang, workspace_dir, net, read_only, image, user, hardened_rootfs):
    """Build the docker run kwargs shared by the batch and streaming runners."""
    cmd = ["python3", "-c", code] if lang == "python" else ["sh", "-c", code]
    kwargs = dict(
        image=image or SANDBOX_IMAGE,
        command=cmd,
        volumes={workspace_dir: {"bind": "/workspace", "mode": "ro" if read_only else "rw"}},
        working_dir="/workspace",
        network_mode=net,
        mem_limit=SANDBOX_MEM_LIMIT,
        pids_limit=SANDBOX_PIDS_LIMIT,
        cpu_period=100000,
        cpu_quota=SANDBOX_CPU_QUOTA,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        detach=True,
        stderr=True,
        stdout=True,
    )
    if user:
        kwargs["user"] = user
    if hardened_rootfs:
        kwargs["read_only"] = True
        kwargs["tmpfs"] = {"/tmp": "size=64m", "/root": "size=16m"}
    return kwargs


def run_container_stream(code, lang, workspace_dir, meta_out, network=None,
                         read_only=False, image=None, redact=True, user=None,
                         hardened_rootfs=False):
    """Execute code and *yield* output as it is produced (live streaming).

    `meta_out` is a dict this generator fills in with the run metadata once the
    container exits. A watchdog thread enforces the same wall-clock timeout as the
    batch runner. Redaction is applied line-by-line so complete lines are masked
    before they are shown; the trailing partial line is redacted at the end.
    """
    meta_out.update({"duration": 0.0, "exit_code": None, "timed_out": False, "ok": False})
    docker_cli = get_docker_client()
    if not docker_cli:
        yield ("RUNTIME_ERROR: Docker client is not initialized. "
               "Please make sure Docker Desktop is running.")
        return

    net = network or SANDBOX_NETWORK
    kwargs = _sandbox_kwargs(code, lang, workspace_dir, net, read_only, image,
                             user, hardened_rootfs)
    started = time.monotonic()
    killed = {"v": False}
    container = None
    try:
        container = docker_cli.containers.run(**kwargs)
    except Exception as e:
        meta_out["duration"] = round(time.monotonic() - started, 2)
        _audit(code, lang, meta_out, net, error=str(e))
        yield f"RUNTIME_ERROR: {e}"
        return

    def _watchdog():
        deadline = started + SANDBOX_TIMEOUT
        while time.monotonic() < deadline:
            try:
                container.reload()
                if container.status in ("exited", "dead", "removing"):
                    return
            except Exception:
                return
            time.sleep(0.4)
        killed["v"] = True
        try:
            container.kill()
        except Exception:
            pass

    threading.Thread(target=_watchdog, daemon=True).start()

    emitted = 0
    buf = ""

    def _emit(text):
        nonlocal emitted
        if emitted >= SANDBOX_OUTPUT_LIMIT:
            return ""
        room = SANDBOX_OUTPUT_LIMIT - emitted
        chunk = text[:room]
        emitted += len(chunk)
        return chunk

    try:
        for raw in container.logs(stream=True, follow=True, stdout=True, stderr=True):
            buf += raw.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                out = _emit((redact_secrets(line) if redact else line) + "\n")
                if out:
                    yield out
    except Exception as e:
        yield f"\nRUNTIME_ERROR: {e}"
    if buf:  # trailing partial line
        out = _emit(redact_secrets(buf) if redact else buf)
        if out:
            yield out
    if emitted >= SANDBOX_OUTPUT_LIMIT:
        yield "\n...[output truncated]"

    try:
        result = container.wait(timeout=5)
        exit_code = result.get("StatusCode", None) if isinstance(result, dict) else result
    except Exception:
        exit_code = None
    meta_out["duration"] = round(time.monotonic() - started, 2)
    meta_out["timed_out"] = killed["v"]
    meta_out["exit_code"] = exit_code
    meta_out["ok"] = (exit_code in (0, None)) and not killed["v"]
    try:
        container.remove(force=True)
    except Exception:
        pass
    _audit(code, lang, meta_out, net)
    if killed["v"]:
        yield f"\nRUNTIME_ERROR: Execution exceeded {SANDBOX_TIMEOUT}s and was terminated."
    elif exit_code not in (0, None):
        yield f"\n[exit code {exit_code}]"


def _audit(code, lang, meta, net, error=None):
    audit_log({
        "event": "sandbox_run",
        "lang": lang,
        "network": net,
        "code_sha256": hashlib.sha256(code.encode("utf-8", "replace")).hexdigest()[:16],
        "code_len": len(code),
        "duration": meta.get("duration"),
        "exit_code": meta.get("exit_code"),
        "timed_out": meta.get("timed_out"),
        "error": error,
    })


def _finalize(text, redact):
    if redact:
        text = redact_secrets(text)
    if len(text) > SANDBOX_OUTPUT_LIMIT:
        text = text[:SANDBOX_OUTPUT_LIMIT] + "\n...[output truncated]"
    return text


def _safe_logs(container):
    try:
        return container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
    except Exception:
        return ""
