"""HTTP-facing orchestration for the auditable automatic factor miner."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.repositories.factors import TARGET_ONLY_FIELDS, FactorRepository
from quantlab.repositories.research_runs import ResearchRunRepository
from quantlab.services.factor_auto_mine import (
    AutoMineConfig,
    AutoMineError,
    _create_run,
    _read_dataset,
    formula_label,
    generate_candidates,
    mine_candidates,
    reason_label,
)
from quantlab.services.factor_calculation import FactorCalculationService
from quantlab.services.factor_manual import parse_expression


class FactorMiningService:
    def __init__(self, settings: Settings, database: Database, factors: FactorRepository) -> None:
        self.settings = settings
        self.database = database
        self.factors = factors
        self.research_runs = ResearchRunRepository(settings, database)

    @staticmethod
    def _config(payload: Any) -> AutoMineConfig:
        if not isinstance(payload, dict):
            raise AutoMineError("factor mining config must be an object")
        required = ("dataset_id", "dataset_version_id", "date_from", "train_end", "validation_end", "date_to")
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            raise AutoMineError(f"missing mining config: {', '.join(missing)}")
        def integer(key: str, default: int) -> int:
            value = payload.get(key, default)
            if isinstance(value, bool):
                raise AutoMineError(f"{key} must be an integer")
            try:
                return int(value)
            except (TypeError, ValueError) as error:
                raise AutoMineError(f"{key} must be an integer") from error
        def number(key: str, default: float) -> float:
            try:
                return float(payload.get(key, default))
            except (TypeError, ValueError) as error:
                raise AutoMineError(f"{key} must be a number") from error
        def strings(key: str, default: tuple[str, ...], *, allow_empty: bool = False) -> tuple[str, ...]:
            value = payload.get(key, list(default))
            if not isinstance(value, list) or (not allow_empty and not value) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise AutoMineError(f"{key} must be a non-empty string array")
            return tuple(item.strip() for item in value)
        windows = payload.get("windows", [1, 2, 5])
        if not isinstance(windows, list):
            raise AutoMineError("windows must be an integer array")
        return AutoMineConfig(
            dataset_id=str(payload["dataset_id"]).strip(), dataset_version_id=str(payload["dataset_version_id"]).strip(),
            date_from=str(payload["date_from"]), train_end=str(payload["train_end"]),
            validation_end=str(payload["validation_end"]), date_to=str(payload["date_to"]),
            seed=integer("seed", 0), max_candidates=integer("max_candidates", 20), max_depth=integer("max_depth", 4),
            correlation_threshold=number("correlation_threshold", 0.95),
            min_validation_coverage=number("min_validation_coverage", 0.5), min_validation_rank_ic=number("min_validation_rank_ic", -1.0),
            fields=strings("fields", (), allow_empty=True), operators=strings("operators", ("identity", "shift", "rolling_mean")),
            windows=tuple(integer_value if isinstance(integer_value, int) and not isinstance(integer_value, bool) else int(integer_value) for integer_value in windows),
            markets=strings("markets", (), allow_empty=True),
        )

    def run(self, payload: Any) -> dict[str, Any]:
        config = self._config(payload.get("config") if isinstance(payload, dict) and "config" in payload else payload)
        return mine_candidates(self.settings, self.database, self.factors, config)

    def create_job(self, payload: Any) -> dict[str, Any]:
        config = self._config(payload.get("config") if isinstance(payload, dict) and "config" in payload else payload)
        run_id = _create_run(self.database, config)
        checkpoint = {"processed_candidates": [], "processed_dedupe_keys": [], "logs": [], "cursor": 0}
        run = self.research_runs.save_checkpoint(run_id, checkpoint)
        return {"research_run": run, "checkpoint": checkpoint, "next_action": "step_or_cancel"}

    @staticmethod
    def _checkpoint(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise AutoMineError("checkpoint must be an object")

        def values(key: str) -> list[str]:
            value = payload.get(key, [])
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise AutoMineError(f"{key} must be a string array")
            return list(dict.fromkeys(item.strip() for item in value))

        cursor = payload.get("cursor", 0)
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise AutoMineError("cursor must be a non-negative integer")
        return {"processed_candidates": values("processed_candidates"), "processed_dedupe_keys": values("processed_dedupe_keys"), "logs": values("logs"), "cursor": cursor}

    def checkpoint(self, run_id: str, payload: Any) -> dict[str, Any]:
        checkpoint = self._checkpoint(payload)
        return {"research_run": self.research_runs.save_checkpoint(run_id, checkpoint), "checkpoint": checkpoint}

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self.research_runs.get(run_id)
        if run is None:
            raise AutoMineError("research run not found")
        if run["status"] == "completed" or run["status"] == "failed":
            raise AutoMineError("only an active research job can be cancelled")
        checkpoint = run.get("summary", {}).get("checkpoint", {"processed_candidates": [], "processed_dedupe_keys": [], "logs": [], "cursor": 0})
        checkpoint = {**checkpoint, "cancel_reason": "cancelled"}
        if run["status"] == "queued":
            self.research_runs.transition(run_id, "running")
        self.research_runs.save_checkpoint(run_id, checkpoint)
        return {"research_run": self.research_runs.fail(run_id, "cancelled"), "checkpoint": checkpoint}

    def resume(self, run_id: str) -> dict[str, Any]:
        source = self.research_runs.get(run_id)
        if source is None:
            raise AutoMineError("research run not found")
        if source["status"] != "failed" or source.get("error_message") != "cancelled":
            raise AutoMineError("only a cancelled failed run can be resumed")
        config = source["config"]
        search = config.get("search", {})
        splits = config["splits"]
        resumed_config = AutoMineConfig(
            dataset_id=config["dataset_id"], dataset_version_id=config["dataset_version_id"],
            date_from=splits["train"]["from"], train_end=splits["train"]["to"],
            validation_end=splits["validation"]["to"], date_to=splits["test"]["to"],
            seed=int(config.get("seed", 0)), max_candidates=int(search.get("max_candidates", 20)),
            max_depth=int(search.get("max_depth", 4)), correlation_threshold=float(search.get("correlation_threshold", 0.95)),
            min_validation_coverage=float(search.get("min_validation_coverage", 0.5)),
            min_validation_rank_ic=float(search.get("min_validation_rank_ic", -1.0)),
            fields=tuple(search.get("fields", [])), operators=tuple(search.get("operators", ("identity", "shift", "rolling_mean"))),
            windows=tuple(search.get("windows", (1, 2, 5))),
            markets=tuple(search.get("markets", ())),
        )
        checkpoint = source.get("summary", {}).get("checkpoint", {})
        frame = _read_dataset(self.settings, self.database, resumed_config)
        available = sorted(set(frame.columns) - {"date", "instrument"} - TARGET_ONLY_FIELDS)
        candidates = generate_candidates(
            list(search.get("fields", [])) or available,
            list(search.get("operators", ("identity", "shift", "rolling_mean"))),
            list(search.get("windows", (1, 2, 5))),
            seed=resumed_config.seed,
            max_candidates=resumed_config.max_candidates,
            max_depth=resumed_config.max_depth,
            correlation_threshold=resumed_config.correlation_threshold,
        )
        skipped = set(checkpoint.get("processed_dedupe_keys", []))
        remaining = [item for item in candidates if item["dedupe_key"] not in skipped]
        child_id = _create_run(self.database, resumed_config, parent_run_id=run_id)
        child_config = self.research_runs.get(child_id)["config"]
        child_config["parent_run_id"] = run_id
        child_config["job_checkpoint"] = checkpoint
        with self.database.transaction() as connection:
            connection.execute("UPDATE research_runs SET config_json=? WHERE run_id=?", (json.dumps(child_config, ensure_ascii=False, sort_keys=True), child_id))
        child_checkpoint = {**checkpoint, "resumed_from_run_id": run_id}
        child = self.research_runs.save_checkpoint(child_id, child_checkpoint)
        return {"research_run": child, "checkpoint": child_checkpoint, "skipped_dedupe_keys": sorted(skipped), "remaining_candidates": remaining}

    @staticmethod
    def _market_label(markets: list[str]) -> str:
        labels = {"SH": "沪市", "SZ": "深市", "BJ": "北交所"}
        if not markets:
            return "全部市场"
        return "、".join(labels.get(item, item) for item in markets)

    @staticmethod
    def _operator_label(operator: str) -> str:
        labels = {
            "identity": "原值",
            "shift": "往前看 N 日",
            "diff": "N 日差分",
            "pct_change": "N 日涨跌幅",
            "rolling_mean": "N 日均值",
            "rolling_std": "N 日波动",
            "rolling_max": "N 日最高",
            "rolling_min": "N 日最低",
            "rolling_sum": "N 日合计",
            "ts_rank": "过去 N 日分位",
            "ts_zscore": "相对历史标准化",
            "rolling_bias": "相对均线偏离",
            "rolling_corr": "N 日相关",
            "rolling_cov": "N 日协方差",
            "ewm_mean": "指数加权均值",
            "cs_rank": "当天截面排名",
        }
        return labels.get(operator, operator)

    def _task_items(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        summary = run.get("summary") or {}
        stored = summary.get("task_items")
        if isinstance(stored, list) and stored:
            return stored
        items: list[dict[str, Any]] = []
        for index, evaluation in enumerate(summary.get("evaluations") or []):
            if not isinstance(evaluation, dict):
                continue
            formula = str(evaluation.get("formula") or "")
            decision = str(evaluation.get("decision") or "rejected")
            reason = str(evaluation.get("reason") or "")
            items.append(
                {
                    "candidate_id": evaluation.get("dedupe_key") or f"eval-{index}",
                    "index": index,
                    "formula": formula,
                    "formula_label": formula_label(formula),
                    "dedupe_key": evaluation.get("dedupe_key"),
                    "input_fields": [],
                    "decision": decision,
                    "kept_in_task": decision == "accepted",
                    "reason": reason,
                    "reason_label": reason_label(reason),
                    "period_metrics": evaluation.get("period_metrics") or {},
                    "enabled": False,
                    "enabled_entity_id": None,
                    "enabled_version_id": None,
                    "generation_run_id": run["run_id"],
                    "dataset_id": run.get("dataset_id"),
                    "dataset_version_id": run.get("dataset_version_id"),
                }
            )
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT entity_id, version_id, formula, quality_json FROM factor_versions WHERE generation_run_id=? ORDER BY entity_id",
                (run["run_id"],),
            ).fetchall()
        by_formula = {str(row["formula"]): row for row in rows}
        for item in items:
            row = by_formula.get(str(item.get("formula")))
            if row is None:
                continue
            quality = json.loads(row["quality_json"] or "{}")
            if quality.get("enabled_from_task"):
                item["enabled"] = True
                item["enabled_entity_id"] = row["entity_id"]
                item["enabled_version_id"] = row["version_id"]
        return items

    def _job_public(self, run: dict[str, Any]) -> dict[str, Any]:
        config = run.get("config") or {}
        search = config.get("search") or {}
        splits = config.get("splits") or {}
        items = self._task_items(run)
        markets = [str(item) for item in search.get("markets") or []]
        fields = [str(item) for item in search.get("fields") or []]
        operators = [str(item) for item in search.get("operators") or []]
        return {
            "run_id": run["run_id"],
            "name": run.get("name") or "自动因子挖掘",
            "status": run["status"],
            "created_at": run.get("created_at"),
            "finished_at": run.get("finished_at"),
            "dataset_id": config.get("dataset_id") or run.get("dataset_id"),
            "dataset_version_id": config.get("dataset_version_id") or run.get("dataset_version_id"),
            "markets": markets,
            "market_label": self._market_label(markets),
            "date_from": (splits.get("train") or {}).get("from"),
            "date_to": (splits.get("test") or {}).get("to"),
            "fields": fields,
            "field_label": "、".join(formula_label(item) for item in fields) or "宽表可用字段",
            "operators": operators,
            "operator_label": "、".join(self._operator_label(item) for item in operators) or "未选择变换",
            "windows": search.get("windows") or [],
            "kept_count": sum(1 for item in items if item.get("kept_in_task")),
            "rejected_count": sum(1 for item in items if not item.get("kept_in_task")),
            "enabled_count": sum(1 for item in items if item.get("enabled")),
            "item_count": len(items),
            "detail_url": f"/research/factor-jobs?run_id={run['run_id']}",
        }

    def list_jobs(self, *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        listed = self.research_runs.list(research_type="automatic", page=page, page_size=page_size)
        items = []
        for row in listed["items"]:
            run = self.research_runs.get(row["run_id"])
            if run is None:
                continue
            items.append(self._job_public(run))
        return {**listed, "items": items}

    def job_detail(self, run_id: str) -> dict[str, Any]:
        run = self.research_runs.get(run_id)
        if run is None or run.get("research_type") != "automatic":
            raise AutoMineError("research run not found")
        job = self._job_public(run)
        return {
            "job": job,
            "research_run": run,
            "items": self._task_items(run),
            "config_summary": (
                f"{job['dataset_id']} / {job['dataset_version_id']} · {job['market_label']} · "
                f"{job['date_from'] or '—'}–{job['date_to'] or '—'} · 字段 {job['field_label']} · 变换 {job['operator_label']}"
            ),
        }

    def _existing_version(self, run_id: str, formula: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT entity_id, version_id FROM factor_versions WHERE generation_run_id=? AND formula=? ORDER BY entity_id",
                (run_id, formula),
            ).fetchone()
        if row is None:
            return None
        return self.factors.get(row["entity_id"], row["version_id"])

    def _definition_from_item(self, run: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        config = run.get("config") or {}
        dataset_id = str(item.get("dataset_id") or config.get("dataset_id") or run.get("dataset_id") or "")
        dataset_version_id = str(item.get("dataset_version_id") or config.get("dataset_version_id") or run.get("dataset_version_id") or "")
        formula = str(item.get("formula") or "")
        input_fields = item.get("input_fields")
        if not isinstance(input_fields, list) or not input_fields:
            parsed = parse_expression(formula, {formula.split(".", 1)[0]})
            input_fields = parsed.input_fields
        ast_dump = str(item.get("ast") or "")
        if not ast_dump:
            ast_dump = formula
        index = int(item.get("index") or 0)
        entity_id = f"factor_auto_{run['run_id'].replace('-', '_')}_{index}"
        metrics = item.get("period_metrics") or {}
        return {
            "entity_id": entity_id,
            "version_id": "v1",
            "name": item.get("formula_label") or formula_label(formula),
            "category": "自动挖掘",
            "dataset_id": dataset_id,
            "dataset_version_id": dataset_version_id,
            "formula": formula,
            "input_fields": input_fields,
            "source": "automatic_mining",
            "direction": "positive",
            "frequency": "daily",
            "missing_policy": "drop",
            "pit_policy": "as-of observation date",
            "pit_lineage": {
                "rule": "as-of observation date",
                "snapshot": f"dataset:{dataset_id}:{dataset_version_id}",
                "window_mode": "per_instrument_observation",
            },
            "upstream_factor_versions": [],
            "origin": "automatic",
            "author": "QuantLab",
            "code_hash": hashlib.sha256(ast_dump.encode()).hexdigest(),
            "generation_run_id": run["run_id"],
            "quality_status": "needs_review",
            "quality": {
                "period_metrics": metrics,
                "selection_reason": item.get("reason"),
                "selection_inputs": item.get("decision_inputs") or {},
                "parent_candidates": item.get("parent_candidates") or [],
                "enabled_from_task": True,
            },
        }

    @staticmethod
    def _calculation_public(result: dict[str, Any] | None, *, error_message: str | None = None) -> dict[str, Any]:
        result = result or {}
        return {
            "status": result.get("status") or ("failed" if error_message else None),
            "calculation_id": result.get("calculation_id"),
            "ic_mean": result.get("ic_mean"),
            "ic_positive_ratio": result.get("ic_positive_ratio"),
            "ic_std": result.get("ic_std"),
            "icir": result.get("icir"),
            "coverage": result.get("coverage"),
            "error_message": result.get("error_message") or error_message,
        }

    def _run_enable_analysis(
        self,
        calculator: FactorCalculationService,
        saved: dict[str, Any],
        *,
        date_from: str | None,
        date_to: str | None,
        markets: list[str],
    ) -> dict[str, Any]:
        try:
            result = calculator.run(
                saved["entity_id"],
                version_id=str(saved.get("version_id") or "v1"),
                date_from=date_from,
                date_to=date_to,
                markets=markets,
            )
            return self._calculation_public(result)
        except Exception as error:
            latest = calculator.latest(factor_id=str(saved["entity_id"]))
            return self._calculation_public(latest, error_message=str(error))

    def enable(self, run_id: str, candidate_ids: Any) -> dict[str, Any]:
        if not isinstance(candidate_ids, list) or not candidate_ids or any(
            not isinstance(item, str) or not item.strip() for item in candidate_ids
        ):
            raise AutoMineError("candidate_ids must be a non-empty string array")
        selected = list(dict.fromkeys(item.strip() for item in candidate_ids))
        run = self.research_runs.get(run_id)
        if run is None or run.get("research_type") != "automatic":
            raise AutoMineError("research run not found")
        items = self._task_items(run)
        by_id = {str(item.get("candidate_id")): item for item in items}
        job = self._job_public(run)
        date_from = job.get("date_from")
        date_to = job.get("date_to")
        markets = job.get("markets") or []
        calculator = FactorCalculationService(self.settings, self.database)
        enabled_items: list[dict[str, Any]] = []
        for candidate_id in selected:
            item = by_id.get(candidate_id)
            if item is None:
                raise AutoMineError("candidate not found")
            reason = str(item.get("reason") or "")
            if reason.startswith("evaluation_error"):
                raise AutoMineError("cannot enable unevaluable formula")
            if item.get("enabled") and item.get("enabled_entity_id"):
                enabled_items.append(item)
                continue
            existing = self._existing_version(run_id, str(item.get("formula") or ""))
            if existing is not None:
                quality = {**(existing.get("quality") or {}), "enabled_from_task": True}
                if existing.get("status") == "draft":
                    saved = self.factors.update_draft(existing["entity_id"], existing["version_id"], {"quality": quality})
                else:
                    saved = existing
            else:
                saved = self.factors.import_definition(self._definition_from_item(run, item))
            item["enabled"] = True
            item["enabled_entity_id"] = saved["entity_id"]
            item["enabled_version_id"] = saved["version_id"]
            item["calculation"] = self._run_enable_analysis(
                calculator,
                saved,
                date_from=date_from,
                date_to=date_to,
                markets=markets,
            )
            enabled_items.append(item)
        summary = dict(run.get("summary") or {})
        summary["task_items"] = items
        summary["candidates"] = [
            {
                "formula": item["formula"],
                "formula_label": item.get("formula_label"),
                "dedupe_key": item.get("dedupe_key"),
                "candidate_id": item.get("candidate_id"),
                "kept_in_task": True,
                "enabled": bool(item.get("enabled")),
                "enabled_entity_id": item.get("enabled_entity_id"),
                "period_metrics": item.get("period_metrics"),
                "selection_reason": item.get("reason"),
            }
            for item in items
            if item.get("kept_in_task")
        ]
        completed = self.research_runs.patch_summary(run_id, summary)
        first_factor_id = None
        for item in enabled_items:
            if (item.get("calculation") or {}).get("status") == "completed":
                entity = str(item.get("enabled_entity_id") or "")
                first_factor_id = entity.removeprefix("factor_") if entity.startswith("factor_") else entity
                break
        return {
            "enabled_count": len(enabled_items),
            "items": enabled_items,
            "calculations": [item.get("calculation") for item in enabled_items],
            "first_factor_id": first_factor_id,
            "research_run": completed,
            "job": self._job_public(completed),
        }

    def detail(self, run_id: str) -> dict[str, Any]:
        run = self.research_runs.get(run_id)
        if run is None:
            raise AutoMineError("research run not found")
        evaluations = run.get("summary", {}).get("evaluations", [])
        items = self._task_items(run)
        candidates = [item for item in items if item.get("kept_in_task")]
        return {
            "research_run": run,
            "candidates": candidates,
            "evaluations": evaluations,
            "rejections": run.get("summary", {}).get("rejections", []),
            "lineage": {
                "dataset_id": run.get("dataset_id"),
                "dataset_version_id": run.get("dataset_version_id"),
                "splits": run.get("summary", {}).get("splits", {}),
                "seed": run.get("summary", {}).get("seed"),
            },
            "candidate_count": len(items),
        }

    def candidate_draft(self, run_id: str, dedupe_key: str) -> dict[str, Any]:
        raise AutoMineError("请到因子计算任务勾选后再启用，不会从这里直接进因子库")
