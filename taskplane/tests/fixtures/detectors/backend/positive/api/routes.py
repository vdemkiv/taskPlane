@app.get("/orders")
async def list_orders():
    with transaction():
        return fetch_orders()
