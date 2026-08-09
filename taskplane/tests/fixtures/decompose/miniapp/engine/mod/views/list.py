"""List view — renders item collections (fixture)."""
from . import detail
from engine.mod.store import db


def render_list(items):
    rows = [detail.render_detail(i) for i in items]
    return "\n".join(rows) + str(db.query("select 1"))
