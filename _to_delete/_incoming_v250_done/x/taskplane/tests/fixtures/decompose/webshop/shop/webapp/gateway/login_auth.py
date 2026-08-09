"""Token check (fixture)."""
import os

SECRET_ENV = "SHOP_TOKEN"


def require_token():
    token = os.environ.get(SECRET_ENV)
    if not token:
        raise PermissionError("missing auth token")
    return token
