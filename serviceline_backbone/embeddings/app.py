from fastapi import FastAPI
from sentence_transformers import SentenceTransformer

app = FastAPI()
model = SentenceTransformer("BAAI/bge-small-en-v1.5")

@app.post("/embed")
async def embed(payload: dict):
    texts = payload["texts"]
    vectors = model.encode(texts).tolist()
    return {"vectors": vectors}
