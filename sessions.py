"""
sessions.py — engagement persistence + reporting for SYS.AGENT.

Saves/loads conversations as JSON under ./sessions and renders Markdown
transcripts and findings reports from a message ledger.
"""
import base64
import hashlib
import json
import os
import re
import time

SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")

# Saved sessions can capture live recon data (hosts, hashes, headers) from
# real engagements, so encryption-at-rest is supported: set SESSIONS_KEY
# (any passphrase) to encrypt new saves with it. Existing plaintext saves
# still load; leaving it unset keeps the previous plaintext-JSON behavior.
SESSIONS_KEY = os.getenv("SESSIONS_KEY")


def _fernet():
    """Build a Fernet instance from SESSIONS_KEY, or None if encryption is off."""
    if not SESSIONS_KEY:
        return None
    from cryptography.fernet import Fernet
    digest = hashlib.sha256(SESSIONS_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _safe_name(name):
    name = _SAFE.sub("_", (name or "engagement").strip()) or "engagement"
    return name[:64]


def save_session(name, messages, meta=None):
    """Persist a message ledger to ./sessions/<name>.json. Returns the path.

    Encrypted at rest when SESSIONS_KEY is set (payload becomes a Fernet
    token wrapped in a small JSON envelope); plaintext JSON otherwise.
    """
    path = os.path.join(SESSIONS_DIR, _safe_name(name) + ".json")
    payload = {
        "name": name,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "meta": meta or {},
        "messages": messages,
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    fernet = _fernet()
    with open(path, "w", encoding="utf-8") as f:
        if fernet:
            json.dump({"encrypted": True, "data": fernet.encrypt(raw).decode("ascii")}, f)
        else:
            f.write(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def list_sessions():
    """Return saved engagement names (without extension), newest first."""
    items = []
    for fn in os.listdir(SESSIONS_DIR):
        if fn.endswith(".json"):
            full = os.path.join(SESSIONS_DIR, fn)
            items.append((fn[:-5], os.path.getmtime(full)))
    items.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in items]


def load_session(name):
    """Load a saved engagement; returns its messages list (or None).

    Transparently handles both encrypted (SESSIONS_KEY) and plaintext saves.
    """
    path = os.path.join(SESSIONS_DIR, _safe_name(name) + ".json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if isinstance(doc, dict) and doc.get("encrypted"):
        fernet = _fernet()
        if not fernet:
            raise ValueError(f"Session '{name}' is encrypted but SESSIONS_KEY is not set.")
        doc = json.loads(fernet.decrypt(doc["data"].encode("ascii")).decode("utf-8"))
    return doc.get("messages")


def delete_session(name):
    path = os.path.join(SESSIONS_DIR, _safe_name(name) + ".json")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def export_transcript(messages, title="SYS.AGENT Engagement"):
    """Render the conversation as a Markdown transcript."""
    lines = [f"# {title}", "", f"_Exported {time.strftime('%Y-%m-%d %H:%M:%S')}_", ""]
    role_label = {"user": "🧑 Operator", "assistant": "🤖 Agent", "system": "⚙️ System"}
    for m in messages:
        if m["role"] == "system":
            continue
        lines.append(f"### {role_label.get(m['role'], m['role'])}")
        lines.append("")
        lines.append(m["content"])
        lines.append("")
    return "\n".join(lines)


SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Info": 4}


def _findings_table(findings):
    if not findings:
        return ["_No findings recorded._", ""]
    ordered = sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.get("severity", "Info"), 9))
    rows = ["| # | Severity | Title | Target | Notes |",
            "|---|----------|-------|--------|-------|"]
    for i, f in enumerate(ordered, 1):
        notes = (f.get("notes", "") or "").replace("\n", " ").replace("|", "\\|")
        rows.append(f"| {i} | {f.get('severity','Info')} | {f.get('title','')} "
                    f"| {f.get('target','')} | {notes} |")
    rows.append("")
    return rows


def build_report(messages, target="(unspecified)", title="Security Engagement Report",
                 findings=None):
    """Produce a structured Markdown findings report from a session ledger.

    Surfaces recorded findings (severity-ranked) plus every executed command and
    its result. The operator fills in the narrative sections.
    """
    runs = []
    pending_cmd = None
    for m in messages:
        content = m["content"]
        if m["role"] == "assistant" and "```" in content:
            pending_cmd = content
        elif m["role"] == "user" and content.startswith("RESULT:"):
            runs.append((pending_cmd, content[len("RESULT:"):].strip()))
            pending_cmd = None

    lines = [
        f"# {title}", "",
        f"- **Target scope:** {target}",
        f"- **Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Executed commands:** {len(runs)}",
        f"- **Findings recorded:** {len(findings or [])}",
        "",
        "## Executive Summary", "",
        "_Summarize the objective, scope, and headline findings here._", "",
        "## Findings", "",
        *_findings_table(findings or []),
        "## Activity Log", "",
    ]
    if not runs:
        lines.append("_No sandbox executions were recorded in this session._")
    for i, (cmd, result) in enumerate(runs, 1):
        lines += [
            f"### Step {i}", "",
            "**Command / script:**", "",
            (cmd or "_n/a_").strip(), "",
            "**Result:**", "",
            "```", result, "```", "",
            "**Finding / notes:** _fill in severity, evidence, remediation_", "",
        ]
    return "\n".join(lines)
