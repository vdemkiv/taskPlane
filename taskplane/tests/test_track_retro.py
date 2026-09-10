from taskplane.tests.phase_fixture import save_component_workflow
import os
import json
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import host_native  # noqa: E402
import lens  # noqa: E402
import loop  # noqa: E402
import track  # noqa: E402

CAT = lens.load_catalog()


_GONE = {"tech-strategy", "cost-roi", "business-alignment"}


class TestAdvisoryTierRemoved(unittest.TestCase):
    """v1.0 removed the exec advisory tier from the code-review catalog;
    strategy now lives in the on-demand north-star review, not a lens tier."""

    def test_strategy_artifact_routes_nothing(self):
        r = lens.route([], artifact_type="strategy", catalog=CAT)
        self.assertEqual({x["id"] for x in r["lenses"]}, set())

    def test_advisory_ids_gone_from_catalog_and_code_routes(self):
        self.assertFalse(_GONE & {l["id"] for l in CAT["lenses"]})
        r2 = lens.route(["src/todo/core.py"], catalog=CAT)
        self.assertFalse(_GONE & {x["id"] for x in r2["lenses"]})

    def test_advisory_not_an_nfr_axis(self):
        import requirements as reqs
        self.assertFalse(_GONE & reqs.NFR_LENSES)



class TestTracks(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_new_list_switch_close(self):
        out = track.new(self.ws, "auth", "build auth")
        self.assertIsNone(out["active"])
        track.new(self.ws, "billing", "build billing")
        self.assertEqual(len(track.list_(self.ws)["tracks"]), 2)
        self.assertEqual(track.switch(self.ws, "auth")["code"], "unsupported_track_switch")
        self.assertIsNone(loop.load(self.ws))
        track.close(self.ws, "auth")
        self.assertIsNone(track.list_(self.ws)["active"])




if __name__ == "__main__":
    unittest.main()
