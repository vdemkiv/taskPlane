"""Order persistence (fixture)."""
from . import queries


def init(conn):
    conn.execute(queries.CREATE)
    conn.commit()


def migrate(conn):
    conn.execute(queries.MIGRATE)
    conn.commit()
