"""
assistant.py — CLI front-end for SYS.AGENT.

Human-in-the-loop approval + auto-healer, running against the same hardened
Docker sandbox as the Streamlit UI (both import from agent_core).
"""
import os

from agent_core import (
    DEFAULT_MODEL,
    MODEL_SAFETY_WARNING,
    get_llm_client,
    extract_all_code,
    ask_model,
    run_container,
    looks_like_error,
    redact_secrets,
    build_system_prompt,
)

client = get_llm_client()

WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)

MAX_RETRIES = int(os.getenv("MAX_HEAL_RETRIES", "2"))

# Shared with app.py (agent_core.build_system_prompt) so the two front-ends
# can't drift: no scope configured here, so this is just the base prompt.
SYSTEM_PROMPT = build_system_prompt()


def human_in_the_loop(code, lang):
    print("\n" + "=" * 40)
    print("⚠️  ACTION REQUIRED: REVIEW GENERATED CODE ⚠️")
    print("=" * 40)
    print(f"[{lang}]\n{code}")
    print("=" * 40)
    choice = input("\nApprove execution in the secure sandbox? (y/n): ").strip().lower()
    return choice == "y"


def main():
    print("========================================")
    print("🚀 Secure Local AI Agent Initialized")
    print("   [Memory Active | Workspace Mounted | Hardened Sandbox | Auto-Healer On]")
    print("   Type 'exit' or 'quit' to close.")
    print("========================================")
    print(MODEL_SAFETY_WARNING)
    print("========================================\n")

    chat_memory = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        user_prompt = input("👤 You: ").strip()
        if user_prompt.lower() in ("exit", "quit"):
            print("👋 Shutting down assistant. Goodbye!")
            break
        if not user_prompt:
            continue

        chat_memory.append({"role": "user", "content": user_prompt})

        retry_count = 0
        handled = False
        while not handled and retry_count <= MAX_RETRIES:
            ai_response = ask_model(client, DEFAULT_MODEL, chat_memory)
            chat_memory.append({"role": "assistant", "content": ai_response})

            blocks = extract_all_code(ai_response)
            if not blocks:
                print("\n🤖 Agent:")
                print(ai_response + "\n")
                handled = True
                break

            any_error = False
            for code, lang in ((b["code"], b["lang"]) for b in blocks):
                if not human_in_the_loop(code, lang):
                    print("\n🛑 Execution aborted by user.\n")
                    handled = True
                    break

                print("\n🐳 Executing in hardened sandbox...")
                result, meta = run_container(code, lang, WORKSPACE_DIR)
                # Redact before it ever hits terminal scrollback or re-enters the model.
                result = redact_secrets(result)
                print("\n--- 🐳 Sandbox Output ---")
                print(result if result else "[SUCCESS — no output]")
                print(f"[{meta['duration']}s · exit={meta['exit_code']}"
                      f"{' · TIMEOUT' if meta['timed_out'] else ''}]")
                print("-------------------------\n")

                # Feed the result back so the model has context either way.
                chat_memory.append({"role": "user", "content": f"RESULT:\n{result}"})

                if looks_like_error(result):
                    any_error = True

            if handled:
                break

            if any_error:
                retry_count += 1
                if retry_count <= MAX_RETRIES:
                    print(f"🔧 [Auto-Healer] Run failed — asking the model to fix it "
                          f"(attempt {retry_count}/{MAX_RETRIES})...")
                    chat_memory.append({
                        "role": "user",
                        "content": "Your last script failed (see the RESULT above). "
                                   "Return a single, fully corrected script only.",
                    })
                else:
                    print("❌ [Auto-Healer] Max retries reached. Could not fix the code.")
                    handled = True
            else:
                handled = True


if __name__ == "__main__":
    main()
