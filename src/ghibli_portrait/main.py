from fastapi import FastAPI
from src.ghibli_portrait.api.routes import router


app = FastAPI()
app.include_router(router)

@app.get('/')
def root():
    return