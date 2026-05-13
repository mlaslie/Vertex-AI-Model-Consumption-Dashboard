from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.api_core import exceptions as gax_exceptions

from .config import settings
from .monitoring import MonitoringClient

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Vertex AI Gemini Monitoring")
client = MonitoringClient(settings.gcp_project_id)


_EMPTY = object()


def _handle_gcp(fn, *args, empty=_EMPTY, **kwargs):
    """Run a Cloud Monitoring call, mapping known errors to HTTP responses.

    If `empty` is supplied, NotFound (the metric descriptor doesn't exist yet
    because the project has never emitted it) is treated as "no data" and that
    fallback is returned instead of a 4xx/5xx.
    """
    try:
        return fn(*args, **kwargs)
    except gax_exceptions.NotFound:
        if empty is not _EMPTY:
            return empty
        raise HTTPException(
            status_code=404,
            detail=(
                "No Vertex AI publisher metrics found in this project yet. "
                "They appear automatically after the first Gemini API call "
                "(allow up to ~10 minutes for the descriptor to register)."
            ),
        )
    except gax_exceptions.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    except gax_exceptions.InvalidArgument as e:
        raise HTTPException(status_code=400, detail=str(e))
    except gax_exceptions.GoogleAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


_EMPTY_SUMMARY = {
    "total_requests": 0,
    "success_rate_pct": 0.0,
    "avg_qpm": 0.0,
    "avg_input_tokens": 0.0,
    "avg_output_tokens": 0.0,
}


@app.get("/api/summary")
def get_summary(window_seconds: int = Query(3600, ge=60, le=30 * 24 * 3600)):
    return _handle_gcp(client.summary, window_seconds, empty=_EMPTY_SUMMARY)


@app.get("/api/response-codes")
def get_response_codes(
    window_seconds: int = Query(3600, ge=60, le=30 * 24 * 3600),
    model: str | None = None,
):
    return _handle_gcp(
        client.request_count_by_response_code,
        window_seconds,
        model,
        empty={},
    )


@app.get("/api/requests-by-model")
def get_requests_by_model(
    window_seconds: int = Query(3600, ge=60, le=30 * 24 * 3600),
):
    return _handle_gcp(client.requests_by_model, window_seconds, empty={})


@app.get("/api/requests-by-region")
def get_requests_by_region(
    window_seconds: int = Query(3600, ge=60, le=30 * 24 * 3600),
):
    return _handle_gcp(client.requests_by_region, window_seconds, empty={})


@app.get("/api/model-stats")
def get_model_stats(
    window_seconds: int = Query(3600, ge=60, le=30 * 24 * 3600),
):
    return _handle_gcp(client.model_stats, window_seconds, empty=[])


@app.get("/api/qpm")
def get_qpm(
    window_seconds: int = Query(3600, ge=60, le=30 * 24 * 3600),
    alignment_seconds: int = Query(60, ge=60, le=86400),
    model: str | None = None,
):
    series = _handle_gcp(
        client.queries_per_minute,
        window_seconds,
        alignment_seconds,
        model,
        empty=[],
    )
    return [
        {"label": s.label, "points": [asdict(p) for p in s.points]}
        for s in series
    ]


@app.get("/api/tokens")
def get_tokens(
    window_seconds: int = Query(3600, ge=60, le=30 * 24 * 3600),
    alignment_seconds: int = Query(60, ge=60, le=86400),
    model: str | None = None,
):
    grouped = _handle_gcp(
        client.tokens_per_minute,
        window_seconds,
        alignment_seconds,
        model,
        empty={},
    )
    return {
        ttype: [
            {"label": s.label, "points": [asdict(p) for p in s.points]}
            for s in series_list
        ]
        for ttype, series_list in grouped.items()
    }


@app.get("/api/diagnostics")
def diagnostics():
    """List which Vertex AI metric descriptors exist in this project.

    Useful when /api/* returns empty — tells you whether the project has
    *any* publisher/serving metrics yet."""
    return _handle_gcp(client.list_aiplatform_metrics)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "project": settings.gcp_project_id}


app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static",
)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
