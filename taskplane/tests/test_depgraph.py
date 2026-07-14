import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph as dg  # noqa: E402


def w(ws, rel, content):
    p = os.path.join(ws, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(content)


class TestScan(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        w(self.ws, "src/db/conn.py", "import os\n")
        w(self.ws, "src/auth/session.py", "from src.db import conn\n")
        w(self.ws, "src/api/users.py",
          "from src.auth import session\nimport requests\n")
        w(self.ws, "web/app.ts",
          "import {x} from '../src/api/users'\nimport React from 'react'\n")
        w(self.ws, "docker-compose.yml",
          "services:\n  api:\n    image: x\n    depends_on:\n      - db\n"
          "  db:\n    image: postgres\n")

    def test_scan_builds_modules_and_edges(self):
        g = dg.scan(self.ws)
        self.assertIn("auth", g["modules"])
        self.assertIn("ext:requests", g["modules"])
        pairs = {(e["from"], e["to"]) for e in g["edges"]}
        self.assertIn(("auth", "db"), pairs)               # py import
        self.assertIn(("api", "auth"), pairs)
        self.assertIn(("web", "api"), pairs)               # ts relative import
        self.assertIn(("svc:api", "svc:db"), pairs)       # compose infra
        self.assertTrue(os.path.exists(dg._path(self.ws)))  # external store

    def test_incremental_uses_cache(self):
        dg.scan(self.ws)
        g1 = dg.load(self.ws)
        g2 = dg.scan(self.ws)   # nothing changed → same edges from cache
        self.assertEqual(g1["files"], g2["files"])

    def test_recorded_edge_survives_rescan(self):
        dg.scan(self.ws)
        dg.record_edge(self.ws, "src/api", "svc:db", kind="queries")
        g = dg.scan(self.ws)
        self.assertTrue(any(e.get("recorded") and e["kind"] == "queries"
                            for e in g["edges"]))


class TestImpact(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        w(self.ws, "src/db/conn.py", "x=1\n")
        w(self.ws, "src/auth/session.py", "from src.db import conn\n")
        w(self.ws, "src/api/users.py", "from src.auth import session\n")
        dg.scan(self.ws)

    def test_reverse_bfs_with_depth(self):
        imp = dg.impact(self.ws, ["src/db/conn.py"])
        self.assertEqual(imp["touched"], ["db"])
        d1 = [e["module"] for e in imp["impacted"][1]]
        d2 = [e["module"] for e in imp["impacted"].get(2, [])]
        self.assertIn("auth", d1)         # auth imports db
        self.assertIn("api", d2)          # api imports auth → depth 2
        self.assertIn("blast radius", dg.render_context(imp))

    def test_leaf_change_impacts_nothing(self):
        imp = dg.impact(self.ws, ["src/api/users.py"])
        self.assertEqual(imp["total_impacted"], 0)

    def test_html_written_with_highlighting(self):
        out = dg.to_html(self.ws, ["src/db/conn.py"])
        html = open(out).read()
        self.assertIn("db", html)
        self.assertIn("impacted", html)
        self.assertIn("<svg", html.lower())


class TestLoopImpactWiring(unittest.TestCase):
    def test_evaluate_action_carries_impact(self):
        import json
        import loop
        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "plan"))
        w(ws, "src/db/conn.py", "x=1\n")
        w(ws, "src/auth/session.py", "from src.db import conn\n")
        subprocess.run(["git", "init", "-q"], cwd=ws)
        subprocess.run(["git", "add", "-A"], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        dg.scan(ws)
        with open(os.path.join(ws, "plan", "tasks.json"), "w") as f:
            json.dump({"tasks": [{"id": "t1", "scope": ["src/db/**"],
                                  "tests": "true"}]}, f)
        loop.init(ws, "db work", spec_path="s", checkpoints=["plan"])
        loop.next_action(ws); loop.gate(ws, "pass"); loop.approve(ws)
        loop.next_action(ws)
        w(ws, "src/db/conn.py", "x=2\n")      # the "build"
        loop.gate(ws, "pass")
        act = loop.next_action(ws)             # evaluate
        self.assertEqual(act["step"], "evaluate")
        self.assertIsNotNone(act["impact"])
        d1 = [e["module"] for e in act["impact"]["impacted"][1]]
        self.assertIn("auth", d1)              # reviewer sees blast radius


if __name__ == "__main__":
    unittest.main()


class TestImpactExcludesLoopOwned(unittest.TestCase):
    def test_loop_paths_not_in_blast_radius(self):
        import json
        import loop
        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "plan"))
        w(ws, "src/db/conn.py", "x=1\n")
        subprocess.run(["git", "init", "-q"], cwd=ws)
        subprocess.run(["git", "add", "-A"], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        dg.scan(ws)
        with open(os.path.join(ws, "plan", "tasks.json"), "w") as f:
            json.dump({"tasks": [{"id": "t1", "scope": ["src/db/**"],
                                  "tests": "true"}]}, f)
        loop.init(ws, "g", spec_path="s", checkpoints=["plan"])
        loop.next_action(ws); loop.gate(ws, "pass"); loop.approve(ws)
        loop.next_action(ws)
        w(ws, "src/db/conn.py", "x=2\n")
        loop.gate(ws, "pass")
        act = loop.next_action(ws)
        touched = act["impact"]["touched"]
        self.assertEqual(touched, ["db"])       # no .taskplane/knowledge/plan


class TestCSharpJavaRuby(unittest.TestCase):
    """C#/Java/Ruby scanned with the same precision as Python: internal
    references resolve to modules, stdlib skipped, real deps become ext:."""

    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_csharp_namespace_resolution_and_csproj(self):
        w(self.ws, "Backend/Data/Repo.cs",
          "namespace Contoso.Data;\nusing System.Linq;\n"
          "public class Repo {}\n")
        w(self.ws, "Backend/Orders/OrdersService.cs",
          "using Contoso.Data;\nusing Newtonsoft.Json;\n"
          "using System;\nnamespace Contoso.Orders {\n"
          "  public class OrdersService {} }\n")
        w(self.ws, "Backend/Orders/Orders.csproj",
          '<Project><ItemGroup>'
          '<ProjectReference Include="..\\Data\\Data.csproj" />'
          '<PackageReference Include="Dapper" Version="2.0" />'
          '</ItemGroup></Project>')
        g = dg.scan(self.ws)
        pairs = {(e["from"], e["to"]) for e in g["edges"]}
        self.assertIn(("Backend/Orders", "Backend/Data"), pairs)  # using →
        self.assertIn(("Backend/Orders", "ext:Newtonsoft.Json"), pairs)
        self.assertIn(("Backend/Orders", "ext:Dapper"), pairs)    # csproj
        # System.* never becomes a node
        self.assertFalse(any("System" in m for m in g["modules"]))

    def test_java_package_resolution(self):
        w(self.ws, "src/main/java/com/shop/data/Db.java",
          "package com.shop.data;\npublic class Db {}\n")
        w(self.ws, "src/main/java/com/shop/api/Api.java",
          "package com.shop.api;\nimport com.shop.data.Db;\n"
          "import java.util.List;\n"
          "import org.springframework.web.bind.annotation.RestController;\n"
          "public class Api {}\n")
        g = dg.scan(self.ws)
        pairs = {(e["from"], e["to"]) for e in g["edges"]}
        self.assertIn(("main/java", "ext:org.springframework.web"), pairs)
        # both files in src/main → internal edge collapses to same module,
        # so resolution is proven by the ABSENCE of ext:com.shop
        self.assertFalse(any(t.startswith("ext:com.shop")
                             for _, t in pairs))
        self.assertFalse(any(t.startswith("ext:java") for _, t in pairs))

    def test_ruby_requires_and_gemfile(self):
        w(self.ws, "lib/billing/invoice.rb", "class Invoice; end\n")
        w(self.ws, "app/services/charger.rb",
          "require 'billing/invoice'\nrequire 'json'\nrequire 'stripe'\n"
          "require_relative '../models/order'\nclass Charger; end\n")
        w(self.ws, "app/models/order.rb", "class Order; end\n")
        w(self.ws, "Gemfile", "source 'https://rubygems.org'\n"
          "gem 'rails'\ngem 'sidekiq'\n")
        g = dg.scan(self.ws)
        pairs = {(e["from"], e["to"]) for e in g["edges"]}
        self.assertIn(("services", "billing"), pairs)      # require
        self.assertIn(("services", "models"), pairs)       # relative
        self.assertIn(("services", "ext:stripe"), pairs)
        self.assertIn(("(root)", "ext:rails"), pairs)           # Gemfile
        self.assertFalse(any(t == "ext:json" for _, t in pairs))  # stdlib

    def test_impact_crosses_csharp_layers(self):
        w(self.ws, "Backend/Data/Repo.cs",
          "namespace Contoso.Data;\nclass Repo {}\n")
        w(self.ws, "Backend/Orders/Svc.cs",
          "using Contoso.Data;\nnamespace Contoso.Orders;\nclass Svc {}\n")
        dg.scan(self.ws)
        imp = dg.impact(self.ws, ["Backend/Data/Repo.cs"])
        d1 = [e["module"] for e in imp["impacted"].get(1, [])]
        self.assertIn("Backend/Orders", d1)   # blast radius crosses layers
