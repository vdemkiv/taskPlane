"""R-0007: every completed chunk has a production caller edge."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_live_edges_are_present_in_production_sources():
    sources = {name: (ROOT / "taskplane" / name).read_text()
               for name in ("tp.py", "loop.py", "review.py", "dashboard.py")}
    assert "context_note=explicit_context_note" in sources["tp.py"]
    assert "explicit_context_note=explicit_context_note" in sources["review.py"]
    assert "dod_graph_input=graph_input" in sources["tp.py"]
    assert sources["loop.py"].count("dod_graph_input=graph_input") >= 2
    assert sources["loop.py"].count("worker_guidance") >= 4
    assert "worker_efficiency_projection" in sources["dashboard.py"]
    assert "record_dispatch_audit" in sources["review.py"]

