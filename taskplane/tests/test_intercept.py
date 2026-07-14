

class TestRuntimeOwnedExcludedFromDoD(__import__("unittest").TestCase):
    def test_kb_and_plan_writes_do_not_fail_scope_diff(self):
        import os, subprocess, tempfile
        import taskplane_lite as tpl
        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "src"))
        with open(os.path.join(ws, "src", "a.py"), "w") as f:
            f.write("x=1\n")
        subprocess.run(["git", "init", "-q"], cwd=ws)
        subprocess.run(["git", "add", "-A"], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        head = tpl.git_head(ws)
        # runtime writes during the task: KB record + plan — NOT task changes
        os.makedirs(os.path.join(ws, "knowledge"))
        os.makedirs(os.path.join(ws, "plan"))
        open(os.path.join(ws, "knowledge", "index.json"), "w").write("{}")
        open(os.path.join(ws, "plan", "tasks.json"), "w").write("{}")
        with open(os.path.join(ws, "src", "a.py"), "w") as f:
            f.write("x=2\n")   # the actual in-scope change
        c = tpl.build_contract("t", scope=["src/**"])
        errors = tpl.dod_check(c, ws, head)
        self.assertEqual(errors, [])   # only src/a.py counts, and it's in scope


class TestRuntimeDirSelfIgnored(__import__("unittest").TestCase):
    def test_git_add_A_never_commits_taskplane(self):
        import os, subprocess, tempfile
        import taskplane_lite as tpl
        ws = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=ws)
        tpl.trace(ws, "x")             # creates .taskplane + trace
        tpl.activate(ws, tpl.build_contract("t", scope=["src/**"]),
                     snapshot=None)
        subprocess.run(["git", "add", "-A"], cwd=ws)
        out = subprocess.run(["git", "status", "--porcelain"], cwd=ws,
                             capture_output=True, text=True).stdout
        self.assertNotIn(".taskplane", out)   # runtime never staged
