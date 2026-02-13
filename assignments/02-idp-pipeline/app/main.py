from fastapi import FastAPI

app = FastAPI(title="IDP Pipeline MVP", version="0.1.0")

@app.get("/")
def root():
    return {"message": "OK. Try /health"}
