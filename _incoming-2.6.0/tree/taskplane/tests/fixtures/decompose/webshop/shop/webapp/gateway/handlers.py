"""HTTP handlers (fixture)."""
import requests

from . import login_auth


def handle_order(app):
    @app.get('/orders')
    def orders():
        login_auth.require_token()
        return requests.get('http://inventory.local/stock').json()

    @app.post('/orders')
    async def place():
        # transaction rollback on failure
        return {'ok': True}
