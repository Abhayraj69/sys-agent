import os
import shutil
import streamlit as st

import agent_core
import sessions
from agent_core import (
    extract_all_code, ask_model, stream_model, run_container,
    looks_like_error, list_models, docker_ok, ollama_ok,
)

# --- 1. UI INITIALIZATION ---
st.set_page_config(
    page_title="SYS.AGENT // TERMINAL", 
    page_icon="💀", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- HACKER TERMINAL STYLING ---
# Rendered via st.html (not st.markdown) so indented CSS isn't parsed as a code block.
st.html("""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <style>
    :root{
        --neon:#00ff9c; --neon-dim:#0aa66a; --amber:#ffb000;
        --bg:#040806; --panel:#070d0a; --grid:rgba(0,255,156,.05);
        --scan:rgba(0,255,156,.04);
    }
    header, footer, #MainMenu {visibility:hidden;}
    .stApp{
        background:
            radial-gradient(1200px 600px at 50% -10%, rgba(0,255,156,.06), transparent 60%),
            linear-gradient(0deg,var(--bg),var(--bg));
        color:var(--neon);
        font-family:'JetBrains Mono','Share Tech Mono',monospace;
    }
    /* faint CRT grid + scanlines over the whole app */
    .stApp:before{
        content:""; position:fixed; inset:0; pointer-events:none; z-index:0;
        background-image:
            linear-gradient(var(--grid) 1px,transparent 1px),
            linear-gradient(90deg,var(--grid) 1px,transparent 1px);
        background-size:32px 32px;
    }
    .stApp:after{
        content:""; position:fixed; inset:0; pointer-events:none; z-index:9999;
        background:repeating-linear-gradient(0deg,var(--scan),var(--scan) 1px,transparent 2px,transparent 3px);
        mix-blend-mode:overlay; opacity:.6;
    }
    .block-container{position:relative; z-index:1; padding-top:2.2rem;}

    /* headings + text glow */
    h1,h2,h3,h4{
        font-family:'Share Tech Mono',monospace !important;
        letter-spacing:.06em; text-transform:uppercase;
        text-shadow:0 0 6px rgba(0,255,156,.55);
    }
    .stMarkdown, p, li, label, span{ text-shadow:0 0 2px rgba(0,255,156,.18); }

    /* inputs */
    .stTextInput input, .stChatInput textarea{
        border-radius:0 !important; border:1px solid var(--neon-dim) !important;
        background:#020402 !important; color:var(--neon) !important;
        box-shadow:inset 0 0 12px rgba(0,255,156,.08) !important;
        font-family:'JetBrains Mono',monospace !important;
    }
    .stChatInput textarea:focus, .stTextInput input:focus{
        border-color:var(--neon) !important;
        box-shadow:0 0 10px rgba(0,255,156,.35), inset 0 0 12px rgba(0,255,156,.12) !important;
    }
    [data-testid="stChatInput"]{
        border:1px solid var(--neon-dim); box-shadow:0 0 18px rgba(0,255,156,.12);
        background:#020402;
    }

    /* buttons */
    .stButton>button, .stDownloadButton>button{
        border-radius:0 !important; border:1px solid var(--neon-dim) !important;
        background:#020402 !important; color:var(--neon) !important; width:100%;
        letter-spacing:.08em; text-transform:uppercase; font-weight:500;
        transition:all .15s ease;
    }
    .stButton>button:hover, .stDownloadButton>button:hover{
        background:var(--neon) !important; color:#020402 !important;
        box-shadow:0 0 14px var(--neon), 0 0 30px rgba(0,255,156,.4);
        border-color:var(--neon) !important;
    }

    /* sidebar */
    [data-testid="stSidebar"]{
        background:linear-gradient(180deg,#050a07,#020402) !important;
        border-right:1px solid var(--neon-dim);
        box-shadow:2px 0 20px rgba(0,255,156,.08);
    }

    /* chat bubbles */
    .stChatMessage{
        background:linear-gradient(180deg,rgba(0,255,156,.03),transparent) !important;
        border:1px solid rgba(0,255,156,.14) !important; border-left:2px solid var(--neon) !important;
        border-radius:0 !important; margin-bottom:12px;
    }
    [data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"]{
        background:#020402 !important; border:1px solid var(--neon-dim) !important; color:var(--neon) !important;
    }
    /* code + result blocks: proper terminal look */
    pre, code, .stCode, [data-testid="stCodeBlock"]{
        background:#010301 !important; border:1px solid rgba(0,255,156,.18) !important;
        border-radius:0 !important;
    }
    pre code{ color:#7dffc4 !important; text-shadow:0 0 4px rgba(0,255,156,.3); }

    /* alerts styled as terminal banners */
    [data-testid="stAlert"]{ border-radius:0 !important; border-left:3px solid var(--amber) !important; }

    /* --- custom boot header --- */
    .sys-header{
        border:1px solid var(--neon-dim); background:linear-gradient(180deg,rgba(0,255,156,.04),transparent);
        padding:14px 18px; margin-bottom:8px; position:relative; overflow:hidden;
        box-shadow:0 0 24px rgba(0,255,156,.1), inset 0 0 30px rgba(0,255,156,.04);
    }
    .sys-header .bar{
        display:flex; gap:6px; margin-bottom:10px;
    }
    .sys-header .dot{width:11px;height:11px;border-radius:50%;box-shadow:0 0 6px currentColor;}
    .sys-header .title{
        font-family:'Share Tech Mono',monospace; font-size:2.1rem; line-height:1.1;
        color:var(--neon); text-shadow:0 0 10px rgba(0,255,156,.7); letter-spacing:.05em;
    }
    .sys-header .sub{ color:var(--neon-dim); font-size:.82rem; letter-spacing:.12em; margin-top:4px; }
    .sys-header .cursor{ display:inline-block; width:10px; height:1.1em; background:var(--neon);
        margin-left:4px; vertical-align:-2px; animation:blink 1s steps(2) infinite; box-shadow:0 0 8px var(--neon);}
    @keyframes blink{50%{opacity:0;}}
    .sys-header .flick{ animation:flick 4s infinite; }
    @keyframes flick{0%,97%,100%{opacity:1;}98%{opacity:.75;}99%{opacity:.9;}}
    .status-line{ color:var(--neon-dim); font-size:.78rem; letter-spacing:.1em; margin:6px 0 14px; }
    .status-line b{ color:var(--neon); }
    </style>
""")

st.html("""
<div class="sys-header flick">
  <div class="bar">
    <span class="dot" style="color:#ff5f56;background:#ff5f56;"></span>
    <span class="dot" style="color:#ffbd2e;background:#ffbd2e;"></span>
    <span class="dot" style="color:#27c93f;background:#27c93f;"></span>
  </div>
  <div class="title">▓ SYS.AGENT // SECURE_SHELL<span class="cursor"></span></div>
  <div class="sub">root@sys-agent:~# autonomous_security_architect --sandbox=docker --net=isolated</div>
</div>
<div class="status-line">[ SESSION <b>ACTIVE</b> ] · [ SANDBOX <b>HARDENED</b> ] · [ HUMAN-IN-THE-LOOP <b>ON</b> ] · [ ENCRYPTION <b>AES-256</b> ]</div>
""")

st.warning(agent_core.MODEL_SAFETY_WARNING)

# --- 2. BACKEND SETUP ---
# All model access and the hardened Docker sandbox live in agent_core, shared
# with the CLI (assistant.py) so the two front-ends can never drift apart.

@st.cache_resource
def get_llm_client():
    return agent_core.get_llm_client()

@st.cache_data(ttl=30)
def cached_models():
    return list_models()

# __file__-relative (not cwd-relative) so the workspace lands in the same place
# regardless of where `streamlit run` is invoked from — matches assistant.py / sessions.py.
WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)

