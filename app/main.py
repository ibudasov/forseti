from dataclasses import asdict

from fastapi import FastAPI
from pedantic import frozen_dataclass


@frozen_dataclass(type_safe=True)
class Item:
    name: str
    price: float
    quantity: int = 1


app = FastAPI(title="Forseti API")


@app.get("/")
def read_root():
    return {"message": "Welcome to Forseti FastAPI"}


@app.post("/items/")
def create_item(item: Item):
    return {"item": asdict(item)}
