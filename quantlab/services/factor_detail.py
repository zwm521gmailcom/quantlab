"""Read-only factor version details and persistent backtest draft references."""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from quantlab.config import Settings
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.repositories.database import Database
from quantlab.repositories.factors import TARGET_ONLY_FIELDS, FactorRepository
from quantlab.repositories.research_runs import ResearchRunRepository


_VERSION_NUMBER = re.compile(r"^v(\d+)")
MAX_DRAFT_FACTORS = 50
MAX_DIAGNOSTIC_ROWS = 200_000
MAX_DIAGNOSTIC_SERIES = 250


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _entity_id(factor_id: str) -> str:
    return factor_id if str(factor_id).startswith("factor_") else f"factor_{factor_id}"


def _number(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


class FactorDetailService:
    def __init__(self, settings: Settings, database: Database, factors: FactorRepository) -> None:
        self.settings = settings
        self.database = database
        self.factors = factors
        self.artifacts = ArtifactRepository(settings, database)
        self.research_runs = ResearchRunRepository(settings, database)

    def _history(self, entity_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT version_id, status, quality_status, last_verified_at, origin "
                "FROM factor_versions WHERE entity_id=? ORDER BY version_id",
                (entity_id,),
            ).fetchall()
        items = [dict(row) for row in rows]
        return {"items": items, "count": len(items)}

    @staticmethod
    def _unavailable(message: str) -> dict[str, Any]:
        return {"status": "unavailable", "message": message}

    @staticmethod
    def _series(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return items[-MAX_DIAGNOSTIC_SERIES:]

    def _read_diagnostic_sample(self, path: Path, field: str) -> tuple[pd.DataFrame, list[str], int]:
        parquet = pq.ParquetFile(path)
        available = set(parquet.schema_arrow.names)
        candidates = [field, "date", "trade_date", "instrument", "ts_code", "future_return", "return", "next_return"]
        columns = list(dict.fromkeys(item for item in candidates if item in available))
        batches = []
        rows_read = 0
        scanner = ds.dataset(path, format="parquet").scanner(columns=columns, use_threads=False)
        for batch in scanner.to_batches():
            if rows_read >= MAX_DIAGNOSTIC_ROWS:
                break
            take = min(batch.num_rows, MAX_DIAGNOSTIC_ROWS - rows_read)
            batches.append(batch.slice(0, take))
            rows_read += take
        if not batches:
            return pd.DataFrame(columns=columns), columns, rows_read
        import pyarrow as pa

        table = batches[0] if len(batches) == 1 else pa.Table.from_batches(batches)
        return table.to_pandas(), columns, rows_read

    def _target_diagnostics(self, frame: pd.DataFrame, field: str) -> dict[str, Any]:
        date_field = next((item for item in ("date", "trade_date") if item in frame), None)
        instrument_field = next((item for item in ("instrument", "ts_code") if item in frame), None)
        target_field = next((item for item in ("future_return", "return", "next_return") if item in frame), None)
        missing = []
        if date_field is None:
            missing.append("date/trade_date")
        if instrument_field is None:
            missing.append("instrument/ts_code")
        if target_field is None:
            missing.append("future_return")
        if missing:
            message = f"缺少真实诊断字段：{', '.join(missing)}；未生成替代指标"
            unavailable = self._unavailable(message)
            return {
                "daily_ic": unavailable,
                "group_returns": unavailable,
                "turnover": unavailable,
                "stability": unavailable,
                "unavailable_metrics": ["daily_ic", "group_returns", "turnover", "stability"],
            }

        values = frame[[date_field, instrument_field, field, target_field]].copy()
        values["_factor"] = pd.to_numeric(values[field], errors="coerce")
        values["_target"] = pd.to_numeric(values[target_field], errors="coerce")
        values = values.dropna(subset=["_factor", "_target", date_field, instrument_field])
        daily_ic: list[dict[str, Any]] = []
        group_daily: list[dict[str, Any]] = []
        top_members: dict[str, set[str]] = {}
        for date_value, group in values.groupby(date_field, sort=True):
            if len(group) < 3:
                continue
            date_text = str(date_value)
            ic = group["_factor"].rank(method="average").corr(group["_target"].rank(method="average"))
            if pd.notna(ic):
                daily_ic.append({"date": date_text, "ic": float(ic), "count": int(len(group))})
            try:
                bins = pd.qcut(group["_factor"], q=5, labels=False, duplicates="drop")
            except ValueError:
                bins = pd.Series(index=group.index, dtype="float64")
            grouped = group.assign(_group=bins).dropna(subset=["_group"])
            if len(grouped) < 3:
                continue
            means = grouped.groupby("_group", observed=True)["_target"].mean()
            group_daily.append({"date": date_text, "returns": {f"group_{int(key) + 1}": float(value) for key, value in means.items()}, "count": int(len(grouped))})
            top_group = int(means.index.max())
            top_members[date_text] = set(grouped.loc[grouped["_group"] == top_group, instrument_field].astype(str))

        ic_values = [item["ic"] for item in daily_ic]
        turnover_series: list[dict[str, Any]] = []
        previous: set[str] | None = None
        for date_text, members in top_members.items():
            if previous:
                turnover_series.append({"date": date_text, "turnover": float(1 - len(previous & members) / max(len(previous), 1)), "previous_count": len(previous), "current_count": len(members)})
            previous = members
        mean_ic = _number(pd.Series(ic_values).mean()) if ic_values else None
        positive_ratio = float(sum(value > 0 for value in ic_values) / len(ic_values)) if ic_values else None
        return {
            "daily_ic": {"status": "available" if daily_ic else "insufficient_data", "count": len(daily_ic), "mean": mean_ic, "positive_ratio": positive_ratio, "series": self._series(daily_ic)},
            "group_returns": {"status": "available" if group_daily else "insufficient_data", "count": len(group_daily), "daily": self._series(group_daily)},
            "turnover": {"status": "available" if turnover_series else "insufficient_data", "count": len(turnover_series), "mean": _number(pd.Series([item["turnover"] for item in turnover_series]).mean()) if turnover_series else None, "series": self._series(turnover_series)},
            "stability": {"status": "available" if daily_ic else "insufficient_data", "ic_std": _number(pd.Series(ic_values).std(ddof=0)) if ic_values else None, "positive_ic_ratio": positive_ratio, "observation_count": len(daily_ic)},
            "unavailable_metrics": [],
        }

    @staticmethod
    def _histogram(values: pd.Series) -> list[dict[str, Any]]:
        minimum = float(values.min())
        maximum = float(values.max())
        if minimum == maximum:
            return [{"left": minimum, "right": maximum, "count": int(values.size)}]
        buckets = pd.cut(values, bins=10, include_lowest=True).value_counts().sort_index()
        return [{"left": float(interval.left), "right": float(interval.right), "count": int(count)} for interval, count in buckets.items()]

    def _quality(self, record: dict[str, Any]) -> dict[str, Any]:
        row = self.factors._row(record["factor_entity_id"], record["factor_version_id"])
        if row is None:
            raise ValueError("FactorVersion not found")
        path = self.settings.require_read_path(row["path"])
        field = record["input_fields"][0]
        footer = self.factors._footer_stats(path, field)
        frame, columns, rows_read = self._read_diagnostic_sample(path, field)
        values = pd.to_numeric(frame[field], errors="coerce") if field in frame else pd.Series(dtype="float64")
        clean = values.dropna()
        sample_coverage = None if rows_read == 0 else float(clean.size / rows_read)
        if clean.empty:
            quantiles = {f"p{percentile}": None for percentile in (1, 5, 25, 50, 75, 95, 99)}
            distribution = {"count": 0, "mean": None, "std": None, "min": None, "max": None, "histogram": []}
        else:
            percentiles = (1, 5, 25, 50, 75, 95, 99)
            quantiles = {f"p{percentile}": _number(clean.quantile(percentile / 100)) for percentile in percentiles}
            distribution = {"count": int(clean.size), "mean": _number(clean.mean()), "std": _number(clean.std(ddof=0)), "min": _number(clean.min()), "max": _number(clean.max()), "histogram": self._histogram(clean)}
        return {
            "row_count": footer["row_count"],
            "missing_rows": int(rows_read - clean.size),
            "coverage": sample_coverage,
            "registered_missing_rows": footer["missing_rows"],
            "registered_coverage": footer["coverage"],
            "min": footer["min"],
            "max": footer["max"],
            "quantiles": quantiles,
            "distribution": distribution,
            "sample": {"rows_read": rows_read, "max_rows": MAX_DIAGNOSTIC_ROWS, "columns": columns, "truncated": footer["row_count"] > rows_read},
            **self._target_diagnostics(frame, field),
        }

    def _diagnostics(self, entity_id: str, version_id: str, record: dict[str, Any]) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT diagnosed_at, summary_json, diagnostic_run_id, artifact_id FROM factor_diagnostics "
                "WHERE entity_id=? AND version_id=?",
                (entity_id, version_id),
            ).fetchone()
        if row is None:
            return {"status": "not_diagnosed", "message": "尚未诊断", "quality": {}}
        run = self.research_runs.get(row["diagnostic_run_id"]) if row["diagnostic_run_id"] else None
        artifact = self.artifacts.get(row["artifact_id"]) if row["artifact_id"] else None
        return {
            "status": "diagnosed",
            "message": "已诊断",
            "diagnosed_at": row["diagnosed_at"],
            "quality": json.loads(row["summary_json"] or "{}"),
            "research_run": run,
            "artifact": artifact,
        }

    def get(self, factor_id: str, version_id: str) -> dict[str, Any]:
        entity_id = factor_id if str(factor_id).startswith("factor_") else f"factor_{factor_id}"
        record = self.factors.get(entity_id, version_id)
        if record is None:
            raise ValueError("FactorVersion not found")
        artifact = self.artifacts.get(record["artifact_id"]) if record.get("artifact_id") else None
        with self.database.connect() as connection:
            metadata_row = connection.execute(
                "SELECT metadata_json FROM dataset_versions WHERE entity_id=? AND version_id=?",
                (record["dataset_id"], record["dataset_version_id"]),
            ).fetchone()
        metadata = json.loads(metadata_row["metadata_json"] or "{}") if metadata_row else {}
        storage_alias = metadata.get("path_alias") or ""
        return {
            "factor": {
                "entity_id": record["factor_entity_id"],
                "name": record["name"],
                "category": record["category"],
                "status": record["factor_status"],
            },
            "definition": record,
            "lineage": {
                "dataset_id": record["dataset_id"],
                "dataset_version_id": record["dataset_version_id"],
                "input_fields": record["input_fields"],
                "upstream_factor_versions": record["upstream_factor_versions"],
                "code_hash": record.get("code_hash") or None,
                "generation_run_id": record.get("generation_run_id") or None,
                "artifact": artifact,
                "pit_policy": record["pit_policy"],
                "pit_lineage": record["pit_lineage"],
                "source": record["source"],
                "storage_alias": storage_alias,
            },
            "diagnostics": self._diagnostics(entity_id, version_id, record),
            "history": self._history(entity_id),
        }

    def diagnose(self, factor_id: str, version_id: str) -> dict[str, Any]:
        entity_id = _entity_id(factor_id)
        record = self.factors.get(entity_id, version_id)
        if record is None:
            raise ValueError("FactorVersion not found")
        if record["factor_status"] != "published" or record["status"] != "published":
            raise ValueError("published FactorVersion is required for diagnostics")
        diagnosed_at = _now()
        dataset_start = str(record.get("dataset_date_min") or "20170101").replace("-", "")
        dataset_end = str(record.get("dataset_date_max") or dataset_start).replace("-", "")
        config = {
            "dataset_id": record["dataset_id"],
            "dataset_version_id": record["dataset_version_id"],
            "factor_versions": [{"factor_id": entity_id, "version_id": version_id}],
            "sample": {"date_from": dataset_start, "date_to": dataset_end, "universe": "锁定因子版本诊断"},
            "filters": {"st_status": "dataset_snapshot", "suspended": "dataset_snapshot"},
            "filter_snapshot": {"st_status": "dataset_snapshot", "suspended": "dataset_snapshot", "captured_at": diagnosed_at},
            "label": {"definition": "t+1 open -> t+2 close", "price_fields": ["hfq_open", "hfq_close"]},
            "pit_snapshot": {"rule": record["pit_policy"], "captured_at": diagnosed_at, "lineage": record["pit_lineage"], "code_hash": record.get("code_hash") or None},
        }
        run = self.research_runs.create(
            name=f"因子诊断：{record['name']} · {version_id}", research_type="manual", config=config
        )
        try:
            quality = self._quality(record)
            output_dir = self.settings.runtime_root / "results" / run["run_id"]
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "factor_diagnostics.json"
            output_path.write_text(json.dumps(quality, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            artifact = self.artifacts.register(
                run_id=run["run_id"], path=output_path, display_name="因子真实诊断", artifact_role="factor_diagnostics"
            )
            run = self.research_runs.complete(run["run_id"], {"factor_id": entity_id, "version_id": version_id, "diagnosed_at": diagnosed_at, "coverage": quality["coverage"]})
        except Exception as error:
            self.research_runs.fail(run["run_id"], str(error))
            raise
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO factor_diagnostics(entity_id, version_id, diagnosed_at, summary_json, diagnostic_run_id, artifact_id) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(entity_id, version_id) DO UPDATE SET "
                "diagnosed_at=excluded.diagnosed_at, summary_json=excluded.summary_json, "
                "diagnostic_run_id=excluded.diagnostic_run_id, artifact_id=excluded.artifact_id",
                (entity_id, version_id, diagnosed_at, json.dumps(quality, ensure_ascii=False, sort_keys=True), run["run_id"], artifact.artifact_id),
            )
        return {
            "status": "diagnosed", "message": "已诊断", "diagnosed_at": diagnosed_at, "quality": quality,
            "research_run": run, "artifact": self.artifacts.get(artifact.artifact_id),
        }

    def copy_version(self, factor_id: str, version_id: str) -> dict[str, Any]:
        entity_id = _entity_id(factor_id)
        source = self.factors.get(entity_id, version_id)
        if source is None:
            raise ValueError("FactorVersion not found")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT version_id FROM factor_versions WHERE entity_id=?",
                (entity_id,),
            ).fetchall()
        numbers = [int(match.group(1)) for row in rows if (match := _VERSION_NUMBER.match(row["version_id"]))]
        next_version = f"v{max(numbers, default=0) + 1}"
        definition = {
            "entity_id": entity_id,
            "name": source["name"],
            "category": source["category"],
            "version_id": next_version,
            "dataset_id": source["dataset_id"],
            "dataset_version_id": source["dataset_version_id"],
            "formula": source["formula"],
            "input_fields": source["input_fields"],
            "source": source["source"],
            "direction": source["direction"],
            "frequency": source["frequency"],
            "missing_policy": source["missing_policy"],
            "pit_policy": source["pit_policy"],
            "pit_lineage": source["pit_lineage"],
            "upstream_factor_versions": source["upstream_factor_versions"],
            "quality_status": "needs_review",
            "origin": source["origin"],
            "code_hash": source.get("code_hash") or "",
            "generation_run_id": source.get("generation_run_id") or "",
            "artifact_id": source.get("artifact_id") or "",
        }
        return self.factors.import_definition(definition)

    @staticmethod
    def _normalize_draft_factors(items: Any) -> list[dict[str, str]]:
        if not isinstance(items, list) or not items:
            raise ValueError("factor_version_ids are required")
        if len(items) > MAX_DRAFT_FACTORS:
            raise ValueError(f"factor_version_ids supports at most {MAX_DRAFT_FACTORS} items")
        normalized: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("factor_version_ids must contain objects")
            extra = set(item) - {"factor_id", "entity_id", "version_id"}
            if extra:
                raise ValueError("additional factor reference fields are not allowed")
            factor_id = str(item.get("factor_id", item.get("entity_id", ""))).strip()
            version_id = str(item.get("version_id", "")).strip()
            if factor_id in TARGET_ONLY_FIELDS or factor_id.removeprefix("factor_") in TARGET_ONLY_FIELDS:
                raise ValueError(f"{factor_id} is target-only and cannot be a backtest factor")
            if not factor_id or not version_id:
                raise ValueError("factor references require factor_id and version_id")
            factor_id = _entity_id(factor_id)
            reference = {"factor_id": factor_id, "version_id": version_id}
            if reference in normalized:
                raise ValueError(f"duplicate factor version: {factor_id}:{version_id}")
            normalized.append(reference)
        return normalized

    def create_backtest_draft(self, items: Any) -> dict[str, Any]:
        normalized = self._normalize_draft_factors(items)
        self._validate_draft_factor_versions(normalized)
        timestamp = _now()
        draft_id = f"bt-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO backtest_drafts(draft_id, revision, factor_version_ids_json, created_at, updated_at) "
                "VALUES (?, 1, ?, ?, ?)",
                (draft_id, json.dumps(normalized, ensure_ascii=False, sort_keys=True), timestamp, timestamp),
            )
        return self.get_backtest_draft(draft_id)

    def _validate_draft_factor_versions(self, normalized: list[dict[str, str]]) -> None:
        for item in normalized:
            record = self.factors.get(item["factor_id"], item["version_id"])
            if record is None or record["factor_status"] != "published" or record["status"] != "published":
                raise ValueError(f"published factor version is required: {item['factor_id']}:{item['version_id']}")

    def update_backtest_draft(
        self, draft_id: str, items: Any, *, expected_revision: int
    ) -> dict[str, Any]:
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected revision must be a positive integer")
        normalized = self._normalize_draft_factors(items)
        self._validate_draft_factor_versions(normalized)
        timestamp = _now()
        with self.database.transaction() as connection:
            result = connection.execute(
                "UPDATE backtest_drafts SET revision=revision+1, factor_version_ids_json=?, updated_at=? "
                "WHERE draft_id=? AND revision=?",
                (
                    json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                    timestamp,
                    draft_id,
                    expected_revision,
                ),
            )
        if result.rowcount != 1:
            with self.database.connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM backtest_drafts WHERE draft_id=?", (draft_id,)
                ).fetchone()
            if exists is None:
                raise ValueError("BacktestDraft not found")
            raise ValueError("BacktestDraft revision conflict")
        return self.get_backtest_draft(draft_id)

    def get_backtest_draft(self, draft_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT draft_id, revision, factor_version_ids_json, created_at, updated_at "
                "FROM backtest_drafts WHERE draft_id=?",
                (draft_id,),
            ).fetchone()
        if row is None:
            raise ValueError("BacktestDraft not found")
        return {
            "draft_id": row["draft_id"],
            "revision": row["revision"],
            "factor_version_ids": json.loads(row["factor_version_ids_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
