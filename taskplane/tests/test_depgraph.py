import json
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
    with open(p, "w", encoding="utf-8") as f:
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
        html = open(out, encoding="utf-8").read()
        self.assertIn("db", html)
        self.assertIn("impacted", html)
        self.assertIn("<svg", html.lower())


def _coverage_graph(files, edges=()):
    modules = {}
    for rel in files:
        module = dg.module_of(rel)
        modules.setdefault(module, {"files": 0})["files"] += 1
    for edge in edges:
        modules.setdefault(edge[0], {"files": 0})
        modules.setdefault(edge[1], {"files": 0})
    return {
        "modules": modules,
        "files": {rel: {"module": dg.module_of(rel)} for rel in files},
        "edges": [{"from": source, "to": target, "kind": "imports"}
                  for source, target in edges],
        "meta": {"content_fingerprint": "graph-fixture-v1",
                 "scanned_head": "candidate-fixture"},
    }


def test_source_touchpoints_are_exhaustive_verified_and_provenance_bound():
    with tempfile.TemporaryDirectory() as ws:
        w(ws, "src/service.py",
          "def unique_symbol():\n    return 1\n\ndef shared():\n    return 2\n")
        w(ws, "src/other.py", "def shared():\n    return 3\n")
        w(ws, "config/settings.json", '{"feature": {"enabled": true}}\n')
        w(ws, "src/unsupported.wat", "(module)\n")
        os.symlink("service.py", os.path.join(ws, "src", "linked.py"))
        graph = _coverage_graph([
            "src/service.py", "src/other.py", "config/settings.json",
            "src/unsupported.wat",
        ])
        graph["modules"]["contract:source-touchpoint"] = {"files": 0}
        requested = [
            {"input_id": "file", "kind": "file", "path": "src/service.py"},
            {"input_id": "module", "kind": "module", "node": "src"},
            {"input_id": "symbol", "kind": "symbol", "symbol": "unique_symbol"},
            {"input_id": "config", "kind": "config-key",
             "path": "config/settings.json", "key": "feature.enabled"},
            {"input_id": "contract", "kind": "contract",
             "node": "contract:source-touchpoint"},
            {"input_id": "missing", "kind": "symbol", "symbol": "absent"},
            {"input_id": "ambiguous", "kind": "symbol", "symbol": "shared"},
            {"input_id": "language", "kind": "symbol",
             "path": "src/unsupported.wat", "symbol": "anything"},
            {"input_id": "runtime", "kind": "runtime-declaration",
             "runtime_kind": "reflection", "name": "late_binding"},
            {"input_id": "symlink", "kind": "file", "path": "src/linked.py"},
        ]

        result = dg.build_source_touchpoint_coverage(
            ws, requested, graph=graph,
            source={"tree": "candidate-fixture"},
            requirement={"id": "R-fixture"})

        rows = {row["input_id"]: row for row in result["results"]}
        assert sorted(rows) == sorted(row["input_id"] for row in requested)
        assert [rows[name]["state"] for name in
                ("file", "module", "symbol", "config", "contract")] == \
            ["verified"] * 5
        assert rows["missing"]["reason_code"] == "missing-symbol"
        assert rows["ambiguous"]["reason_code"] == "ambiguous-symbol"
        assert rows["language"]["reason_code"] == "unsupported-language"
        assert rows["runtime"]["reason_code"] == "unsupported-runtime"
        assert rows["symlink"]["reason_code"] == "source-symlink"
        assert all(row["provenance"]["source_fingerprint"] ==
                   result["source"]["fingerprint"] for row in rows.values())
        assert ws not in json.dumps(result, sort_keys=True)
        assert result["coverage"]["state"] == "incomplete"


