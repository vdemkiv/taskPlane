"""Focused adversarial proofs for H1-C durable state and read-only safety."""

from __future__ import annotations

import json
import os
import shlex
import shutil
from pathlib import Path
from unittest import mock

from taskplane import taskplane_lite as tp


def test_h14_critical_write_fsyncs_before_acknowledgement(tmp_path: Path):
    target = tmp_path / "loop.json"
    target.write_text('{"generation": 1}\n', encoding="utf-8")
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def recording_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def recording_replace(source: str, destination: str) -> None:
        events.append("replace")
        real_replace(source, destination)

    with mock.patch.object(tp.os, "fsync", side_effect=recording_fsync), \
            mock.patch.object(tp.os, "replace", side_effect=recording_replace):
        tp.atomic_write_json(str(target), {"generation": 2})

    assert events == ["fsync", "replace", "fsync"]
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 2}

    replace = mock.Mock()
    with mock.patch.object(tp.os, "fsync", side_effect=OSError("disk lost")), \
            mock.patch.object(tp.os, "replace", replace):
        try:
            tp.atomic_write_json(str(target), {"generation": 3})
        except OSError as exc:
            assert "disk lost" in str(exc)
        else:  # pragma: no cover - the durability boundary must fail closed
            raise AssertionError("write acknowledged before the data fsync")
    replace.assert_not_called()
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 2}

    fsync_calls = 0

    def fail_parent_fsync(fd: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("directory metadata lost")
        real_fsync(fd)

    with mock.patch.object(tp.os, "fsync", side_effect=fail_parent_fsync):
        try:
            tp.atomic_write_json(str(target), {"generation": 3})
        except OSError as exc:
            assert "metadata lost" in str(exc)
        else:  # pragma: no cover - replacement is not durable without this
            raise AssertionError("write acknowledged before directory fsync")
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 3}


def test_h15_interrupted_migration_keeps_legacy_authoritative(tmp_path: Path,
                                                              monkeypatch):
    workspace = tmp_path / "repo"
    legacy = workspace / "knowledge"
    legacy.mkdir(parents=True)
    (legacy / "decisions.json").write_text("complete", encoding="utf-8")
    (legacy / "history.json").write_text("history", encoding="utf-8")
    home = tmp_path / "home"
    monkeypatch.setenv("TASKPLANE_HOME", str(home))
    monkeypatch.setenv("TASKPLANE_STORE", "external")
    real_copytree = shutil.copytree

    def interrupted_copy(source: str, destination: str, **kwargs):
        Path(destination).mkdir(parents=True)
        shutil.copy2(Path(source) / "decisions.json",
                     Path(destination) / "decisions.json")
        raise OSError("cross-device copy interrupted")

    with mock.patch("shutil.copytree", side_effect=interrupted_copy):
        try:
            tp.migrate_store(str(workspace))
        except OSError as exc:
            assert "interrupted" in str(exc)
        else:  # pragma: no cover - partial migration must not be acknowledged
            raise AssertionError("partial migration was acknowledged")

    external = Path(tp.external_store_root(str(workspace))) / "knowledge"
    assert not external.exists()
    assert tp.kb_root(str(workspace)) == str(legacy)
    assert (legacy / "history.json").read_text(encoding="utf-8") == "history"

    # A partial final directory left by an older cross-filesystem move is not
    # authoritative. Retry quarantines it and publishes one verified tree.
    external.mkdir(parents=True)
    (external / "decisions.json").write_text("partial", encoding="utf-8")
    assert tp.kb_root(str(workspace)) == str(legacy)

    with mock.patch("shutil.copytree", wraps=real_copytree):
        result = tp.migrate_store(str(workspace))
    assert result["moved"] is True
    assert not legacy.exists()
    assert tp.kb_root(str(workspace)) == str(external)
    assert (external / "history.json").read_text(encoding="utf-8") == "history"
    assert list(external.parent.glob("knowledge.partial.*"))


