from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from src.ghibli_portrait.api.routes import router
from src.ghibli_portrait.config import Settings

app = FastAPI()
app.include_router(router)

s = Settings()

app.mount('/tmp', StaticFiles(directory=s.TMP_PATH), name='tmp')