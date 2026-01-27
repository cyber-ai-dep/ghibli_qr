from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from src.ghibli_portrait.api.routes import router, TAGS_METADATA
from src.ghibli_portrait.config import Settings

s = Settings()

app = FastAPI(
    title="Ghibli Portrait API V1",
    description=(
        "Production-ready Ghibli portrait transformation API with unified response format.\n\n"
        "## Primary Endpoint\n"
        "Use **POST /v1/ghibli-qr** for the complete Ghibli + QR pipeline.\n\n"
        "## Response Contract\n"
        "All responses follow a unified envelope:\n"
        "```json\n"
        '{"success": bool, "data": object|null, "message": string, "errors": array|null, "timestamp": string}\n'
        "```\n\n"
        "## Error Structure\n"
        "```json\n"
        '{"code": "SCREAMING_SNAKE_CASE", "type": "VALIDATION_ERROR|EXTERNAL_ERROR|SYSTEM_ERROR", '
        '"stage": "INPUT|SOURCE_RESOLUTION|STAGE1_GHIBLI|STAGE2_QR|ORCHESTRATION", "field": "camelCase", "message": "..."}\n'
        "```"
    ),
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
)

app.include_router(router)

app.mount('/tmp', StaticFiles(directory=s.TMP_PATH), name='tmp')