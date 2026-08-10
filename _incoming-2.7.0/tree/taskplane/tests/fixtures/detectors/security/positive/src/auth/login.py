SECRET_KEY = "not-a-real-secret"

def authenticate(user, password):
    return hmac_sign(password, SECRET_KEY)
