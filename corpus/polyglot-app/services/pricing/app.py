from fastapi import FastAPI
from .rules import quote
api = FastAPI()
@api.get("/api/pricing/quote")
def q(): return quote()