MAX_HEAL_RETRIES = int(os.getenv("MAX_HEAL_RETRIES", "2"))

BASE_SYSTEM_PROMPT = agent_core.BASE_SYSTEM_PROMPT

# Curated command presets (label -> prompt sent to the agent).
PRESETS = {
    "— select a preset —": "",
    "Port scan a host": "Perform a TCP port scan of the target using a pure-Python socket scan of the top 100 ports. Print open ports.",
    "HTTP header analysis": "Fetch the target URL and analyze the HTTP response headers for missing security headers (CSP, HSTS, X-Frame-Options, etc.). Summarize findings.",
    "Hash identify + crack (wordlist)": "Identify the hash type of the provided hash, then attempt a dictionary attack using a small built-in wordlist. Report the result.",
    "DNS / subdomain recon": "Given a domain, enumerate common subdomains by resolving a built-in list of prefixes and report which resolve.",
    "File hash + entropy": "For each file in /workspace, compute its SHA-256 and Shannon entropy to flag possibly packed/encrypted files.",
}


# Config profiles: one click sets network + hardening + prompt posture together.
PROFILES = {
    "Standard": {"network": "bridge", "hardened_rootfs": False, "nonroot": False},
    "Recon (net, hardened)": {"network": "bridge", "hardened_rootfs": True, "nonroot": True},
    "CTF (net, root ok)": {"network": "bridge", "hardened_rootfs": False, "nonroot": False},
    "Offline (no net)": {"network": "none", "hardened_rootfs": True, "nonroot": True},
}

