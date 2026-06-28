"""
Quick test harness for the DIRECT BytePlus ARK Seedream pipeline.

Two ways to use it:

1) As a standalone script. Pass the image (URL or local path) and optional QR url
   on the command line — falls back to IMAGE_PATH/QR_URL below if omitted:
       uv run python seedream_direct_test.py <imageUrlOrPath> [qrUrl]
       uv run python seedream_direct_test.py            # uses the defaults below
   It runs Stage 1 (Ghibli) + Stage 2 (QR merge) and saves the outputs to ./output/.

2) As a tiny FastAPI test server (same response theme as the main app):
       uv run uvicorn seedream_direct_test:app --port 8020
   Then open http://localhost:8020/docs and call:
       POST /seedream/ghibli      { "imgUrl": "<url or local path>" }
       POST /seedream/ghibli-qr   { "imgUrl": "<url or local path>", "url": "https://..." }
"""

import asyncio

# Make the project root importable when run as a standalone script from tests/manual/.
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.ghibli_portrait.api.responses import success_response, internal_error_response
from src.ghibli_portrait.models.schemas import ErrorStage, GhibliQRRequest
# Test-only pipeline helpers now live alongside this script (tests/manual/).
from seedream_pipeline import (
    download_to,
    run_pipeline,
    single_shot_ghibli_qr,
    stage1_to_ghibli,
)

# ============================================================================
# ADJUSTABLE INPUTS (standalone script mode)
# ============================================================================
# Can be a public URL OR a local file path (e.g. r"C:\Users\me\photo.jpg").
IMAGE_PATH = "https://i.ibb.co/mVTdQqJx/Whats-App-Image-2026-04-26-at-11-34-25-AM.jpg"
# URL to encode in the QR code that gets merged into the final image.
QR_URL = "https://example.com/profile"
OUTPUT_DIR = "output"


async def _main() -> None:
    print(f"[1/2] Stage 1 — converting to Ghibli:\n      {IMAGE_PATH}")
    result = await run_pipeline(IMAGE_PATH, QR_URL)

    print(f"[ok] Ghibli URL : {result.ghibli_url}")
    print(f"[ok] Final  URL : {result.final_url}")

    s1 = await download_to(result.ghibli_url, f"{OUTPUT_DIR}/stage1_ghibli.jpg")
    s2 = await download_to(result.final_url, f"{OUTPUT_DIR}/stage2_ghibli_qr.jpg")
    print(f"[saved] {s1}")
    print(f"[saved] {s2}")


# ============================================================================
# QUICK TEST ENDPOINTS (FastAPI) — same unified-envelope theme as main app
# ============================================================================
app = FastAPI(title="Seedream Direct (BytePlus) — Test", version="0.1.0")


class SeedreamGhibliRequest(BaseModel):
    model_config = {"populate_by_name": True}
    img_url: str = Field(..., alias="imgUrl", description="Image URL or local file path")


@app.get("/seedream/health", tags=["Health & Utilities"])
async def health():
    return JSONResponse(
        content=success_response(
            message="Seedream direct test server is running",
            data={"status": "healthy"},
        ).model_dump(by_alias=True)
    )


@app.post("/seedream/ghibli", tags=["Core APIs"], summary="Stage 1 only — portrait to Ghibli")
async def ghibli_only(req: SeedreamGhibliRequest):
    try:
        ghibli_url, _ = await stage1_to_ghibli(req.img_url)
        return JSONResponse(
            content=success_response(
                message="Ghibli transformation completed",
                data={"resultUrls": [ghibli_url], "model": "seedream-4-5", "stage": "STAGE1_GHIBLI"},
            ).model_dump(by_alias=True)
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=internal_error_response(
                message=f"Stage 1 failed: {e}", stage=ErrorStage.STAGE1_GHIBLI
            ).model_dump(by_alias=True),
        )


@app.post("/seedream/ghibli-qr", tags=["API Production"], summary="Full pipeline — Ghibli + QR merge")
async def ghibli_qr(req: GhibliQRRequest):
    try:
        result = await run_pipeline(req.img_url, req.url)
        return JSONResponse(
            content=success_response(
                message="Ghibli + QR pipeline completed successfully",
                data={
                    "resultUrls": [result.final_url],
                    "ghibliUrl": result.ghibli_url,
                    "model": "seedream-4-5",
                    "encodedUrl": req.url,
                },
            ).model_dump(by_alias=True)
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=internal_error_response(
                message=f"Pipeline failed: {e}", stage=ErrorStage.ORCHESTRATION
            ).model_dump(by_alias=True),
        )


@app.post(
    "/seedream/ghibli-qr-single",
    tags=["API Production"],
    summary="Single request — Ghibli + QR in ONE Seedream call",
)
async def ghibli_qr_single(req: GhibliQRRequest):
    """Both jobs (Ghibli restyle + QR merge) in a single API call, to test its accuracy."""
    try:
        final_url, _ = await single_shot_ghibli_qr(req.img_url, req.url)
        return JSONResponse(
            content=success_response(
                message="Ghibli + QR (single-shot) completed successfully",
                data={
                    "resultUrls": [final_url],
                    "model": "seedream-4-5",
                    "mode": "single-request",
                    "encodedUrl": req.url,
                },
            ).model_dump(by_alias=True)
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=internal_error_response(
                message=f"Single-shot failed: {e}", stage=ErrorStage.ORCHESTRATION
            ).model_dump(by_alias=True),
        )


if __name__ == "__main__":
    import sys

    # Allow passing the image and QR URL on the command line so the run uses the
    # link you provide instead of the hard-coded IMAGE_PATH:
    #   uv run python seedream_direct_test.py <imageUrlOrPath> [qrUrl]
    if len(sys.argv) > 1 and sys.argv[1].strip():
        IMAGE_PATH = sys.argv[1].strip()
    if len(sys.argv) > 2 and sys.argv[2].strip():
        QR_URL = sys.argv[2].strip()

    print(f"[input] image = {IMAGE_PATH}")
    print(f"[input] qr    = {QR_URL}")
    asyncio.run(_main())
