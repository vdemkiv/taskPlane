"""Focused onboarding journeys; temporary projects keep real user state intact."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "taskplane"))
from taskplane import settings
import dashboard
import storage

spec = importlib.util.spec_from_file_location("onboarding_cli", ROOT / "taskplane/tp.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.fixture
def project(tmp_path, monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("TASKPLANE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    (tmp_path / "README.md").write_text("Project")
    subprocess.run(["git", "add", "README.md"], check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
                    "commit", "-qm", "initial"], check=True)
    return tmp_path


def submission(project, **extra):
    return {"schema": "taskplane.onboarding-setup/v1", "workspace": str(project), **extra}


def decode(value, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(value)))
    return cli._read_onboarding_setup("-", value["workspace"])


def report(project):
    return {"workspace": str(project), "host": "codex", "has_context": True,
            "checks": [], "ready": True, "next_action": "ready",
            "configuration": cli._onboarding_configuration(str(project)),
            "settings": cli._onboarding_settings(str(project)),
            "codex_hooks": {"launcher_ready": True}}


def test_settings_save_reopen(project, monkeypatch):
    before = cli._onboarding_settings(str(project))
    value = submission(project, settings={"expected_digest": before["digest"],
        "stages": {"build": {"model": "gpt-5.5", "reasoning": "high"},
                   "retro": {"model": "inherit", "reasoning": "medium"}}})
    result = cli._apply_onboarding_setup(str(project), decode(value, monkeypatch))
    assert result["status"] == "applied"
    reopened = cli._onboarding_settings(str(project))
    assert reopened["stages"]["build"]["model"] == "gpt-5.5"
    assert reopened["stages"]["retro"]["reasoning"] == "medium"
    assert reopened["stages"]["plan"] == before["stages"]["plan"]
    assert settings.load_settings(workspace=project).stages["build"].model == "gpt-5.5"
    html = dashboard.render_onboarding(report(project))
    assert 'value="gpt-5.5"' in html
    assert 'Saved' not in dashboard.render_onboarding({**report(project),
        "setup_result": {"status": "refused", "error": "write failed"}})


def test_dashboard_defaults_and_advanced(project):
    html = dashboard.render_onboarding(report(project))
    assert 'name="common_model"' in html
    assert 'name="common_reasoning"' in html
    assert '<summary>Advanced' in html
    advanced = html[html.index('<summary>Advanced'):]
    assert 'data-stage="build"' in advanced
    assert '<details class="tp-sec" open' not in html
    assert 'Effective settings (read-only)' not in html
    assert 'var(--surface-2)' in html and 'var(--font-mono)' in html
    assert 'data-settings-digest=' in html


def test_save_error_and_retry(project, monkeypatch, capsys):
    before = cli._onboarding_settings(str(project))
    value = submission(project, settings={"expected_digest": before["digest"],
        "stages": {"build": {"model": "gpt-5.5", "reasoning": "invalid"}}})
    args = argparse.Namespace(workspace=str(project), json=True, out=None,
                             setup_values=value)
    assert cli.cmd_onboard(args) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed["setup_result"]["status"] == "refused"
    html = dashboard.render_onboarding(failed)
    assert 'role="alert"' in html and 'Retry save' in html
    assert 'value="gpt-5.5"' in html
    assert cli._onboarding_settings(str(project))["digest"] == before["digest"]
    value["settings"]["stages"]["build"]["reasoning"] = "medium"
    result = cli._apply_onboarding_setup(str(project), decode(value, monkeypatch))
    assert result["status"] == "applied"


def test_plugin_hooks_once(project, monkeypatch):
    config = project / ".codex" / "hooks.json"
    config.parent.mkdir()
    owned = {"type": "command", "command": "python3 .taskplane/codex-hook.py screen"}
    other = {"type": "command", "command": "echo keep-my-hook"}
    original = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [owned, other]}]},
                "other_setting": True}
    config.write_text(json.dumps(original))
    monkeypatch.setattr(cli, "_install_context", lambda: "personal")
    for _ in range(2):
        result = cli._install_codex_hooks(str(project))
        assert result["registration"] == "plugin"
        assert result["duplicate_project_hooks"] == 0
    saved = json.loads(config.read_text())
    assert saved["hooks"]["PreToolUse"][0]["hooks"] == [other]
    assert saved["other_setting"] is True
    assert (project / ".taskplane" / "codex-hook.py").is_file()


def test_project_execution_and_private_storage(project, monkeypatch):
    selected = storage.select_project_execution_storage(str(project), environment={})
    assert selected["home"] == str(project / ".taskplane")
    assert storage.taskplane_home(workspace=str(project)) == str(project / ".taskplane")
    cli._apply_onboarding_setup(str(project), submission(project, initialize=True,
        knowledge_plan="personal"))
    private = Path(cli.tp.kb_root(str(project)))
    assert private.is_relative_to(project / ".taskplane")
    assert subprocess.run(["git", "check-ignore", str(private)],
                          capture_output=True).returncode == 0
    other = project / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="different workspace"):
        cli._read_onboarding_setup_value(submission(other), str(project))
    (project / ".taskplane" / "retained-evidence.txt").write_text("keep")
    storage.select_project_execution_storage(str(project), environment={})
    assert (project / ".taskplane" / "retained-evidence.txt").read_text() == "keep"


def test_existing_budget_approval(project, capsys):
    contract = cli.tp.build_contract("temporary budget journey", scope=["README.md"], max_actions=2)
    active = cli.tp.activate(str(project), contract)
    assert cli.tp.budget_status(active, used_actions=2)[0] is False
    html = dashboard._widget_gatebar(str(project), {"step": "execute"}, "execute", [], True, 2, 2)
    assert "approve 25 more" in html and "2/2" in html
    assert cli.tp.load_active(str(project))["budget"]["max_actions"] == 2
    args = argparse.Namespace(workspace=str(project), grant=25, spent=None, approved_by="test-human")
    assert cli.cmd_budget(args) == 0
    updated = cli.tp.load_active(str(project))
    assert updated["budget"]["max_actions"] == 27
    assert cli.tp.budget_status(updated, used_actions=2)[0] is True
    assert "+25 actions" in capsys.readouterr().out


def test_run_only_advisory_exception(project, monkeypatch):
    from taskplane import run_context
    # The view consumes the incumbent run-scoped policy owner, never a setup setting.
    observed = []
    def advisory(workspace):
        observed.append(workspace)
        return workspace == str(project)
    monkeypatch.setattr(run_context, "resource_limits_advisory", advisory)
    html = dashboard._widget_gatebar(str(project), {}, "execute", [], False, 0, 60)
    assert "Ignore limits for this run only — advisory" in html
    assert "Future runs" in html
    fresh = dashboard._widget_gatebar(str(project / "new-run"), {}, "execute", [], True, 60, 60)
    assert "Ignore limits for this run only" not in fresh
    assert "approve 25 more" in fresh
    assert observed == [str(project), str(project / "new-run")]
    with pytest.raises(ValueError):
        cli._read_onboarding_setup_value(submission(project, resource_policy="advisory"), str(project))


def test_delivery_provenance(project):
    # A setup projection cannot promote saved draft content into phase approval.
    (project / "plan").mkdir()
    (project / "plan" / "draft.md").write_text("unapproved plan")
    before = (project / "plan" / "draft.md").read_bytes()
    result = cli._onboard_report(str(project))
    assert result["artifacts"] is None
    assert result["ready"] is False
    assert (project / "plan" / "draft.md").read_bytes() == before
    html = dashboard.render_onboarding(result)
    assert "do not approve or start any governed stage" in html
    # Completed delivery phases are established by engine evidence, not this test.
