# Vertex AI Model Consumption Dashboard

An interactive web dashboard for Vertex AI Gemini usage in a GCP project. It
pulls metrics from the Google Cloud Monitoring API and renders them with
Chart.js.

## What you get

- Headline cards: total requests, average QPM, success rate, avg input/output tokens
- **Queries per minute** time series (full width)
- **Tokens per minute** input vs output time series (full width)
- Doughnut breakdowns: response codes (200/429/etc), queries by model, queries by region
- Per-model statistics table: total queries, avg/peak QPS, avg/peak QPM, avg/peak input TPM
- Window selector (1h / 6h / 24h / 7d / 30d) with adaptive bucket size
- Model filter, optional auto-refresh, on-the-fly redraw

Window 30d switches the QPM chart to a daily average (non-zero days only) with
date-only x-axis labels.

## Quick start

```bash
# 1. Clone
git clone https://github.com/mlaslie/Vertex-AI-Model-Consumption-Dashboard.git
cd Vertex-AI-Model-Consumption-Dashboard

# 2. Auth with GCP (Application Default Credentials)
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID

# 3. Create a venv and install deps
python3 -m venv vertex-model-consumption
source vertex-model-consumption/bin/activate
pip install -r requirements.txt

# 4. Configure the project to monitor
cp .env.example .env
# edit .env: set GCP_PROJECT_ID=your-project-id

# 5. Run
python run.py
```

Open http://127.0.0.1:8000.

The authenticated principal (your user, or a service-account key referenced
by `GOOGLE_APPLICATION_CREDENTIALS`) needs `roles/monitoring.viewer` on the
project being monitored.

## Metrics used

From `aiplatform.googleapis.com/publisher/online_serving/*`:

| Metric                       | Used for                                      |
|------------------------------|-----------------------------------------------|
| `model_invocation_count`     | Request totals, QPM, peaks, response codes    |
| `token_count`                | Input/output token TPM and averages           |
| `model_invocation_latencies` | (Available, not yet plotted)                  |

These descriptors only register in a project after the first Gemini API call.
Hit `/api/diagnostics` to see which Vertex AI metric descriptors exist in your
project.

## API

| Endpoint                         | Returns                                      |
|----------------------------------|----------------------------------------------|
| `GET /api/summary`               | Headline numbers for the selected window     |
| `GET /api/qpm`                   | Per-model queries-per-minute time series     |
| `GET /api/tokens`                | Tokens per minute split by input/output      |
| `GET /api/response-codes`        | Request counts bucketed by HTTP code         |
| `GET /api/requests-by-model`     | Totals per `model_user_id`                   |
| `GET /api/requests-by-region`    | Totals per `location` (region)               |
| `GET /api/model-stats`           | Per-model table rows                         |
| `GET /api/diagnostics`           | Available Vertex AI metric descriptors       |
| `GET /healthz`                   | Liveness                                     |

Common query params: `window_seconds` (60 … 30d), `model` (optional `model_user_id` filter), `alignment_seconds` (60 … 86400, time-series bucket size).

## Stack

- **Backend:** FastAPI + `google-cloud-monitoring`
- **Frontend:** vanilla JS + Chart.js (CDN) with the Luxon time adapter
- **Server:** Uvicorn

## Project layout

```
app/
  config.py       Pydantic settings (.env loader)
  monitoring.py   Cloud Monitoring queries
  main.py         FastAPI app + JSON endpoints
static/
  index.html      Dashboard layout
  css/style.css   Dark theme
  js/dashboard.js Chart wiring + auto-refresh
run.py            Uvicorn entrypoint
```