build_system_prompt = agent_core.build_system_prompt


def wipe_workspace():
    for filename in os.listdir(WORKSPACE_DIR):
        fp = os.path.join(WORKSPACE_DIR, filename)
        try:
            if os.path.isfile(fp):
                os.unlink(fp)
            elif os.path.isdir(fp):
                shutil.rmtree(fp)
        except Exception as e:
            st.error(f"Error: {e}")


# --- Session state init ---
ss = st.session_state
if "messages" not in ss:
    ss.messages = [{"role": "system", "content": BASE_SYSTEM_PROMPT}]
ss.setdefault("pending_blocks", [])   # queue of {code, lang} awaiting approval
ss.setdefault("heal_count", 0)
ss.setdefault("last_meta", None)
ss.setdefault("scope", "")
ss.setdefault("findings", [])         # list of {severity, title, target, notes}
ss.setdefault("step_count", 0)        # agent-mode steps taken this objective
ss.setdefault("last_code", None)      # last executed code (for heal diff)

client = get_llm_client()

# --- 3. SIDEBAR ---
with st.sidebar:
    st.header("📂 CONTROLS")

    # Health badges
    d_ok, o_ok = docker_ok(), ollama_ok()
    st.markdown(
        f"**DOCKER** {'🟢' if d_ok else '🔴'}  &nbsp; **OLLAMA** {'🟢' if o_ok else '🔴'}",
    )

    # Model picker
    model_list = cached_models()
    default_model = agent_core.DEFAULT_MODEL
    idx = model_list.index(default_model) if default_model in model_list else 0
    MODEL_NAME = st.selectbox("🧠 Model", model_list, index=idx)

    profile_name = st.selectbox("🧩 Profile", list(PROFILES.keys()))
    prof = PROFILES[profile_name]

    with st.expander("⚙️ Sandbox", expanded=False):
        net_idx = ["bridge", "none"].index(prof["network"])
        NETWORK = st.radio("Network", ["bridge", "none"], index=net_idx, horizontal=True,
                           help="bridge = isolated internet · none = no network")
        READ_ONLY = st.checkbox("Read-only workspace", value=False)
        HARDENED_ROOTFS = st.checkbox("Read-only rootfs (+tmpfs)", value=prof["hardened_rootfs"],
                                      help="Blocks pip installs into site-packages.")
        NONROOT = st.checkbox("Run as non-root", value=prof["nonroot"])
        REDACT = st.checkbox("Redact secrets in output", value=True)
        STREAMING = st.checkbox("Stream responses", value=True)
        STREAM_OUTPUT = st.checkbox("Stream sandbox output (live)", value=False,
                                    help="Show container stdout/stderr live as the script runs.")

    ac1, ac2 = st.columns(2)
    AUTO_HEAL = ac1.checkbox("🔧 Auto-Heal", value=True,
                             help="On a failed run, feed the error back to the model to self-correct.")
    AGENT_MODE = ac2.checkbox("🤖 Agent", value=False,
                              help="Autonomously continue step-by-step toward the objective "
                                   "(each step still needs approval).")
    AGENT_MAX_STEPS = st.slider("Max agent steps", 1, 15, 6) if AGENT_MODE else 0
    SANDBOX_USER = "nobody" if NONROOT else None

    st.write("---")
    ss.scope = st.text_input("🎯 Authorized scope", value=ss.scope,
                             placeholder="e.g. 10.0.0.0/24, example.com",
                             help="Injected into the system prompt to keep the agent in-scope.")

    preset = st.selectbox("⚡ Preset", list(PRESETS.keys()))

    # --- Findings tracker ---
    with st.expander(f"🧾 Findings ({len(ss.findings)})", expanded=False):
        with st.form("add_finding", clear_on_submit=True):
            ft = st.text_input("Title")
            fsev = st.selectbox("Severity", ["Critical", "High", "Medium", "Low", "Info"], index=2)
            ftgt = st.text_input("Target")
            fnotes = st.text_area("Notes", height=60)
            if st.form_submit_button("➕ Add finding") and ft:
                ss.findings.append({"severity": fsev, "title": ft, "target": ftgt, "notes": fnotes})
                st.rerun()
        for i, fnd in enumerate(ss.findings):
            fc1, fc2 = st.columns([0.85, 0.15])
            fc1.markdown(f"**[{fnd['severity']}]** {fnd['title']}  \n`{fnd['target']}`")
            if fc2.button("✕", key=f"delf_{i}"):
                ss.findings.pop(i)
                st.rerun()

    st.write("---")
    st.subheader("📁 Workspace")
    uploaded_file = st.file_uploader("Upload to Sandbox", label_visibility="collapsed")
    if uploaded_file:
        with open(os.path.join(WORKSPACE_DIR, uploaded_file.name), "wb") as f:
            f.write(uploaded_file.getbuffer())
        st.success(f"Deployed: {uploaded_file.name}")

    files = sorted(os.listdir(WORKSPACE_DIR)) if os.path.exists(WORKSPACE_DIR) else []
    for f in files:
        c1, c2 = st.columns([0.8, 0.2])
        c1.text(f"📄 {f}")
        with open(os.path.join(WORKSPACE_DIR, f), "rb") as fd:
            c2.download_button("⬇️", data=fd, file_name=f, key=f"dl_{f}")
    ss.setdefault("confirm_wipe", False)
    if not ss.confirm_wipe:
        if st.button("💀 WIPE WORKSPACE"):
            ss.confirm_wipe = True
            st.rerun()
    else:
        st.error("Delete ALL files in the workspace? This cannot be undone.")
        wc1, wc2 = st.columns(2)
        if wc1.button("✅ Confirm wipe"):
            wipe_workspace()
            ss.confirm_wipe = False
            st.rerun()
        if wc2.button("✕ Cancel"):
            ss.confirm_wipe = False
            st.rerun()

    st.write("---")
    st.subheader("💾 Engagements")
    sess_name = st.text_input("Name", value="engagement", label_visibility="collapsed")
    sc1, sc2 = st.columns(2)
    if sc1.button("Save"):
        sessions.save_session(sess_name, ss.messages)
        st.success("Saved")
    saved = sessions.list_sessions()
    chosen = sc2.selectbox("Load", ["—"] + saved, label_visibility="collapsed")
    if chosen and chosen != "—":
        loaded = sessions.load_session(chosen)
        if loaded:
            ss.messages = loaded
            ss.pending_blocks = []
            st.rerun()

    st.download_button("⬇️ Export transcript (.md)",
                       data=sessions.export_transcript(ss.messages),
                       file_name="transcript.md")
    st.download_button("📑 Generate report (.md)",
                       data=sessions.build_report(ss.messages, target=ss.scope or "(unspecified)",
                                                  findings=ss.findings),
                       file_name="report.md")
    if os.path.exists(agent_core.AUDIT_LOG):
        with open(agent_core.AUDIT_LOG, "rb") as _a:
            st.download_button("🗂 Audit log (.jsonl)", data=_a, file_name="audit.log")


