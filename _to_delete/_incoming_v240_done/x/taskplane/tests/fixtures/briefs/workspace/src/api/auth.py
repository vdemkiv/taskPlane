# Frozen routing fixture (t6, R-0002) — a small code change under src/api/**
# that summons the security/backend/api lens family. CONTENT IS FROZEN: the
# legacy router routes on paths, but the tree is part of the golden capture.
def login(user: str, password: str) -> bool:
    return bool(user) and bool(password)
