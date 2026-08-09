def list_orders(db, users):
    for u in users:
        rows = db.execute("SELECT * FROM orders WHERE user_id = ?", u.id)
        collect(rows)
