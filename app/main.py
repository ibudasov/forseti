from dataclasses import asdict
from fastapi import FastAPI
from pedantic import frozen_dataclass

app = FastAPI(title="Forseti API")

@app.get("/")
def read_root():
    return {"message": "Welcome to Forseti API!"}
