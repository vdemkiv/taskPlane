"""Store backend (fixture)."""
import sqlalchemy


def query(q):
    return sqlalchemy.text(q)
