"""Unit tests for the pure-logic helpers in agent_core / sessions (no Docker/LLM)."""
from unittest import mock

import agent_core
import sessions


def test_extract_all_code_multi():
    text = "here\n```python\nprint(1)\n```\nand\n```bash\nls -la\n```"
    blocks = agent_core.extract_all_code(text)
    assert len(blocks) == 2
    assert blocks[0] == {"code": "print(1)", "lang": "python"}
    assert blocks[1] == {"code": "ls -la", "lang": "bash"}


def test_extract_code_backcompat():
    code, lang = agent_core.extract_code("```python\nx=1\n```")
    assert code == "x=1" and lang == "python"
    assert agent_core.extract_code("no code here") == (None, None)


def test_sh_treated_as_bash():
    blocks = agent_core.extract_all_code("```sh\necho hi\n```")
    assert blocks[0]["lang"] == "bash"


def test_looks_like_error():
    assert agent_core.looks_like_error("RUNTIME_ERROR: boom")
    assert agent_core.looks_like_error("Traceback (most recent call last):")
    assert agent_core.looks_like_error("[exit code 1]\nboom")
    assert not agent_core.looks_like_error("all good")
    assert not agent_core.looks_like_error("")


def test_redact_secrets():
    out = agent_core.redact_secrets("token=abc123 AKIAABCD1234EFGH5678 sk-" + "a" * 30)
    assert "abc123" not in out
    assert "[REDACTED]" in out
    assert "[REDACTED_AWS_KEY]" in out
    assert "[REDACTED_OPENAI_KEY]" in out


def test_report_and_transcript():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "scan it"},
        {"role": "assistant", "content": "```python\nprint('scan')\n```"},
        {"role": "user", "content": "RESULT:\nopen: 80,443"},
    ]
    report = sessions.build_report(msgs, target="10.0.0.1")
    assert "10.0.0.1" in report and "Step 1" in report and "open: 80,443" in report
    tx = sessions.export_transcript(msgs)
    assert "Operator" in tx and "Agent" in tx and "sys" not in tx


def test_report_findings_ranked():
    findings = [
        {"severity": "Low", "title": "verbose banner", "target": "a", "notes": ""},
        {"severity": "Critical", "title": "RCE", "target": "b", "notes": "bad|pipe"},
    ]
    report = sessions.build_report([], findings=findings)
    # Critical must be listed before Low, and pipes escaped in the table.
    assert report.index("RCE") < report.index("verbose banner")
    assert "bad\\|pipe" in report


def test_trim_history_keeps_system_and_tail():
    msgs = [{"role": "system", "content": "S"}]
    msgs += [{"role": "user", "content": str(i)} for i in range(100)]
    out = agent_core.trim_history(msgs, max_msgs=10)
    assert len(out) == 10
    assert out[0]["role"] == "system"
    assert out[-1]["content"] == "99"  # newest kept


def test_run_container_no_docker(monkeypatch=None):
    with mock.patch.object(agent_core, "get_docker_client", return_value=None):
        out, meta = agent_core.run_container("print(1)", "python", ".")
    assert "RUNTIME_ERROR" in out and meta["ok"] is False


def test_run_container_success_mocked():
    fake_container = mock.Mock()
    fake_container.wait.return_value = {"StatusCode": 0}
    fake_container.logs.return_value = b"hello\n"
    fake_cli = mock.Mock()
    fake_cli.containers.run.return_value = fake_container
    with mock.patch.object(agent_core, "get_docker_client", return_value=fake_cli):
        out, meta = agent_core.run_container("print('hi')", "python", ".")
    assert "hello" in out and meta["ok"] and meta["exit_code"] == 0
    fake_container.remove.assert_called_once()  # cleanup happened


def test_run_container_stream_mocked():
    fake_container = mock.Mock()
    fake_container.status = "exited"
    fake_container.logs.return_value = iter([b"line one\n", b"line two\n"])
    fake_container.wait.return_value = {"StatusCode": 0}
    fake_cli = mock.Mock()
    fake_cli.containers.run.return_value = fake_container
    meta = {}
    with mock.patch.object(agent_core, "get_docker_client", return_value=fake_cli):
        chunks = list(agent_core.run_container_stream("print('x')", "python", ".", meta))
    text = "".join(chunks)
    assert "line one" in text and "line two" in text
    assert meta["ok"] and meta["exit_code"] == 0 and not meta["timed_out"]
    fake_container.remove.assert_called_once()


def test_run_container_stream_redacts():
    fake_container = mock.Mock()
    fake_container.status = "exited"
    fake_container.logs.return_value = iter([b"api_key=SECRET123\n"])
    fake_container.wait.return_value = {"StatusCode": 0}
    fake_cli = mock.Mock()
    fake_cli.containers.run.return_value = fake_container
    meta = {}
    with mock.patch.object(agent_core, "get_docker_client", return_value=fake_cli):
        text = "".join(agent_core.run_container_stream("x", "python", ".", meta, redact=True))
    assert "SECRET123" not in text and "[REDACTED]" in text


def test_run_container_timeout_mocked():
    fake_container = mock.Mock()
    fake_container.wait.side_effect = Exception("timeout")
    fake_container.logs.return_value = b"partial"
    fake_cli = mock.Mock()
    fake_cli.containers.run.return_value = fake_container
    with mock.patch.object(agent_core, "get_docker_client", return_value=fake_cli):
        out, meta = agent_core.run_container("while True: pass", "python", ".")
    assert meta["timed_out"] and "exceeded" in out
    fake_container.kill.assert_called_once()
    fake_container.remove.assert_called_once()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} tests passed")
