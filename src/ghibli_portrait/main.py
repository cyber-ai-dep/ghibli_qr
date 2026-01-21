from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from src.ghibli_portrait.api.routes import router
from src.ghibli_portrait.config import Settings

app = FastAPI(
    title="Ghibli Portrait API V1",
    description="Production-ready Ghibli portrait transformation API with unified response format",
    version="1.0.0"
)

app.include_router(router)

s = Settings()

app.mount('/tmp', StaticFiles(directory=s.TMP_PATH), name='tmp')