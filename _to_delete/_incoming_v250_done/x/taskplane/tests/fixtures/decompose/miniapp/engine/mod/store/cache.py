"""Store cache (fixture)."""
from . import db


def cached(q):
    return db.query(q)
