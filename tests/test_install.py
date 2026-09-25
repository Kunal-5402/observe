import json

from observe import install, paths


def test_install_is_idempotent_and_uninstall_keeps_other_hooks():
    settings = paths.claude_settings()
    settings.parent.mkdir(parents=True)
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}
    settings.write_text(json.dumps({"model": "opus", "hooks": {"PreToolUse": [other]}}))

    install.install("claude")
    _, backup = install.install("claude")
    assert backup is not None and backup.exists()

    config = json.loads(settings.read_text())
    assert config["model"] == "opus"
    pre = config["hooks"]["PreToolUse"]
    assert pre[0] == other
    assert len(pre) == 2 and pre[1]["matcher"] == "*"
    assert "-m observe claude hook" in pre[1]["hooks"][0]["command"]
    assert set(install.installed_events("claude")) == set(install.EVENTS["claude"])

    _, removed = install.uninstall("claude")
    assert removed == len(install.EVENTS["claude"])
    assert json.loads(settings.read_text()) == {"model": "opus", "hooks": {"PreToolUse": [other]}}


def test_codex_install_creates_hooks_json():
    path, backup = install.install("codex")
    assert path == paths.codex_hooks() and backup is None
    hooks = json.loads(path.read_text())["hooks"]
    assert set(hooks) == set(install.EVENTS["codex"])
    assert hooks["SessionEnd"][0]["hooks"][0]["timeout"] == 3
    install.uninstall("codex")
    assert json.loads(path.read_text()) == {}