# --- 4. AGENT TURN HELPERS ---
def generate_and_stage():
    """Ask the model for a turn (optionally streamed) and queue any code blocks."""
    context = agent_core.trim_history(ss.messages)
    with st.chat_message("assistant"):
        if STREAMING:
            ans = st.write_stream(stream_model(client, MODEL_NAME, context))
        else:
            with st.spinner("THINKING..."):
                ans = ask_model(client, MODEL_NAME, context)
            st.markdown(ans)
    ss.messages.append({"role": "assistant", "content": ans})
    ss.pending_blocks = extract_all_code(ans)


def submit_prompt(text):
    """Handle a new operator command (from chat input or a preset)."""
    ss.heal_count = 0
    ss.step_count = 0
    # Keep the system prompt in sync with scope + agent mode.
    ss.messages[0] = {"role": "system", "content": build_system_prompt(ss.scope, AGENT_MODE)}
    ss.messages.append({"role": "user", "content": text})
    with st.chat_message("user"):
        st.markdown(text)
    generate_and_stage()
    st.rerun()


# --- 5. RENDER HISTORY ---
for msg in ss.messages:
    if msg["role"] != "system":
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

# Resource dashboard for the most recent run
if ss.last_meta:
    m = ss.last_meta
    status = "🔴 TIMEOUT" if m["timed_out"] else ("🟢 OK" if m["ok"] else "🟠 ERR")
    st.caption(f"⏱ last run: {m['duration']}s · exit={m['exit_code']} · {status}")