def test_h30_readonly_contract_refuses_opaque_mutating_launchers(tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    launchers = (
        "make test",
        "pytest -q",
        "tox -e py",
        "npm test",
        "./scripts/repository-check --review",
    )
    for command in launchers:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason
        assert "can't be screened" in reason

    for command in (
            "rg -n TODO src",
            "git --no-pager --no-optional-locks --no-lazy-fetch "
            "--no-replace-objects "
            "-c core.fsmonitor=false diff "
            "--no-ext-diff --no-textconv --cached --stat",
            "cat README.md"):
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is True, (command, reason)


def test_h30_readonly_contract_rejects_git_alias_extension_surfaces(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text(
        "[alias]\n\tpwn = !touch reviewed-source\n", encoding="utf-8")
    configured_alias = (
        f"GIT_CONFIG_GLOBAL={shlex.quote(str(global_config))} "
        "git --no-pager --no-optional-locks pwn")

    commands = (
        "git --no-pager --no-optional-locks --no-lazy-fetch "
        "--no-replace-objects "
        "-c alias.pwn='!touch reviewed-source' pwn",
        "ALIAS_VALUE='!touch reviewed-source' git --no-pager "
        "--no-optional-locks --config-env=alias.pwn=ALIAS_VALUE pwn",
        configured_alias,
    )
    for command in commands:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason
        assert "can't be screened" in reason


def test_h30_readonly_git_diff_disables_external_and_textconv_helpers(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text(
        "[diff]\n\texternal = touch reviewed-source\n"
        "[diff \"danger\"]\n\ttextconv = touch reviewed-source\n",
        encoding="utf-8")
    prefix = f"GIT_CONFIG_GLOBAL={shlex.quote(str(global_config))} "

    unsafe = (
        prefix + "git --no-pager --no-optional-locks diff --stat",
        "git --no-pager --no-optional-locks diff --ext-diff --stat",
        "git --no-pager --no-optional-locks diff --textconv --stat",
        "git --no-pager --no-optional-locks "
        "-c diff.external='touch reviewed-source' diff --stat",
        "git --no-pager --no-optional-locks "
        "-c diff.danger.textconv='touch reviewed-source' diff --stat",
        "git --no-pager --no-optional-locks --no-lazy-fetch "
        "--no-replace-objects -c core.fsmonitor=false "
        "diff --no-ext-diff --no-textconv --ext --stat",
    )
    for command in unsafe:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason
        assert "can't be screened" in reason

    safe = (
        "git --no-pager --no-optional-locks --no-lazy-fetch "
        "--no-replace-objects "
        "-c core.fsmonitor=false diff "
        "--no-ext-diff --no-textconv --cached --stat")
    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": safe}, str(tmp_path))
    assert allowed is True, reason


def test_h30_readonly_contract_rejects_environment_assignment_prefixes(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    commands = (
        # Git's trace families accept filesystem targets and may write even
        # when the selected Git operation is otherwise a guarded index diff.
        "GIT_TRACE=/tmp/git.trace git --no-pager --no-optional-locks "
        "-c core.fsmonitor=false diff --no-ext-diff --no-textconv "
        "--cached --stat",
        "GIT_TRACE2_EVENT=/tmp/git-trace2.json git --no-pager "
        "--no-optional-locks -c core.fsmonitor=false diff "
        "--no-ext-diff --no-textconv --cached --stat",
        "GIT_TRACE_PERFORMANCE=/tmp/git-performance.trace env "
        "GIT_PAGER=cat git --no-pager --no-optional-locks "
        "-c core.fsmonitor=false diff --no-ext-diff --no-textconv "
        "--cached --stat",
        "env GIT_EXTERNAL_DIFF=/tmp/helper git --no-pager "
        "--no-optional-locks -c core.fsmonitor=false diff "
        "--no-ext-diff --no-textconv --cached --stat",
        "GIT_EDITOR=/tmp/editor git --no-pager --no-optional-locks "
        "-c core.fsmonitor=false diff --no-ext-diff --no-textconv "
        "--cached --stat",
        "env GIT_TRACE2=/tmp/trace -S 'git --no-pager "
        "--no-optional-locks -c core.fsmonitor=false diff "
        "--no-ext-diff --no-textconv --cached --stat'",
        # Non-Git execution surfaces must fail by the same generic boundary;
        # a dynamic-loader prefix can execute code before a nominally safe
        # reader starts.
        "LD_PRELOAD=/tmp/injected.so cat README.md",
        "PYTHONPATH=/tmp/injected python3 --version",
    )

    for command in commands:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason
        assert ("environment assignment" in reason
                or "direct-command read-only grammar" in reason)
        assert "can't be screened" in reason


def test_h30_build_contract_keeps_environment_assignment_compatibility(
        tmp_path: Path):
    contract = tp.build_contract("builder")

    for command in (
            "FEATURE_FLAG=1 cat README.md",
            "./cat README.md",
            'echo "$((1 + 1))"'):
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is True, (command, reason)


def test_h30_readonly_git_diff_rejects_worktree_clean_filter_surface(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    command = (
        "git --no-pager --no-optional-locks --no-lazy-fetch "
        "--no-replace-objects -c core.fsmonitor=false "
        "diff --no-ext-diff --no-textconv --stat -- payload.bin")

    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": command}, str(tmp_path))

    assert allowed is False
    assert "read-only review contract" in reason
    assert ".gitattributes" in reason
    assert "clean filters" in reason


def test_h30_readonly_git_rejects_abbreviated_filter_options(tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    commands = (
        "git --no-pager --no-optional-locks "
        "cat-file --textcon HEAD:payload.bin",
        "git --no-pager --no-optional-locks "
        "cat-file --filt HEAD:payload.bin",
    )

    for command in commands:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason
        assert "Git" in reason
        assert "can't be screened" in reason


def test_h30_readonly_git_rejects_fsmonitor_capable_index_builtin(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text(
        "[core]\n\tfsmonitor = /tmp/untrusted-fsmonitor\n",
        encoding="utf-8")
    command = (
        f"GIT_CONFIG_GLOBAL={shlex.quote(str(global_config))} "
        "git --no-pager --no-optional-locks ls-files")

    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": command}, str(tmp_path))
    assert allowed is False
    assert "read-only review contract" in reason
    assert "can't be screened" in reason


def test_h30_readonly_git_rejects_pretty_config_signature_indirection(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text(
        "[pretty]\n\tdanger = format:%G? %s\n",
        encoding="utf-8")
    prefix = f"GIT_CONFIG_GLOBAL={shlex.quote(str(global_config))} "
    commands = (
        prefix + "git --no-pager --no-optional-locks show "
        "--no-ext-diff --no-textconv --pretty=danger HEAD",
        prefix + "git --no-pager --no-optional-locks log "
        "--no-ext-diff --no-textconv --pretty=danger -1",
    )

    for command in commands:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason
        assert "can't be screened" in reason


def test_h30_readonly_git_keeps_one_explicitly_guarded_diff_form(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    command = (
        "git --no-pager --no-optional-locks --no-lazy-fetch "
        "--no-replace-objects -c core.fsmonitor=false "
        "diff --no-ext-diff --no-textconv --cached --stat -- src")

    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": command}, str(tmp_path))
    assert allowed is True, reason


def test_h30_readonly_rejects_path_qualified_reader_identity(tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    guarded_git = (
        "--no-pager --no-optional-locks -c core.fsmonitor=false diff "
        "--no-ext-diff --no-textconv --cached --stat")

    for command in ("./cat README.md", f"./git {guarded_git}",
                    "/bin/cat README.md", "./env cat README.md",
                    "./command cat README.md"):
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "path-qualified executable" in reason
        assert "trusted read-only program" in reason

    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "./if cat README.md"}, str(tmp_path))
    assert allowed is False
    assert "can't be screened" in reason


def test_h30_readonly_rejects_repository_path_shadow_and_symlink(
        tmp_path: Path, monkeypatch):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    for program in ("cat", "env", "git"):
        executable = shadow / program
        executable.write_text(
            "#!/bin/sh\ntouch reviewed-source\n", encoding="utf-8")
        executable.chmod(0o755)
    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{shadow}{os.pathsep}{original_path}")
    guarded_git = (
        "git --no-pager --no-optional-locks --no-lazy-fetch "
        "--no-replace-objects -c core.fsmonitor=false diff "
        "--no-ext-diff --no-textconv --cached --stat")

    for command in ("cat README.md", guarded_git):
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "PATH candidate" in reason
        assert "repository-controlled" in reason

    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "env cat README.md"}, str(tmp_path))
    assert allowed is False
    assert "direct-command read-only grammar" in reason

    # A repository-owned symlink does not become trusted merely because its
    # target is a genuine system reader.
    shadow_cat = shadow / "cat"
    shadow_cat.unlink()
    shadow_cat.symlink_to("/bin/cat")
    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "cat README.md"}, str(tmp_path))
    assert allowed is False
    assert "PATH candidate" in reason
    assert "repository-controlled" in reason


def test_h30_readonly_balances_nested_shell_substitutions(tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    unsafe = (
        'echo $(python3 -c "open(\'reviewed-source\', \'w\')")',
        "echo \"$(printf x; (touch reviewed-source))\"",
        'echo `python3 -c "open(\'reviewed-source\', \'w\')"`',
        'cat <(python3 -c "open(\'reviewed-source\', \'w\')")',
        'echo "$(printf \'%s\' "$(python3 -c '
        '"open(\'reviewed-source\', \'w\')")")"',
        'echo "$((1 + $(python3 -c '
        '"open(\'reviewed-source\', \'w\')")))"',
        "echo \"$(printf x\"",
        "echo `printf x",
    )

    for command in unsafe:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason


def test_h30_readonly_substitution_grammar_allows_only_literal_text(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "echo '$(touch reviewed-source)'"},
        str(tmp_path))
    assert allowed is True, reason

    refused = (
        r"echo \$(touch reviewed-source)",
        "echo \"$(printf '%s' 'a(b)c')\"",
        "echo `printf '%s' 'a(b)c'`",
        "cat <(printf '%s' 'a(b)c')",
    )

    for command in refused:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "admitted read-only grammar" in reason


def test_h30_readonly_binds_known_writer_identity_and_spelling(
        tmp_path: Path, monkeypatch):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    (tmp_path / ".em-review").mkdir()
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    shadow_touch = shadow / "touch"
    shadow_touch.write_text(
        "#!/bin/sh\ntouch reviewed-source\n", encoding="utf-8")
    shadow_touch.chmod(0o755)
    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{shadow}{os.pathsep}{original_path}")

    for command in (
            "./touch .em-review/receipt",
            "touch .em-review/receipt",
            "'touch' .em-review/receipt",
            r"tou\ch .em-review/receipt"):
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason

    # A repository symlink to the real tool is still a mutable PATH alias,
    # not the startup executable identity.
    trusted_touch = shutil.which("touch", path=original_path)
    assert trusted_touch
    shadow_touch.unlink()
    shadow_touch.symlink_to(trusted_touch)
    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "touch .em-review/receipt"},
        str(tmp_path))
    assert allowed is False
    assert "PATH candidate" in reason
    assert "repository-controlled" in reason


def test_h30_readonly_tp_cli_exemption_is_exact_canonical_identity(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    evil = tmp_path / "evil" / "taskplane" / "tp.py"
    evil.parent.mkdir(parents=True)
    evil.write_text("print('lookalike')\n", encoding="utf-8")

    allowed, reason = tp.screen_tool(
        contract, "Bash",
        {"command": "python3 evil/taskplane/tp.py findings list"},
        str(tmp_path))
    assert allowed is False
    assert "script|module|stdin" in reason or "canonical" in reason

    evil.unlink()
    evil.symlink_to(tp._TP_CLI_PATH)
    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": str(evil) + " --version"},
        str(tmp_path))
    assert allowed is False
    assert "path-qualified executable" in reason

    allowed, reason = tp.screen_tool(
        contract, "Bash",
        {"command": f"{tp._TP_CLI_PATH} --version"},
        str(tmp_path))
    assert allowed is True, reason


def test_h30_readonly_refuses_eval_and_quoted_or_escaped_keywords(
        tmp_path: Path, monkeypatch):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    shadow_eval = shadow / "eval"
    shadow_eval.write_text(
        "#!/bin/sh\ntouch reviewed-source\n", encoding="utf-8")
    shadow_eval.chmod(0o755)
    monkeypatch.setenv(
        "PATH", f"{shadow}{os.pathsep}{os.environ.get('PATH', '')}")

    commands = (
        "eval cat README.md",
        "./eval cat README.md",
        "env eval cat README.md",
        "'if' cat README.md",
        r"\if cat README.md",
        '"time" cat README.md',
        r"\then cat README.md",
    )
    for command in commands:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason


def test_h30_readonly_refuses_nested_legacy_backticks(tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    commands = (
        "echo `printf '%s' \\`touch reviewed-source\\``",
        "echo \"`printf '%s' \\`touch reviewed-source\\``\"",
    )
    for command in commands:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "legacy backtick" in reason


def test_h30_readonly_refuses_writer_modes_hidden_behind_reader_names(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    commands = (
        "xxd -r input.hex .em-review/output.bin",
        "diff --output=.em-review/delta left right",
        "rg --pre helper needle src",
        "rg --hostname-bin helper needle src",
        "file --compile .em-review/magic",
        "tar -cf .em-review/source.tar src",
        "find . -fprint .em-review/files",
        "wget https://example.invalid/payload",
        "mv .em-review/source .em-review/destination",
        "rsync --remove-source-files .em-review/source .em-review/destination",
    )
    for command in commands:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason


def test_h30_readonly_refuses_shell_expansion_and_redirection_grammar(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    commands = (
        "cat README.md > .em-review/copy",
        "cat README.md 3>.em-review/copy",
        "cat <<EOF\nREADME.md\nEOF",
        "cat $INPUT",
        'cat "$INPUT"',
        "cat *.py",
        "cat file?.py",
        "cat {README,LICENSE}.md",
        "cat ~/README.md",
    )
    for command in commands:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "admitted read-only grammar" in reason


def test_h30_readonly_git_requires_closed_repository_and_environment(
        tmp_path: Path, monkeypatch):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    guarded_tail = (
        "-c core.fsmonitor=false diff --no-ext-diff --no-textconv "
        "--cached --stat")
    commands = (
        f"git --no-pager --no-optional-locks --no-replace-objects {guarded_tail}",
        f"git --no-pager --no-optional-locks --no-lazy-fetch {guarded_tail}",
        "git --no-pager --no-optional-locks --no-lazy-fetch "
        f"--no-replace-objects -C .. {guarded_tail}",
    )
    for command in commands:
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "Git" in reason

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "attacker-config"))
    command = (
        "git --no-pager --no-optional-locks --no-lazy-fetch "
        f"--no-replace-objects {guarded_tail}")
    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": command}, str(tmp_path))
    assert allowed is False
    assert "not sanitized" in reason


def test_h30_readonly_tp_cli_rejects_mutating_verb_and_python_launcher(
        tmp_path: Path):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])
    for command in (
            f"{tp._TP_CLI_PATH} clear",
            f"{tp._TP_CLI_PATH} req new title",
            f"{tp._TP_CLI_PATH} dashboard --out reviewed-source",
            f"python3 {tp._TP_CLI_PATH} --version"):
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is False, command
        assert "read-only review contract" in reason


def test_h30_build_keeps_launcher_opacity_and_literal_write_scope(
        tmp_path: Path):
    contract = tp.build_contract("builder", scope=["src/**"])
    for command in (
            "python3 -c 'print(1)'",
            "env cat README.md",
            "rg --pre helper needle src"):
        allowed, reason = tp.screen_tool(
            contract, "Bash", {"command": command}, str(tmp_path))
        assert allowed is True, (command, reason)

    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "cp src/input src/output"},
        str(tmp_path))
    assert allowed is True, reason

    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "cp src/input ../outside"},
        str(tmp_path))
    assert allowed is False
    assert "escapes the workspace" in reason


