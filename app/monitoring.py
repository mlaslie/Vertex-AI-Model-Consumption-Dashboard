"""Cloud Monitoring queries for Vertex AI publisher (Gemini) metrics.

Metric reference (verified against laslie-demo-project's descriptors):

  aiplatform.googleapis.com/publisher/online_serving/model_invocation_count
    DELTA INT64. metric labels: response_code, request_type, method,
      error_category, source, input_token_size, output_token_size, ...
    resource type: aiplatform.googleapis.com/PublisherModel
    resource labels: model_user_id, publisher, model_version_id, location

  aiplatform.googleapis.com/publisher/online_serving/token_count
    DELTA INT64. metric labels: type (input|output), modality, request_type, ...
    (Use this one for summing — the sibling "tokens" metric is a DISTRIBUTION
    and rejects ALIGN_RATE / ALIGN_SUM.)

  aiplatform.googleapis.com/publisher/online_serving/model_invocation_latencies
    DELTA DISTRIBUTION (unit: ms)

The model identifier is on the *resource* (`resource.label.model_user_id`),
not on the metric.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from google.api import metric_pb2
from google.cloud import monitoring_v3
from google.cloud.monitoring_v3 import Aggregation, TimeInterval

INVOCATION_COUNT = (
    "aiplatform.googleapis.com/publisher/online_serving/model_invocation_count"
)
TOKENS = "aiplatform.googleapis.com/publisher/online_serving/token_count"
INVOCATION_LATENCIES = (
    "aiplatform.googleapis.com/publisher/online_serving/model_invocation_latencies"
)
MODEL_FIELD = "resource.label.model_user_id"

ALIGN_RATE = Aggregation.Aligner.ALIGN_RATE
ALIGN_DELTA = Aggregation.Aligner.ALIGN_DELTA
ALIGN_SUM = Aggregation.Aligner.ALIGN_SUM
REDUCE_SUM = Aggregation.Reducer.REDUCE_SUM
REDUCE_NONE = Aggregation.Reducer.REDUCE_NONE


@dataclass(frozen=True)
class TimePoint:
    timestamp: float  # unix seconds
    value: float


@dataclass(frozen=True)
class Series:
    label: str
    points: list[TimePoint]


def _interval(window_seconds: int) -> TimeInterval:
    now = time.time()
    return TimeInterval(
        {
            "end_time": {"seconds": int(now)},
            "start_time": {"seconds": int(now - window_seconds)},
        }
    )


def _filter(metric_type: str, model_filter: str | None = None) -> str:
    parts = [f'metric.type = "{metric_type}"']
    if model_filter:
        parts.append(f'resource.label.model_user_id = "{model_filter}"')
    return " AND ".join(parts)


def _point_int(p) -> int:
    return int(p.value.int64_value)


def _point_double(p) -> float:
    return float(p.value.double_value)


class MonitoringClient:
    def __init__(self, project_id: str):
        self.project = f"projects/{project_id}"
        self.client = monitoring_v3.MetricServiceClient()

    def _list(
        self,
        metric_type: str,
        window_seconds: int,
        alignment_period_seconds: int,
        aligner: Aggregation.Aligner,
        reducer: Aggregation.Reducer,
        group_by: Iterable[str] = (),
        model_filter: str | None = None,
    ):
        aggregation = Aggregation(
            {
                "alignment_period": {"seconds": alignment_period_seconds},
                "per_series_aligner": aligner,
                "cross_series_reducer": reducer,
                "group_by_fields": list(group_by),
            }
        )
        return self.client.list_time_series(
            request={
                "name": self.project,
                "filter": _filter(metric_type, model_filter),
                "interval": _interval(window_seconds),
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                "aggregation": aggregation,
            }
        )

    def list_aiplatform_metrics(self) -> list[dict[str, str]]:
        """Enumerate aiplatform.googleapis.com metric descriptors visible
        in this project. Empty list = no Vertex AI traffic has registered
        a descriptor yet."""
        descriptors = self.client.list_metric_descriptors(
            request={
                "name": self.project,
                "filter": 'metric.type = starts_with("aiplatform.googleapis.com/")',
            }
        )
        return [
            {
                "type": d.type,
                "kind": metric_pb2.MetricDescriptor.MetricKind.Name(d.metric_kind),
                "value_type": metric_pb2.MetricDescriptor.ValueType.Name(d.value_type),
                "display_name": d.display_name,
            }
            for d in descriptors
        ]

    def request_count_by_response_code(
        self, window_seconds: int, model_filter: str | None = None
    ) -> dict[str, int]:
        """Total invocations in the window, bucketed by HTTP response code."""
        series = self._list(
            INVOCATION_COUNT,
            window_seconds=window_seconds,
            alignment_period_seconds=window_seconds,
            aligner=ALIGN_DELTA,
            reducer=REDUCE_SUM,
            group_by=["metric.label.response_code"],
            model_filter=model_filter,
        )
        out: dict[str, int] = {}
        for ts in series:
            code = ts.metric.labels.get("response_code", "unknown")
            total = sum(_point_int(p) for p in ts.points)
            out[code] = out.get(code, 0) + total
        return out

    def requests_by_model(self, window_seconds: int) -> dict[str, int]:
        """Total invocations per model_user_id in the window."""
        return self._totals_grouped_by(
            window_seconds, MODEL_FIELD, "model_user_id"
        )

    def requests_by_region(self, window_seconds: int) -> dict[str, int]:
        """Total invocations per location (region) in the window."""
        return self._totals_grouped_by(
            window_seconds, "resource.label.location", "location"
        )

    def _totals_grouped_by(
        self, window_seconds: int, group_field: str, label_key: str
    ) -> dict[str, int]:
        series = self._list(
            INVOCATION_COUNT,
            window_seconds=window_seconds,
            alignment_period_seconds=window_seconds,
            aligner=ALIGN_DELTA,
            reducer=REDUCE_SUM,
            group_by=[group_field],
        )
        out: dict[str, int] = {}
        for ts in series:
            key = ts.resource.labels.get(label_key, "unknown")
            total = sum(_point_int(p) for p in ts.points)
            out[key] = out.get(key, 0) + total
        return out

    def queries_per_minute(
        self,
        window_seconds: int,
        alignment_period_seconds: int = 60,
        model_filter: str | None = None,
    ) -> list[Series]:
        """Per-bucket QPM time series, grouped by model_user_id."""
        series = self._list(
            INVOCATION_COUNT,
            window_seconds=window_seconds,
            alignment_period_seconds=alignment_period_seconds,
            aligner=ALIGN_RATE,
            reducer=REDUCE_SUM,
            group_by=[MODEL_FIELD],
            model_filter=model_filter,
        )
        results: list[Series] = []
        for ts in series:
            label = ts.resource.labels.get("model_user_id", "unknown")
            points = [
                TimePoint(
                    timestamp=p.interval.end_time.timestamp(),
                    # ALIGN_RATE is per-second; convert to per-minute.
                    value=_point_double(p) * 60.0,
                )
                for p in ts.points
            ]
            points.sort(key=lambda p: p.timestamp)
            results.append(Series(label=label, points=points))
        return results

    def tokens_per_minute(
        self,
        window_seconds: int,
        alignment_period_seconds: int = 60,
        model_filter: str | None = None,
    ) -> dict[str, list[Series]]:
        """Tokens-per-minute time series, split by type (input|output)
        and grouped by model_user_id."""
        series = self._list(
            TOKENS,
            window_seconds=window_seconds,
            alignment_period_seconds=alignment_period_seconds,
            aligner=ALIGN_RATE,
            reducer=REDUCE_SUM,
            group_by=["metric.label.type", MODEL_FIELD],
            model_filter=model_filter,
        )
        grouped: dict[str, list[Series]] = {}
        for ts in series:
            ttype = ts.metric.labels.get("type", "unknown")
            model = ts.resource.labels.get("model_user_id", "unknown")
            points = [
                TimePoint(
                    timestamp=p.interval.end_time.timestamp(),
                    value=_point_double(p) * 60.0,
                )
                for p in ts.points
            ]
            points.sort(key=lambda p: p.timestamp)
            grouped.setdefault(ttype, []).append(Series(label=model, points=points))
        return grouped

    def model_stats(
        self,
        window_seconds: int,
        alignment_period_seconds: int = 60,
    ) -> list[dict[str, float | int | str]]:
        """Per-model table: total queries, avg/peak QPS, avg/peak QPM,
        avg/peak input TPM. Peaks are computed from 1-minute buckets by
        default so they represent the busiest single minute in the window.
        """
        bucket_minutes = alignment_period_seconds / 60.0
        window_minutes = window_seconds / 60.0

        by_model: dict[str, dict[str, float | int | str]] = {}

        # Per-bucket query counts → peak/avg QPM and total queries.
        q_series = self._list(
            INVOCATION_COUNT,
            window_seconds=window_seconds,
            alignment_period_seconds=alignment_period_seconds,
            aligner=ALIGN_DELTA,
            reducer=REDUCE_SUM,
            group_by=[MODEL_FIELD],
        )
        for ts in q_series:
            model = ts.resource.labels.get("model_user_id", "unknown")
            counts = [_point_int(p) for p in ts.points]
            total = sum(counts)
            qpm_values = [c / bucket_minutes for c in counts]
            by_model[model] = {
                "model": model,
                "total_queries": total,
                "avg_qpm": (total / window_minutes) if window_minutes else 0.0,
                "peak_qpm": max(qpm_values) if qpm_values else 0.0,
                "avg_input_tpm": 0.0,
                "peak_input_tpm": 0.0,
            }

        # Per-bucket input-token counts → peak/avg input TPM.
        t_series = self._list(
            TOKENS,
            window_seconds=window_seconds,
            alignment_period_seconds=alignment_period_seconds,
            aligner=ALIGN_DELTA,
            reducer=REDUCE_SUM,
            group_by=[MODEL_FIELD, "metric.label.type"],
        )
        for ts in t_series:
            if ts.metric.labels.get("type") != "input":
                continue
            model = ts.resource.labels.get("model_user_id", "unknown")
            entry = by_model.get(model)
            if entry is None:
                continue
            tokens = [_point_int(p) for p in ts.points]
            total = sum(tokens)
            tpm_values = [t / bucket_minutes for t in tokens]
            entry["avg_input_tpm"] = (
                (total / window_minutes) if window_minutes else 0.0
            )
            entry["peak_input_tpm"] = max(tpm_values) if tpm_values else 0.0

        rows = list(by_model.values())
        for r in rows:
            r["avg_qps"] = r["avg_qpm"] / 60.0
            r["peak_qps"] = r["peak_qpm"] / 60.0
        rows.sort(key=lambda r: r["total_queries"], reverse=True)
        return rows

    def _total_tokens_by_type(self, window_seconds: int) -> dict[str, int]:
        series = self._list(
            TOKENS,
            window_seconds=window_seconds,
            alignment_period_seconds=window_seconds,
            aligner=ALIGN_DELTA,
            reducer=REDUCE_SUM,
            group_by=["metric.label.type"],
        )
        out: dict[str, int] = {}
        for ts in series:
            ttype = ts.metric.labels.get("type", "unknown")
            out[ttype] = out.get(ttype, 0) + sum(_point_int(p) for p in ts.points)
        return out

    def summary(self, window_seconds: int) -> dict[str, float | int]:
        """Headline numbers: total requests, success rate, avg tokens, avg QPM."""
        codes = self.request_count_by_response_code(window_seconds)
        total = sum(codes.values())
        success = sum(v for k, v in codes.items() if k.startswith("2"))
        success_rate = (success / total * 100.0) if total else 0.0
        avg_qpm = (total / (window_seconds / 60.0)) if window_seconds else 0.0

        tokens = self._total_tokens_by_type(window_seconds)
        avg_input = (tokens.get("input", 0) / total) if total else 0.0
        avg_output = (tokens.get("output", 0) / total) if total else 0.0

        return {
            "total_requests": total,
            "success_rate_pct": round(success_rate, 2),
            "avg_qpm": round(avg_qpm, 2),
            "avg_input_tokens": round(avg_input, 2),
            "avg_output_tokens": round(avg_output, 2),
        }