# --- 6. HUMAN-IN-THE-LOOP APPROVAL GATE (multi-block, editable) ---
# Model-generated code is NEVER executed automatically. Each block parks here,
# stays fully editable, and only runs on explicit approval.
pending = bool(ss.pending_blocks)
if pending:
    block = ss.pending_blocks[0]
    remaining = len(ss.pending_blocks)
    st.warning(f"⚠️ REVIEW REQUIRED // {remaining} block(s) queued. Edit if needed, then approve.")
    # Diff vs the last executed code (useful when the auto-healer regenerated it).
    if ss.last_code and ss.last_code.strip() != block["code"].strip():
        import difflib
        diff = "\n".join(difflib.unified_diff(
            ss.last_code.splitlines(), block["code"].splitlines(),
            fromfile="previous", tofile="proposed", lineterm=""))
        with st.expander("Δ diff vs last run"):
            st.code(diff or "(identical)", language="diff")
    edited = st.text_area(f"[{block['lang']}] block", value=block["code"], height=200,
                          key=f"edit_{len(ss.messages)}_{remaining}")
    col_ok, col_skip, col_no = st.columns(3)
    if col_ok.button("✅ APPROVE & RUN"):
        opts = dict(network=NETWORK, read_only=READ_ONLY, redact=REDACT,
                    user=SANDBOX_USER, hardened_rootfs=HARDENED_ROOTFS)
        if STREAM_OUTPUT:
            meta = {}
            st.markdown("**🐳 SANDBOX OUTPUT (live):**")
            result = st.write_stream(
                agent_core.run_container_stream(edited, block["lang"], WORKSPACE_DIR, meta, **opts))
        else:
            with st.spinner("EXECUTING IN SANDBOX..."):
                result, meta = run_container(edited, block["lang"], WORKSPACE_DIR, **opts)
        ss.last_meta = meta
        ss.last_code = edited
        ss.messages.append({"role": "user",
                            "content": f"RESULT:\n{result if result else '[SUCCESS — no output]'}"})
        ss.pending_blocks.pop(0)
        st.rerun()
    if col_skip.button("⏭ SKIP BLOCK"):
        ss.pending_blocks.pop(0)
        st.rerun()
    if col_no.button("🛑 REJECT ALL"):
        ss.messages.append({"role": "user", "content": "[Execution rejected by operator]"})
        ss.pending_blocks = []
        ss.heal_count = 0
        st.rerun()

# --- 7. AUTO-HEALER ---
if not pending:
    last = ss.messages[-1]
    if last["role"] == "user" and last["content"].startswith("RESULT:"):
        if looks_like_error(last["content"]):
            if AUTO_HEAL and ss.heal_count < MAX_HEAL_RETRIES:
                ss.heal_count += 1
                st.info(f"🔧 Auto-Healer: last run failed — asking the model to fix it "
                        f"(attempt {ss.heal_count}/{MAX_HEAL_RETRIES}).")
                ss.messages.append({
                    "role": "user",
                    "content": "Your last script failed (see the RESULT above). "
                               "Return corrected script(s) only.",
                })
                generate_and_stage()
                st.rerun()
            elif ss.heal_count >= MAX_HEAL_RETRIES:
                st.error("❌ Auto-Healer: max retries reached. Refine your command and try again.")
        else:
            ss.heal_count = 0

# --- 7b. AGENT MODE: autonomous step-by-step continuation ---
# After a clean run, ask the model for the next step toward the objective. Each
# generated step still passes through the approval gate above.
if AGENT_MODE and not pending:
    last = ss.messages[-1]
    recent_assistant = next((m["content"] for m in reversed(ss.messages)
                             if m["role"] == "assistant"), "")
    done = agent_core.TASK_DONE_MARKER in recent_assistant
    if (last["role"] == "user" and last["content"].startswith("RESULT:")
            and not looks_like_error(last["content"]) and not done):
        if ss.step_count < AGENT_MAX_STEPS:
            ss.step_count += 1
            st.info(f"🤖 Agent: planning next step ({ss.step_count}/{AGENT_MAX_STEPS})…")
            ss.messages.append({
                "role": "user",
                "content": ("Continue toward the objective with the next single step, "
                            f"or reply {agent_core.TASK_DONE_MARKER} if it is complete."),
            })
            generate_and_stage()
            st.rerun()
        else:
            st.warning("🤖 Agent: step budget reached. Send a new command to continue.")
    elif done:
        st.success("🤖 Agent: objective marked complete.")

# --- 8. INPUT (chat + preset) ---
prompt = st.chat_input("ENTER COMMAND...", disabled=pending)
if prompt:
    submit_prompt(prompt)
elif not pending and PRESETS.get(preset):
    if st.button(f"▶ RUN PRESET: {preset}"):
        submit_prompt(PRESETS[preset])