def test_h30_readonly_refuses_function_alias_and_external_path_precedence(
        tmp_path: Path, monkeypatch):
    contract = tp.build_contract(
        "review", read_only=True, write_allow=[".em-review/**"])

    monkeypatch.setenv("BASH_FUNC_cat%%", "() { touch reviewed-source; }")
    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "cat README.md"}, str(tmp_path))
    assert allowed is False
    assert "function precedence" in reason
    monkeypatch.delenv("BASH_FUNC_cat%%")

    monkeypatch.setenv("SHELLOPTS", "braceexpand:expand_aliases")
    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "cat README.md"}, str(tmp_path))
    assert allowed is False
    assert "alias precedence" in reason
    monkeypatch.delenv("SHELLOPTS")

    shadow = tmp_path.parent / "outside-review-path"
    shadow.mkdir(exist_ok=True)
    shadow_cat = shadow / "cat"
    shadow_cat.write_text(
        "#!/bin/sh\ntouch reviewed-source\n", encoding="utf-8")
    shadow_cat.chmod(0o755)
    monkeypatch.setenv(
        "PATH", f"{shadow}{os.pathsep}{os.environ.get('PATH', '')}")
    allowed, reason = tp.screen_tool(
        contract, "Bash", {"command": "cat README.md"}, str(tmp_path))
    assert allowed is False
    assert "trusted startup identity" in reason
