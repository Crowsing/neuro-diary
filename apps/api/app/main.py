from fastapi import FastAPI

app = FastAPI(title="Neuro Diary API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
