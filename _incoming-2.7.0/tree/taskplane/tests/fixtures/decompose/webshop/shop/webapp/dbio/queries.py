"""Order queries (fixture)."""
CREATE = "CREATE TABLE orders (id INT PRIMARY KEY, total INT)"
MIGRATE = "ALTER TABLE orders ADD COLUMN placed_at TEXT"
REPORT = ("SELECT customer, SUM(total) FROM orders "
          "JOIN customers ON orders.cid = customers.id GROUP BY customer")


def report_rows(conn):
    return conn.execute(REPORT).fetchall()