def test_bounded_coverage_defaults_to_three_and_never_hides_stop_reasons():
    with tempfile.TemporaryDirectory() as ws:
        files = [f"{name}/mod.py" for name in "abcde"]
        for rel in files:
            w(ws, rel, "VALUE = 1\n")
        # Dependency direction is from NEEDS -> dependency. Starting at e,
        # reverse impact reaches d/c/b within three hops and leaves a pending.
        graph = _coverage_graph(files, [("d", "e"), ("c", "d"),
                                        ("b", "c"), ("a", "b")])
        request = [{"input_id": "start", "kind": "module", "node": "e"}]

        bounded = dg.build_source_touchpoint_coverage(
            ws, request, graph=graph,
            source={"tree": "candidate-fixture"},
            requirement={"id": "R-fixture"})
        assert bounded["bounds"]["local_depth"] == 3
        assert bounded["coverage"]["state"] == "truncated"
        assert bounded["coverage"]["complete"] is False
        assert "depth-limit" in bounded["reason_codes"]
        assert bounded["coverage"]["frontier"] == ["a"]

        fanout = dg.build_source_touchpoint_coverage(
            ws, request, graph=_coverage_graph(
                files, [("a", "e"), ("b", "e"), ("c", "e")]),
            source={"tree": "candidate-fixture"},
            requirement={"id": "R-fixture"},
            bounds={"max_fanout_per_node": 2})
        assert fanout["coverage"]["state"] == "truncated"
        assert "fanout-limit" in fanout["reason_codes"]
        assert fanout["counters"]["unexplored_edges"] == 1

        exhausted = dg.build_source_touchpoint_coverage(
            ws, request, graph=_coverage_graph(files, [("a", "e")]),
            source={"tree": "candidate-fixture"},
            requirement={"id": "R-fixture"})
        assert exhausted["coverage"]["state"] == "complete"
        assert exhausted["coverage"]["complete"] is True
        assert exhausted["reason_codes"] == []


def test_source_seam_pipeline_is_byte_stable_and_every_stage_records_bounds():
    with tempfile.TemporaryDirectory() as ws:
        w(ws, "src/a.py", "def entry():\n    return 1\n")
        w(ws, "api/b.py", "from src.a import entry\n")
        graph = _coverage_graph(
            ["src/a.py", "api/b.py"], [("api", "src")])
        request = [{"input_id": "entry", "kind": "symbol",
                    "path": "src/a.py", "symbol": "entry"}]
        kwargs = {
            "graph": graph,
            "source": {"tree": "candidate-fixture"},
            "requirement": {"id": "R-fixture"},
        }

        first = dg.build_source_touchpoint_coverage(ws, request, **kwargs)
        second = dg.build_source_touchpoint_coverage(ws, list(reversed(request)),
                                                     **kwargs)
        assert json.dumps(first, sort_keys=True, separators=(",", ":")) == \
            json.dumps(second, sort_keys=True, separators=(",", ":"))
        assert first["fingerprint"] == second["fingerprint"]
        assert first["coverage"]["state"] == "complete"
        assert set(first["bounds"]) == {
            "local_depth", "contract_depth", "requirement_depth",
            "max_files", "max_file_bytes", "max_aggregate_bytes",
            "max_symbols", "max_edges", "max_fanout_per_node",
            "parser_timeout_seconds", "aggregate_timeout_seconds",
            "automatic_retries", "agent_fanout_per_node",
        }
        assert first["extensions"]["stage_bounds"] == {
            "discovery": first["bounds"], "coverage": first["bounds"]}


if __name__ == "__main__":
    unittest.main()


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
        # M1 (v2.2.1): three-segment package tails keep colliding packages
        # (a/svc/db vs b/svc/db) distinct in the graph.
        w(self.ws, "src/main/java/com/acme/svc/db/A.java",
          "package com.acme.svc.db;\npublic class A {}\n")
        w(self.ws, "src/main/java/org/other/svc/db/B.java",
          "package org.other.svc.db;\npublic class B {}\n")
        g = dg.scan(self.ws)
        pairs = {(e["from"], e["to"]) for e in g["edges"]}
        self.assertIn(("com/shop/api", "com/shop/data"), pairs)
        self.assertIn(("com/shop/api", "ext:org.springframework.web"), pairs)
        self.assertIn("acme/svc/db", g["modules"])
        self.assertIn("other/svc/db", g["modules"])   # no collapse
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
