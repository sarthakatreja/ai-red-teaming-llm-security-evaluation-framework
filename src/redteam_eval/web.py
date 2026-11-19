"""Local development web UI and JSON API. Never deploy publicly as-is."""

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .catalog import SCENARIOS
from .engine import evaluate
from .targets import DEFAULT_MODEL, OllamaTarget, ReferenceTarget, TargetError


app = FastAPI(
    title="AI Red-Teaming & LLM Security Evaluation Framework",
    version=__version__,
    description="Deterministic reference simulations and benign control checks for local models.",
    docs_url="/api/docs",
    redoc_url=None,
)

STATIC = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["reference_secure", "reference_vulnerable", "ollama"] = (
        "reference_vulnerable"
    )
    model: str = Field(default=DEFAULT_MODEL, max_length=100)


@app.middleware("http")
async def no_store_and_hardening(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path == "/":
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
    return response


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "favicon.ico", media_type="image/vnd.microsoft.icon")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__, "mode": "local-first"}


@app.get("/api/scenarios")
def scenarios():
    return {"scenarios": [scenario.public_dict() for scenario in SCENARIOS]}


@app.post("/api/evaluate")
def evaluate_api(request: EvaluationRequest):
    started_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    started = perf_counter()
    mode = "simulation"
    model = None
    if request.target == "reference_secure":
        target = ReferenceTarget("secure")
    elif request.target == "reference_vulnerable":
        target = ReferenceTarget("vulnerable")
    else:
        try:
            target = OllamaTarget(model=request.model)
            target.verify_ready()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TargetError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        mode = "local_model"
        model = target.model
    results = evaluate(target)
    return {
        "target": request.target,
        "execution": {
            "mode": mode,
            "model": model,
            "started_at": started_at,
            "duration_ms": round((perf_counter() - started) * 1000, 3),
        },
        "results": results,
    }

