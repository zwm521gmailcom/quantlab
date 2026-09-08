"""FastAPI application factory for the local-only foundation service."""

from __future__ import annotations

import json
from pathlib import Path

from typing import Literal

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


class NoStoreStaticFiles(StaticFiles):
    def is_not_modified(self, response_headers, request_headers) -> bool:
        return False

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        name = path.rsplit("/", 1)[-1]
        if name in {"app.js", "app.css", "nav.js"}:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            if "etag" in response.headers:
                del response.headers["etag"]
        return response

from quantlab.config import Settings
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.repositories.database import Database
from quantlab.services.catalog import DatasetCatalog
from quantlab.services.overview import OverviewService
from quantlab.services.kline import DEFAULT_KLINE_VERSION, KlineQueryService
from quantlab.services.factor_data import FactorDataService, MAX_ROWS as FACTOR_MAX_ROWS
from quantlab.repositories.research_runs import ResearchRunRepository
from quantlab.repositories.factors import FactorRepository
from quantlab.services.factor_detail import FactorDetailService
from quantlab.services.factor_manual import ManualFactorService
from quantlab.services.factor_mining import FactorMiningService
from quantlab.services.factor_calculation import FactorCalculationService
from quantlab.services.canonical_factor_pack import CanonicalFactorPackService
from quantlab.services.strategy_center import StrategyCenterService
from quantlab.services.model_training import ModelTrainingService
from quantlab.services.backtest_workbench import BacktestWorkbenchService
from quantlab.services.backtest_job import BacktestJobService
from quantlab.services.backtest_plan import BacktestPlanService
from quantlab.services.backtest_control import run_isolated, stop_run
from quantlab.services.result_archive import ResultArchiveService
from quantlab.services.settings import SettingsService
from quantlab.services.tushare_download import TushareDownloadService
from quantlab.services.canonical_market import apply_suspend_d
from quantlab.api.routes.overview import router as overview_router


def _error_payload(
    error_code: str,
    message: str,
    *,
    entity_id: str | None = None,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "error_code": error_code,
        "message": message,
        "entity_id": entity_id,
        "details": details or {},
    }


def _kline_params(
    *,
    ts_code: str | None,
    date_from: str | None,
    date_to: str | None,
    version_id: str,
    mode: Literal["raw", "hfq"],
    fields: str | None,
) -> dict[str, object]:
    return {
        "symbols": ts_code,
        "date_from": date_from,
        "date_to": date_to,
        "version_id": version_id,
        "mode": mode,
        "fields": fields,
    }


def _raise_kline_error(error: ValueError) -> None:
    raise HTTPException(
        status_code=400,
        detail={
            "error_code": "KLINE_QUERY_INVALID",
            "message": str(error),
            "entity_id": "ds_hfq_market_st_v1",
            "details": {},
        },
    ) from error


def _raise_factor_error(error: ValueError) -> None:
    raise HTTPException(
        status_code=400,
        detail={
            "error_code": "FACTOR_QUERY_INVALID",
            "message": str(error),
            "entity_id": "ds_canonical_market",
            "details": {},
        },
    ) from error


def _raise_factor_library_error(error: ValueError, *, status_code: int = 400) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={
            "error_code": "FACTOR_LIBRARY_INVALID" if status_code < 500 else "FACTOR_LIBRARY_ERROR",
            "message": str(error),
            "entity_id": None,
            "details": {},
        },
    ) from error


async def _json_body(request: Request) -> object:
    try:
        return await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        _raise_factor_library_error(ValueError("request body must be valid JSON"))
    raise AssertionError("unreachable")


def _raise_research_error(error: ValueError) -> None:
    message = str(error)
    status_code = 404 if "not found" in message else 400
    raise HTTPException(
        status_code=status_code,
        detail={
            "error_code": "RESEARCH_RUN_INVALID" if status_code == 400 else "RESEARCH_RUN_NOT_FOUND",
            "message": message,
            "entity_id": None,
            "details": {},
        },
    ) from error


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_database = database or Database(resolved_settings.database_path)
    resolved_database.initialize()
    catalog = DatasetCatalog(resolved_settings, resolved_database)
    artifacts = ArtifactRepository(resolved_settings, resolved_database)
    overview = OverviewService(resolved_database)
    kline = KlineQueryService(resolved_settings, resolved_database)
    factor_data = FactorDataService(resolved_settings, resolved_database)
    factors = FactorRepository(resolved_settings, resolved_database)
    factor_detail_service = FactorDetailService(resolved_settings, resolved_database, factors)
    factor_manual_service = ManualFactorService(resolved_settings, resolved_database, factors)
    factor_mining_service = FactorMiningService(resolved_settings, resolved_database, factors)
    factor_calculation = FactorCalculationService(resolved_settings, resolved_database)
    factor_pack_service = CanonicalFactorPackService(
        resolved_settings, resolved_database, factors, factor_calculation
    )
    strategy_center = StrategyCenterService(resolved_settings, resolved_database, factors)
    model_training = ModelTrainingService(
        resolved_settings, resolved_database, factors, factor_calculation, strategy_center
    )
    backtest_workbench = BacktestWorkbenchService(resolved_settings, resolved_database)
    backtest_job = BacktestJobService(resolved_settings, resolved_database)
    backtest_plan = BacktestPlanService(resolved_settings, resolved_database, backtest_workbench, backtest_job)
    result_archive = ResultArchiveService(resolved_settings, resolved_database)
    settings_service = SettingsService(resolved_settings)
    tushare_download = TushareDownloadService(resolved_settings)
    research_runs = ResearchRunRepository(resolved_settings, resolved_database)
    app = FastAPI(title="QuantLab", version="0.1.0")
    app.state.overview_service = overview
    app.state.kline_service = kline
    app.state.factor_data_service = factor_data
    app.state.factor_repository = factors
    app.state.factor_detail_service = factor_detail_service
    app.state.factor_manual_service = factor_manual_service
    app.state.factor_mining_service = factor_mining_service
    app.state.factor_calculation_service = factor_calculation
    app.state.factor_pack_service = factor_pack_service
    app.state.strategy_center_service = strategy_center
    app.state.backtest_workbench_service = backtest_workbench
    app.state.backtest_job_service = backtest_job
    app.state.backtest_plan_service = backtest_plan
    app.state.result_archive_service = result_archive
    app.state.research_run_repository = research_runs
    app.state.settings_service = settings_service
    app.state.tushare_download_service = tushare_download
    app.include_router(overview_router)
    pages = Path(__file__).parents[1] / "web/pages"
    assets = Path(__file__).parents[1] / "web/assets"
    app.mount("/assets", NoStoreStaticFiles(directory=assets), name="assets")

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404 and exc.detail == "Not Found":
            payload = _error_payload("NOT_FOUND", "resource not found")
        elif isinstance(exc.detail, dict):
            payload = _error_payload(
                str(exc.detail.get("error_code", "HTTP_ERROR")),
                str(exc.detail.get("message", "http error")),
                entity_id=exc.detail.get("entity_id"),
                details=exc.detail.get("details")
                if isinstance(exc.detail.get("details"), dict)
                else {},
            )
        else:
            payload = _error_payload("HTTP_ERROR", str(exc.detail))
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        payload = _error_payload(
            "VALIDATION_ERROR",
            "request validation failed",
            details={"errors": jsonable_encoder(exc.errors())},
        )
        return JSONResponse(status_code=422, content=payload)

    @app.exception_handler(Exception)
    async def internal_error_handler(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=_error_payload("INTERNAL_SERVER_ERROR", "internal server error"),
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "quantlab"}

    @app.get("/api/settings/raw-root")
    def raw_root_get() -> dict[str, object]:
        return {"raw_root": settings_service.raw_path()}

    @app.post("/api/settings/raw-root")
    async def raw_root_update(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        if not isinstance(body, dict) or not body.get("path"):
            raise HTTPException(status_code=400, detail=_error_payload("RAW_ROOT_INVALID", "path is required"))
        try:
            return settings_service.update_raw_root(str(body["path"]))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_error_payload("RAW_ROOT_INVALID", str(error))) from error

    @app.get("/api/settings/tushare-token")
    def tushare_token_status() -> dict[str, object]:
        return {"configured": settings_service.token_configured()}

    @app.post("/api/settings/tushare-token")
    async def tushare_token_update(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail=_error_payload("TUSHARE_TOKEN_INVALID", "token is required"))
        token = str(body.get("token") or "").strip()
        return settings_service.update_tushare_token(token)

    @app.get("/api/settings")
    def get_settings() -> dict[str, object]:
        return settings_service.public()

    @app.put("/api/settings")
    async def update_settings(request: Request) -> dict[str, object]:
        try:
            body = await request.json()
            return settings_service.update(body if isinstance(body, dict) else {})
        except ValueError as error:
            raise HTTPException(status_code=400, detail={"error_code": "SETTINGS_INVALID", "message": str(error), "details": {}}) from error

    @app.post("/api/settings/reset")
    def reset_settings() -> dict[str, object]:
        return settings_service.reset()

    @app.post("/api/settings/test-connection")
    def test_settings_connection() -> dict[str, object]:
        value = settings_service.public()
        return {"ok": True, "host": value["environment"]["host"], "roots": value["paths"]}

    @app.post("/api/settings/scan")
    def scan_settings() -> dict[str, object]:
        return settings_service.scan()

    @app.get("/api/datasets")
    def datasets(
        category: str | None = None,
        status: str | None = None,
        quality_status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        q: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ) -> dict[str, object]:
        return catalog.filtered_items(
            category=category, status=status, quality_status=quality_status,
            date_from=date_from, date_to=date_to, query=q, page=page, page_size=page_size,
        )

    @app.post("/api/raw/download/index_basic")
    def raw_download_index_basic() -> dict[str, object]:
        try:
            return tushare_download.download_index_basic()
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_error_payload("TUSHARE_DOWNLOAD_FAILED", str(error))) from error

    @app.post("/api/raw/download/index_weight")
    def raw_download_index_weight(body: dict[str, object] = Body(...)) -> dict[str, object]:
        try:
            index_code = str(body.get("index_code") or "").strip()
            start_date = str(body.get("start_date") or "").strip()
            end_date = str(body.get("end_date") or "").strip()
            if not index_code or not start_date or not end_date:
                raise ValueError("index_code、start_date、end_date 均为必填")
            return tushare_download.download_index_weight(index_code, start_date, end_date)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_error_payload("TUSHARE_DOWNLOAD_FAILED", str(error))) from error

    @app.post("/api/raw/download/suspend_d")
    def raw_download_suspend_d(body: dict[str, object] = Body(...)) -> dict[str, object]:
        try:
            start_date = str(body.get("start_date") or "").strip()
            end_date = str(body.get("end_date") or "").strip()
            if not start_date or not end_date:
                raise ValueError("start_date、end_date 均为必填")
            return tushare_download.download_suspend_d(start_date, end_date)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_error_payload("TUSHARE_DOWNLOAD_FAILED", str(error))) from error

    @app.post("/api/raw/apply/suspend_d")
    def raw_apply_suspend_d() -> dict[str, object]:
        try:
            with resolved_database.connect() as connection:
                row = connection.execute(
                    "SELECT path FROM dataset_versions "
                    "WHERE entity_id='ds_canonical_market' AND version_id='current'"
                ).fetchone()
            canonical = (
                Path(row["path"])
                if row and row["path"]
                else resolved_settings.data_root / "canonical.parquet"
            )
            suspend = resolved_settings.raw_root / "suspend_d" / "suspend_d.parquet"
            return apply_suspend_d(canonical_path=canonical, suspend_path=suspend)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=_error_payload("CANONICAL_APPLY_FAILED", str(error)),
            ) from error

    @app.get("/api/raw/{interface_name}/files")
    def raw_interface_files(
        interface_name: str,
        limit: int = Query(20, ge=1, le=200),
    ) -> dict[str, object]:
        try:
            return catalog.raw_interface_files(interface_name=interface_name, limit=limit)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=_error_payload("RAW_INTERFACE_NOT_FOUND", str(error))) from error

    @app.get("/api/datasets/raw")
    def raw_datasets(
        category: str | None = None,
        status: str | None = None,
        quality_status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        q: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ) -> dict[str, object]:
        raw = catalog.raw_items()
        items = []
        query = (q or "").strip().lower()
        for item in raw:
            if category and item.get("category") != category:
                continue
            if quality_status and item.get("quality_status") != quality_status:
                continue
            if date_from and item.get("date_max") and item["date_max"] < date_from:
                continue
            if date_to and item.get("date_min") and item["date_min"] > date_to:
                continue
            if query and query not in str(item.get("name", "")).lower() and query not in str(item.get("entity_id", "")).lower():
                continue
            items.append(item)
        total = len(items)
        start = (page - 1) * page_size
        return {"items": items[start:start + page_size], "total": total, "page": page, "page_size": page_size, "pages": max(1, (total + page_size - 1) // page_size)}

    @app.get("/api/datasets/quality-alerts")
    def dataset_quality_alerts() -> list[dict[str, object]]:
        return catalog.quality_alerts()

    @app.get("/api/datasets/quality-summary")
    def dataset_quality_summary() -> dict[str, object]:
        return catalog.quality_summary()

    @app.post("/api/datasets/rescan")
    def rescan_datasets() -> dict[str, object]:
        return catalog.rescan()

    @app.get("/api/datasets/scan-audits")
    def dataset_scan_audits() -> list[dict[str, object]]:
        return catalog.scan_audits()

    @app.get("/api/datasets/{entity_id}")
    def dataset(entity_id: str) -> dict[str, object]:
        item = catalog.get_public(entity_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail={"error_code": "DATASET_NOT_FOUND", "message": "dataset not found", "entity_id": entity_id, "details": {}},
            )
        return item

    @app.get("/api/datasets/{entity_id}/versions")
    def dataset_versions(entity_id: str) -> list[dict[str, object]]:
        versions = catalog.versions(entity_id)
        if not versions:
            raise HTTPException(
                status_code=404,
                detail={"error_code": "DATASET_NOT_FOUND", "message": "dataset not found", "entity_id": entity_id, "details": {}},
            )
        return versions

    @app.get("/api/datasets/{entity_id}/versions/{version_id}")
    def dataset_version(entity_id: str, version_id: str) -> dict[str, object]:
        item = catalog.get_public(entity_id, version_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail={"error_code": "DATASET_VERSION_NOT_FOUND", "message": "dataset version not found", "entity_id": entity_id, "details": {"version_id": version_id}},
            )
        return item

    @app.get("/api/artifacts/{artifact_id}/download")
    def artifact_download(artifact_id: str) -> FileResponse:
        path = artifacts.get_download_path(artifact_id)
        if path is None:
            raise HTTPException(
                status_code=404,
                detail={"error_code": "ARTIFACT_NOT_FOUND", "message": "artifact not found", "entity_id": artifact_id, "details": {}},
            )
        return FileResponse(path, filename=path.name)

    @app.get("/api/artifacts/{artifact_id}")
    def artifact(artifact_id: str) -> dict[str, object]:
        item = artifacts.get(artifact_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail={"error_code": "ARTIFACT_NOT_FOUND", "message": "artifact not found", "entity_id": artifact_id, "details": {}},
            )
        return item

    @app.get("/api/kline/query")
    def kline_query(
        ts_code: str | None = Query(None, alias="ts_code"),
        date_from: str | None = None,
        date_to: str | None = None,
        version_id: str = Query(DEFAULT_KLINE_VERSION),
        mode: Literal["raw", "hfq"] = "raw",
        fields: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(100, ge=1, le=500),
        max_rows: int = Query(5000, ge=1, le=5000),
        downsample: int | None = Query(None, ge=2, le=200),
        tail: bool = Query(False),
    ) -> dict[str, object]:
        try:
            return kline.query(
                **_kline_params(
                    ts_code=ts_code,
                    date_from=date_from,
                    date_to=date_to,
                    version_id=version_id,
                    mode=mode,
                    fields=fields,
                ),
                page=page,
                page_size=page_size,
                max_rows=max_rows,
                downsample=downsample,
                tail=tail,
            )
        except ValueError as error:
            _raise_kline_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/kline/summary")
    def kline_summary(
        ts_code: str | None = Query(None, alias="ts_code"),
        date_from: str | None = None,
        date_to: str | None = None,
        version_id: str = Query(DEFAULT_KLINE_VERSION),
    ) -> dict[str, object]:
        try:
            return kline.summary(
                symbols=ts_code,
                date_from=date_from,
                date_to=date_to,
                version_id=version_id,
            )
        except ValueError as error:
            _raise_kline_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/kline/quality")
    def kline_quality(
        version_id: str = Query(DEFAULT_KLINE_VERSION),
    ) -> dict[str, object]:
        try:
            return kline.quality(version_id=version_id)
        except ValueError as error:
            _raise_kline_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/kline/export.csv")
    def kline_export(
        ts_code: str | None = Query(None, alias="ts_code"),
        date_from: str | None = None,
        date_to: str | None = None,
        version_id: str = Query(DEFAULT_KLINE_VERSION),
        mode: Literal["raw", "hfq"] = "raw",
        fields: str | None = None,
        max_rows: int = Query(5000, ge=1, le=5000),
    ) -> Response:
        try:
            csv_text = kline.csv_text(
                **_kline_params(
                    ts_code=ts_code,
                    date_from=date_from,
                    date_to=date_to,
                    version_id=version_id,
                    mode=mode,
                    fields=fields,
                ),
                max_rows=max_rows,
            )
        except ValueError as error:
            _raise_kline_error(error)
        return Response(
            content=csv_text,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=kline-export.csv"},
        )

    @app.get("/api/factor-data/catalog")
    def factor_catalog() -> list[dict[str, object]]:
        try:
            items = factor_data.catalog()
            for item in items:
                latest = factor_calculation.latest(factor_id=str(item["factor_id"]))
                item["latest_calculation"] = latest
            return items
        except ValueError as error:
            _raise_factor_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factor-calculations", status_code=201)
    async def factor_calculation_create(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail=_error_payload("FACTOR_CALCULATION_INVALID", "request body is required"))
        try:
            return factor_calculation.run(
                str(body.get("factor_id", "")),
                version_id=str(body.get("version_id") or "v1"),
                date_from=body.get("date_from"),
                date_to=body.get("date_to"),
                markets=body.get("markets"),
            )
        except ValueError as error:
            status_code = 404 if any(token in str(error) for token in ("not found", "unknown factor", "找不到")) else 400
            raise HTTPException(status_code=status_code, detail=_error_payload("FACTOR_CALCULATION_INVALID", str(error))) from error

    @app.get("/api/factor-calculations")
    def factor_calculation_list(
        factor_id: str | None = None,
        version_id: str | None = None,
    ) -> dict[str, object]:
        return factor_calculation.list(factor_id=factor_id, version_id=version_id)

    @app.get("/api/factor-calculations/latest/{factor_id}")
    def factor_calculation_latest(factor_id: str) -> dict[str, object]:
        item = factor_calculation.latest(factor_id=factor_id)
        if item is None:
            raise HTTPException(status_code=404, detail=_error_payload("FACTOR_CALCULATION_NOT_FOUND", "factor calculation not found", entity_id=factor_id))
        return item

    @app.get("/api/factor-calculations/{calculation_id}")
    def factor_calculation_detail(calculation_id: int) -> dict[str, object]:
        item = factor_calculation.get(calculation_id)
        if item is None:
            raise HTTPException(status_code=404, detail=_error_payload("FACTOR_CALCULATION_NOT_FOUND", "factor calculation not found"))
        return item

    @app.get("/api/factor-data/factors/{factor_id}")
    def factor_detail(factor_id: str) -> dict[str, object]:
        try:
            items = factor_data.catalog()
            item = next((entry for entry in items if entry["factor_id"] == factor_id), None)
            if item is None:
                raise ValueError(f"unknown factor: {factor_id}")
            item["latest_calculation"] = factor_calculation.latest(factor_id=factor_id)
            return item
        except ValueError as error:
            _raise_factor_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/factor-data/query")
    def factor_query(
        factor: str,
        ts_code: str | None = Query(None, alias="ts_code"),
        date_from: str | None = None,
        date_to: str | None = None,
        version_id: str = Query("v1"),
        fields: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(100, ge=1, le=500),
        max_rows: int = Query(5000, ge=1, le=FACTOR_MAX_ROWS),
    ) -> dict[str, object]:
        try:
            return factor_data.query(
                factor=factor,
                symbols=ts_code,
                date_from=date_from,
                date_to=date_to,
                version_id=version_id,
                fields=fields,
                page=page,
                page_size=page_size,
                max_rows=max_rows,
            )
        except ValueError as error:
            _raise_factor_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/factor-data/summary")
    def factor_summary(
        factor: str,
        ts_code: str | None = Query(None, alias="ts_code"),
        date_from: str | None = None,
        date_to: str | None = None,
        version_id: str = Query("v1"),
    ) -> dict[str, object]:
        try:
            return factor_data.summary(
                factor=factor,
                symbols=ts_code,
                date_from=date_from,
                date_to=date_to,
                version_id=version_id,
            )
        except ValueError as error:
            _raise_factor_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/factor-data/quality")
    def factor_quality(version_id: str = Query("v1")) -> dict[str, object]:
        try:
            return factor_data.quality(version_id=version_id)
        except ValueError as error:
            _raise_factor_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/factor-data/export.csv")
    def factor_export(
        factor: str,
        ts_code: str | None = Query(None, alias="ts_code"),
        date_from: str | None = None,
        date_to: str | None = None,
        version_id: str = Query("v1"),
        max_rows: int = Query(5000, ge=1, le=FACTOR_MAX_ROWS),
    ) -> Response:
        try:
            content = factor_data.csv_text(
                factor=factor,
                symbols=ts_code,
                date_from=date_from,
                date_to=date_to,
                version_id=version_id,
                max_rows=max_rows,
            )
        except ValueError as error:
            _raise_factor_error(error)
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={factor}-sample.csv"},
        )

    @app.get("/api/factors")
    def factor_library_list(
        q: str | None = None,
        category: str | None = None,
        source: str | None = None,
        lifecycle: str | None = None,
        quality: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=100),
    ) -> dict[str, object]:
        try:
            return factors.list(
                query=q, category=category, source=source, lifecycle=lifecycle,
                quality=quality, page=page, page_size=page_size,
            )
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/factors/{entity_id}/{version_id}")
    def factor_library_detail(entity_id: str, version_id: str) -> dict[str, object]:
        try:
            item = factors.get(entity_id, version_id)
        except ValueError as error:
            _raise_factor_library_error(error)
        if item is None:
            _raise_factor_library_error(ValueError("FactorVersion not found"), status_code=404)
        return item

    @app.get("/api/factors/{factor_id}/versions/{version_id}")
    def factor_version_detail(factor_id: str, version_id: str) -> dict[str, object]:
        try:
            return factor_detail_service.get(factor_id, version_id)
        except ValueError as error:
            _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
        raise AssertionError("unreachable")

    @app.get("/api/factor-packs/canonical")
    def factor_pack_canonical() -> dict[str, object]:
        return factor_pack_service.catalog()

    @app.post("/api/factor-packs/canonical/ingest")
    async def factor_pack_ingest(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        if not isinstance(body, dict):
            body = {}
        try:
            field = body.get("field")
            return factor_pack_service.ingest(
                dataset_id=str(body.get("dataset_id") or "ds_canonical_market"),
                dataset_version_id=str(body.get("dataset_version_id") or "current"),
                field=None if field in (None, "") else str(field),
            )
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factor-drafts/preview")
    async def factor_draft_preview(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return factor_manual_service.preview(body)  # type: ignore[arg-type]
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/factor-drafts/fields")
    def factor_draft_fields(dataset_id: str, dataset_version_id: str) -> dict[str, object]:
        try:
            return factor_manual_service.fields(dataset_id, dataset_version_id)
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factor-drafts", status_code=201)
    async def factor_draft_create(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return factor_manual_service.create_draft(body)  # type: ignore[arg-type]
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factor-drafts/{entity_id}/{version_id}/diagnose")
    def factor_draft_diagnose(
        entity_id: str,
        version_id: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, object]:
        try:
            return factor_manual_service.diagnose(
                entity_id, version_id, date_from=date_from, date_to=date_to
            )
        except ValueError as error:
            _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
        raise AssertionError("unreachable")

    @app.patch("/api/factor-drafts/{entity_id}/{version_id}")
    async def factor_draft_update(entity_id: str, version_id: str, request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return factor_manual_service.update_draft(entity_id, version_id, body)  # type: ignore[arg-type]
        except ValueError as error:
            _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
        raise AssertionError("unreachable")

    @app.post("/api/factor-drafts/{entity_id}/{version_id}/publish")
    def factor_draft_publish(entity_id: str, version_id: str) -> dict[str, object]:
        try:
            return factor_manual_service.publish(entity_id, version_id)
        except ValueError as error:
            _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
        raise AssertionError("unreachable")

    @app.patch("/api/factors/{factor_id}")
    def factor_rename(factor_id: str, body: dict[str, object] = Body(...)) -> dict[str, object]:
        name = body.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail=_error_payload("FACTOR_LIBRARY_INVALID", "请填写新名称。"))
        try:
            return factors.rename(factor_id, name.strip())
        except ValueError as error:
            message = str(error)
            status_code = 404 if "not found" in message.lower() or "找不到" in message else 400
            _raise_factor_library_error(error, status_code=status_code)
        raise AssertionError("unreachable")

    @app.post("/api/factors/{factor_id}/versions/{version_id}/copy", status_code=201)
    def factor_version_copy(factor_id: str, version_id: str) -> dict[str, object]:
        try:
            return factor_detail_service.copy_version(factor_id, version_id)
        except ValueError as error:
            _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
        raise AssertionError("unreachable")

    @app.post("/api/factors/{factor_id}/versions/{version_id}/diagnose")
    def factor_version_diagnose(factor_id: str, version_id: str) -> dict[str, object]:
        try:
            return factor_detail_service.diagnose(factor_id, version_id)
        except ValueError as error:
            _raise_factor_library_error(error, status_code=404 if "not found" in str(error) else 400)
        raise AssertionError("unreachable")

    @app.post("/api/backtest-drafts")
    async def backtest_draft_create(request: Request, response: Response) -> dict[str, object]:
        body = await _json_body(request)
        try:
            if isinstance(body, dict):
                items = body.get("factor_version_ids")
                draft_id = body.get("draft_id")
                if draft_id is not None:
                    return factor_detail_service.update_backtest_draft(
                        str(draft_id), items, expected_revision=body.get("revision")
                    )
            else:
                items = body
            response.status_code = 201
            return factor_detail_service.create_backtest_draft(items)
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factor-mining/runs", status_code=201)
    async def factor_mining_create(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return factor_mining_service.run(body)
        except ValueError as error:
            raise HTTPException(status_code=400, detail={"error_code": "FACTOR_MINING_INVALID", "message": str(error), "entity_id": None, "details": {}}) from error

    @app.post("/api/factor-mining/jobs", status_code=201)
    async def factor_mining_job_create(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return factor_mining_service.create_job(body)
        except ValueError as error:
            raise HTTPException(status_code=400, detail={"error_code": "FACTOR_MINING_INVALID", "message": str(error), "entity_id": None, "details": {}}) from error

    @app.post("/api/factor-mining/jobs/{run_id}/checkpoint")
    async def factor_mining_job_checkpoint(run_id: str, request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return factor_mining_service.checkpoint(run_id, body)
        except ValueError as error:
            raise HTTPException(status_code=400, detail={"error_code": "FACTOR_MINING_JOB_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error

    @app.post("/api/factor-mining/jobs/{run_id}/cancel")
    def factor_mining_job_cancel(run_id: str) -> dict[str, object]:
        try:
            return factor_mining_service.cancel(run_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail={"error_code": "FACTOR_MINING_JOB_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error

    @app.post("/api/factor-mining/jobs/{run_id}/resume", status_code=201)
    def factor_mining_job_resume(run_id: str) -> dict[str, object]:
        try:
            return factor_mining_service.resume(run_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail={"error_code": "FACTOR_MINING_JOB_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error

    @app.get("/api/factor-jobs")
    def factor_jobs_list(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ) -> dict[str, object]:
        try:
            return factor_mining_service.list_jobs(page=page, page_size=page_size)
        except ValueError as error:
            raise HTTPException(status_code=400, detail={"error_code": "FACTOR_JOB_INVALID", "message": str(error), "entity_id": None, "details": {}}) from error

    @app.get("/api/factor-jobs/{run_id}")
    def factor_job_detail(run_id: str) -> dict[str, object]:
        try:
            return factor_mining_service.job_detail(run_id)
        except ValueError as error:
            status_code = 404 if "not found" in str(error) else 400
            raise HTTPException(status_code=status_code, detail={"error_code": "FACTOR_JOB_NOT_FOUND" if status_code == 404 else "FACTOR_JOB_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error

    @app.post("/api/factor-jobs/{run_id}/enable")
    async def factor_job_enable(run_id: str, request: Request) -> dict[str, object]:
        body = await _json_body(request)
        candidate_ids = body.get("candidate_ids") if isinstance(body, dict) else None
        try:
            return factor_mining_service.enable(run_id, candidate_ids)
        except ValueError as error:
            status_code = 404 if "not found" in str(error) else 400
            raise HTTPException(status_code=status_code, detail={"error_code": "FACTOR_JOB_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error

    @app.get("/api/factor-mining/runs/{run_id}")
    def factor_mining_detail(run_id: str) -> dict[str, object]:
        try:
            return factor_mining_service.detail(run_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail={"error_code": "FACTOR_MINING_NOT_FOUND", "message": str(error), "entity_id": run_id, "details": {}}) from error

    @app.get("/api/factor-mining/runs/{run_id}/candidates/{dedupe_key}")
    def factor_mining_candidate_detail(run_id: str, dedupe_key: str) -> dict[str, object]:
        try:
            detail = factor_mining_service.detail(run_id)
            for candidate in detail["candidates"]:
                if candidate.get("dedupe_key") == dedupe_key:
                    return candidate
            raise ValueError("candidate not found or was rejected")
        except ValueError as error:
            raise HTTPException(status_code=404, detail={"error_code": "FACTOR_MINING_CANDIDATE_NOT_FOUND", "message": str(error), "entity_id": run_id, "details": {}}) from error

    def _strategy_error(error: ValueError, status_code: int = 400) -> None:
        raise HTTPException(status_code=status_code, detail={"error_code": "STRATEGY_CENTER_INVALID", "message": str(error), "entity_id": None, "details": {}}) from error

    @app.get("/api/models")
    def model_list() -> dict[str, object]:
        return strategy_center.list_models()

    @app.post("/api/models", status_code=201)
    async def model_create(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return strategy_center.create_model(str(body.get("entity_id", "")), str(body.get("name", "")))  # type: ignore[union-attr]
        except (AttributeError, ValueError) as error:
            _strategy_error(ValueError(str(error)))
        raise AssertionError("unreachable")

    @app.get("/api/models/kinds")
    def model_kinds() -> dict[str, object]:
        return model_training.list_kinds()

    @app.post("/api/models/design", status_code=201)
    async def model_design(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return model_training.create_design(body)  # type: ignore[arg-type]
        except ValueError as error:
            _strategy_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/models/catalog")
    def model_catalog_ensure() -> dict[str, object]:
        try:
            return model_training.ensure_catalog()
        except ValueError as error:
            _strategy_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/models/{entity_id}/versions/{version_id}")
    def model_version_detail(entity_id: str, version_id: str) -> dict[str, object]:
        item = strategy_center.get_model_version(entity_id, version_id)
        if item is None:
            _strategy_error(ValueError("model version not found"), 404)
        return item

    @app.post("/api/models/{entity_id}/versions", status_code=201)
    async def model_version_create(entity_id: str, request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return strategy_center.create_model_version(entity_id, body)  # type: ignore[arg-type]
        except ValueError as error:
            _strategy_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/models/{entity_id}/versions/{version_id}/publish")
    def model_version_publish(entity_id: str, version_id: str) -> dict[str, object]:
        try:
            return strategy_center.publish_model_version(entity_id, version_id)
        except ValueError as error:
            _strategy_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/models/runs")
    def model_training_runs() -> list[dict[str, object]]:
        return strategy_center.list_training_runs()

    @app.get("/api/models/runs/{run_id}")
    def model_training_run_detail(run_id: str) -> dict[str, object]:
        item = strategy_center.get_training_run(run_id)
        if item is None:
            _strategy_error(ValueError("training run not found"), 404)
        return item

    @app.post("/api/models/{entity_id}/versions/{version_id}/training-runs", status_code=201)
    async def model_training_run_create(entity_id: str, version_id: str, request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            config = body.get("config", body) if isinstance(body, dict) else {}
            return strategy_center.create_training_run(entity_id, version_id, config)  # type: ignore[arg-type]
        except ValueError as error:
            _strategy_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/models/runs/{run_id}/status")
    async def model_training_run_status(run_id: str, request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return strategy_center.transition_training_run(run_id, str(body.get("status", "")))  # type: ignore[union-attr]
        except (AttributeError, ValueError) as error:
            _strategy_error(ValueError(str(error)))
        raise AssertionError("unreachable")

    @app.post("/api/models/runs/{run_id}/artifacts")
    async def model_training_run_artifact(run_id: str, request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return strategy_center.attach_training_artifact(run_id, Path(str(body.get("path", ""))), str(body.get("display_name", "模型Artifact")))  # type: ignore[union-attr]
        except (AttributeError, ValueError, FileNotFoundError) as error:
            _strategy_error(ValueError(str(error)))
        raise AssertionError("unreachable")

    @app.get("/api/strategies")
    def strategy_list() -> dict[str, object]:
        return strategy_center.list_strategies()

    @app.post("/api/strategies", status_code=201)
    async def strategy_create(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return strategy_center.create_strategy(str(body.get("entity_id", "")), str(body.get("name", "")))  # type: ignore[union-attr]
        except (AttributeError, ValueError) as error:
            _strategy_error(ValueError(str(error)))
        raise AssertionError("unreachable")

    @app.get("/api/strategies/{entity_id}/versions/{version_id}")
    def strategy_version_detail(entity_id: str, version_id: str) -> dict[str, object]:
        item = strategy_center.get_strategy_version(entity_id, version_id)
        if item is None:
            _strategy_error(ValueError("strategy version not found"), 404)
        return item

    @app.post("/api/strategies/{entity_id}/versions", status_code=201)
    async def strategy_version_create(entity_id: str, request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return strategy_center.create_strategy_version(entity_id, body)  # type: ignore[arg-type]
        except ValueError as error:
            _strategy_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/strategies/{entity_id}/versions/{version_id}/publish")
    def strategy_version_publish(entity_id: str, version_id: str) -> dict[str, object]:
        try:
            return strategy_center.publish_strategy_version(entity_id, version_id)
        except ValueError as error:
            _strategy_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/strategies/{entity_id}/versions/{version_id}/copy", status_code=201)
    def strategy_version_copy(entity_id: str, version_id: str) -> dict[str, object]:
        try:
            return strategy_center.copy_strategy_version(entity_id, version_id)
        except ValueError as error:
            _strategy_error(error, 404 if "not found" in str(error) else 400)
        raise AssertionError("unreachable")

    @app.get("/api/strategies/{entity_id}/versions/{left}/diff/{right}")
    def strategy_version_diff(entity_id: str, left: str, right: str) -> dict[str, object]:
        try:
            return strategy_center.diff_strategy_versions(entity_id, left, right)
        except ValueError as error:
            _strategy_error(error, 404)
        raise AssertionError("unreachable")

    @app.post("/api/strategies/{entity_id}/versions/{version_id}/backtest-draft")
    def strategy_to_backtest_draft(entity_id: str, version_id: str) -> dict[str, object]:
        try:
            return strategy_center.to_backtest_draft(entity_id, version_id)
        except ValueError as error:
            _strategy_error(error, 404)
        raise AssertionError("unreachable")

    @app.post("/api/factor-mining/runs/{run_id}/candidates/{dedupe_key}/draft")
    def factor_mining_candidate_draft(run_id: str, dedupe_key: str) -> dict[str, object]:
        try:
            return factor_mining_service.candidate_draft(run_id, dedupe_key)
        except ValueError as error:
            status_code = 404 if "not found" in str(error) else 400
            raise HTTPException(status_code=status_code, detail={"error_code": "FACTOR_MINING_CANDIDATE_NOT_FOUND" if status_code == 404 else "FACTOR_MINING_INVALID", "message": str(error), "entity_id": run_id, "details": {}}) from error

    @app.get("/api/backtest-drafts/{draft_id}")
    def backtest_draft_detail(draft_id: str) -> dict[str, object]:
        try:
            return factor_detail_service.get_backtest_draft(draft_id)
        except ValueError as error:
            _raise_factor_library_error(error, status_code=404)

    def _plan_error(error: Exception, plan_id: str | None = None) -> None:
        message = str(error)
        if isinstance(error, RuntimeError) and message == "busy":
            raise HTTPException(
                status_code=409,
                detail=_error_payload("BACKTEST_PLAN_BUSY", "回测计划正在运行，请等当前批次结束或先停止。", entity_id=plan_id),
            ) from error
        if isinstance(error, ValueError) and message == "empty":
            raise HTTPException(
                status_code=400,
                detail=_error_payload("BACKTEST_PLAN_EMPTY", "没有可运行的任务。已完成的不会重跑，请勾选待运行项。", entity_id=plan_id),
            ) from error
        if isinstance(error, ValueError) and message == "closed":
            raise HTTPException(
                status_code=400,
                detail=_error_payload("BACKTEST_PLAN_CLOSED", "计划已完结，不能再改或开跑。", entity_id=plan_id),
            ) from error
        if isinstance(error, ValueError) and message == "not_empty":
            raise HTTPException(
                status_code=400,
                detail=_error_payload("BACKTEST_PLAN_NOT_EMPTY", "计划中还有任务，不能删除。", entity_id=plan_id),
            ) from error
        if isinstance(error, ValueError) and message == "not_selected":
            raise HTTPException(
                status_code=400,
                detail=_error_payload("BACKTEST_PLAN_NOTHING_SELECTED", "没有要删除的任务。请先勾选。", entity_id=plan_id),
            ) from error
        missing = "not found" in message.lower() or "找不到" in message
        status_code = 404 if missing else 400
        code = "BACKTEST_PLAN_NOT_FOUND" if missing else "BACKTEST_PLAN_INVALID"
        raise HTTPException(status_code=status_code, detail=_error_payload(code, message, entity_id=plan_id)) from error

    @app.get("/api/backtest-plans")
    def backtest_plan_list(open_only: bool = Query(False, alias="open")) -> dict[str, object]:
        return backtest_plan.list_plans(open_only=open_only)

    @app.post("/api/backtest-plans", status_code=201)
    async def backtest_plan_create(request: Request) -> dict[str, object]:
        try:
            body = await _json_body(request)
            if not isinstance(body, dict):
                raise ValueError("plan body is required")
            return backtest_plan.create(body)
        except (RuntimeError, ValueError) as error:
            _plan_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/backtest-plans/{plan_id}")
    def backtest_plan_detail(plan_id: str) -> dict[str, object]:
        try:
            return backtest_plan.get(plan_id)
        except ValueError as error:
            _plan_error(error, plan_id)
        raise AssertionError("unreachable")

    @app.post("/api/backtest-plans/{plan_id}/items", status_code=201)
    async def backtest_plan_add_item(plan_id: str, request: Request) -> dict[str, object]:
        try:
            body = await _json_body(request)
            if not isinstance(body, dict):
                raise ValueError("item body is required")
            return backtest_plan.add_item(plan_id, body)
        except (RuntimeError, ValueError) as error:
            _plan_error(error, plan_id)
        raise AssertionError("unreachable")

    @app.patch("/api/backtest-plans/{plan_id}/items")
    async def backtest_plan_select_items(plan_id: str, request: Request) -> dict[str, object]:
        try:
            body = await _json_body(request)
            if not isinstance(body, dict) or not isinstance(body.get("selected"), dict):
                raise ValueError("selected is required")
            return backtest_plan.set_selected(plan_id, body["selected"])
        except (RuntimeError, ValueError) as error:
            _plan_error(error, plan_id)
        raise AssertionError("unreachable")

    @app.post("/api/backtest-plans/{plan_id}/items/delete")
    async def backtest_plan_delete_items(plan_id: str, request: Request) -> dict[str, object]:
        try:
            body = await _json_body(request)
        except Exception:
            body = {}
        try:
            item_ids = body.get("item_ids") if isinstance(body, dict) else None
            if item_ids is not None and not isinstance(item_ids, list):
                raise ValueError("item_ids must be a list")
            return backtest_plan.delete_items(plan_id, item_ids=item_ids)
        except (RuntimeError, ValueError) as error:
            _plan_error(error, plan_id)
        raise AssertionError("unreachable")

    @app.delete("/api/backtest-plans/{plan_id}/items/{item_id}")
    def backtest_plan_delete_item(plan_id: str, item_id: str) -> dict[str, object]:
        try:
            return backtest_plan.delete_item(plan_id, item_id)
        except (RuntimeError, ValueError) as error:
            _plan_error(error, plan_id)
        raise AssertionError("unreachable")

    @app.post("/api/backtest-plans/{plan_id}/start", status_code=202)
    async def backtest_plan_start(plan_id: str, request: Request) -> dict[str, object]:
        body: object = {}
        try:
            body = await _json_body(request)
        except Exception:
            body = {}
        try:
            item_ids = body.get("item_ids") if isinstance(body, dict) else None
            if item_ids is not None and not isinstance(item_ids, list):
                raise ValueError("item_ids must be a list")
            return backtest_plan.start(plan_id, item_ids=item_ids)
        except (RuntimeError, ValueError) as error:
            _plan_error(error, plan_id)
        raise AssertionError("unreachable")

    @app.post("/api/backtest-plans/{plan_id}/stop")
    def backtest_plan_stop(plan_id: str) -> dict[str, object]:
        try:
            return backtest_plan.stop(plan_id)
        except (RuntimeError, ValueError) as error:
            _plan_error(error, plan_id)
        raise AssertionError("unreachable")

    @app.post("/api/backtest-plans/{plan_id}/close")
    def backtest_plan_close(plan_id: str) -> dict[str, object]:
        try:
            return backtest_plan.close(plan_id)
        except (RuntimeError, ValueError) as error:
            _plan_error(error, plan_id)
        raise AssertionError("unreachable")

    @app.delete("/api/backtest-plans/{plan_id}")
    def backtest_plan_delete(plan_id: str) -> dict[str, object]:
        try:
            return backtest_plan.delete(plan_id)
        except (RuntimeError, ValueError) as error:
            _plan_error(error, plan_id)
        raise AssertionError("unreachable")

    @app.post("/api/backtests/validate")
    async def backtest_validate(request: Request) -> dict[str, object]:
        try:
            body = await _json_body(request)
            return backtest_workbench.validate(body)  # type: ignore[arg-type]
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_CONFIG_INVALID", str(error))) from error

    @app.post("/api/backtests/preview")
    async def backtest_preview(request: Request) -> dict[str, object]:
        try:
            body = await _json_body(request)
            return backtest_workbench.preview(body)  # type: ignore[arg-type]
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_CONFIG_INVALID", str(error))) from error

    @app.post("/api/backtests", status_code=201)
    async def backtest_create(request: Request) -> dict[str, object]:
        try:
            body = await _json_body(request)
            return backtest_workbench.submit(body)  # type: ignore[arg-type]
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_SUBMIT_INVALID", str(error))) from error

    @app.post("/api/backtests/drafts", status_code=201)
    async def backtest_draft_save(request: Request) -> dict[str, object]:
        try:
            body = await _json_body(request)
            if not isinstance(body, dict) or not isinstance(body.get("config"), dict):
                raise ValueError("config is required")
            return backtest_workbench.save_draft(
                body["config"],  # type: ignore[arg-type]
                draft_id=body.get("draft_id"),
                expected_revision=body.get("expected_revision"),
            )
        except ValueError as error:
            status = 404 if "draft not found" in str(error) else 400
            raise HTTPException(status_code=status, detail=_error_payload("BACKTEST_DRAFT_INVALID", str(error))) from error

    @app.get("/api/backtests/drafts/{draft_id}")
    def backtest_workbench_draft_detail(draft_id: str) -> dict[str, object]:
        draft = backtest_workbench.get_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_DRAFT_NOT_FOUND", "backtest draft not found", entity_id=draft_id))
        return draft

    @app.get("/api/backtests/runs")
    def backtest_archive_list(
        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
        status: str | None = None, strategy: str | None = None, q: str | None = None,
        date_from: str | None = None, date_to: str | None = None,
        sort: str = "created_at", order: Literal["asc", "desc"] = "desc",
    ) -> dict[str, object]:
        try:
            return result_archive.list(page=page, page_size=page_size, status=status, strategy=strategy, query=q, date_from=date_from, date_to=date_to, sort=sort, order=order)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_ARCHIVE_INVALID", str(error))) from error

    @app.get("/api/backtests/runs.csv")
    def backtest_archive_export(
        status: str | None = None, strategy: str | None = None, q: str | None = None,
        date_from: str | None = None, date_to: str | None = None,
        sort: str = "created_at", order: Literal["asc", "desc"] = "desc",
    ) -> Response:
        try:
            content = result_archive.csv_text(status=status, strategy=strategy, query=q, date_from=date_from, date_to=date_to, sort=sort, order=order)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_ARCHIVE_INVALID", str(error))) from error
        return Response(content=content, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=backtest-results.csv"})

    @app.get("/api/backtests/runs/{run_id}")
    def backtest_archive_detail(run_id: str) -> dict[str, object]:
        result = result_archive.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_NOT_FOUND", "backtest run not found", entity_id=run_id))
        return result

    @app.post("/api/backtests/runs/{run_id}/copy-config", status_code=201)
    def backtest_archive_copy(run_id: str) -> dict[str, object]:
        try:
            return result_archive.copy_config(run_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_NOT_FOUND", str(error), entity_id=run_id)) from error

    @app.delete("/api/backtests/runs/{run_id}", status_code=204)
    def backtest_archive_delete(run_id: str) -> Response:
        try:
            result_archive.delete(run_id)
        except ValueError as error:
            message = str(error)
            status_code = 404 if "找不到" in message or "not found" in message.lower() else 400
            code = "BACKTEST_NOT_FOUND" if status_code == 404 else "BACKTEST_ARCHIVE_INVALID"
            raise HTTPException(status_code=status_code, detail=_error_payload(code, message, entity_id=run_id)) from error
        return Response(status_code=204)

    @app.get("/api/backtests/{run_id}")
    def backtest_detail(run_id: str) -> dict[str, object]:
        result = backtest_workbench.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_NOT_FOUND", "backtest run not found", entity_id=run_id))
        return result

    @app.post("/api/backtests/{run_id}/execute")
    def backtest_execute(run_id: str) -> dict[str, object]:
        try:
            result = run_isolated(resolved_settings, backtest_job, run_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_error_payload("BACKTEST_EXECUTION_INVALID", str(error), entity_id=run_id)) from error
        if result is None:
            raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_NOT_FOUND", "backtest run not found", entity_id=run_id))
        return result

    @app.post("/api/backtests/{run_id}/stop")
    def backtest_stop(run_id: str) -> dict[str, object]:
        try:
            return stop_run(backtest_job, run_id)
        except ValueError as error:
            message = str(error)
            missing = "not found" in message.lower() or "找不到" in message
            status_code = 404 if missing else 400
            code = "BACKTEST_NOT_FOUND" if missing else "BACKTEST_STOP_INVALID"
            raise HTTPException(status_code=status_code, detail=_error_payload(code, message, entity_id=run_id)) from error

    @app.get("/api/backtests/{run_id}/status")
    def backtest_status(run_id: str) -> dict[str, object]:
        result = backtest_job.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=_error_payload("BACKTEST_NOT_FOUND", "backtest run not found", entity_id=run_id))
        return result
        raise AssertionError("unreachable")

    @app.post("/api/factors", status_code=201)
    async def factor_library_create(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return factors.import_definition(body)  # type: ignore[arg-type]
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factors/import", status_code=201)
    async def factor_library_import(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return factors.import_definition(body)  # type: ignore[arg-type]
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.patch("/api/factors/{entity_id}/{version_id}")
    async def factor_library_update(entity_id: str, version_id: str, request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return factors.update_draft(entity_id, version_id, body)  # type: ignore[arg-type]
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factors/diagnose")
    async def factor_library_diagnose(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return factors.diagnose(body.get("items") if isinstance(body, dict) else body)
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factors/quality-diagnose")
    async def factor_library_quality_diagnose(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            return factors.diagnose(body.get("items") if isinstance(body, dict) else body)
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factors/publish")
    async def factor_library_publish_many(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            items = body.get("items") if isinstance(body, dict) else body
            return factors.publish_many(items)
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factors/deprecate")
    async def factor_library_deprecate_many(request: Request) -> dict[str, object]:
        body = await _json_body(request)
        try:
            items = body.get("items") if isinstance(body, dict) else body
            return factors.deprecate_many(items)
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factors/{entity_id}/{version_id}/publish")
    def factor_library_publish(entity_id: str, version_id: str) -> dict[str, object]:
        try:
            return factors.publish(entity_id, version_id)
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/factors/{entity_id}/{version_id}/deprecate")
    def factor_library_deprecate(entity_id: str, version_id: str) -> dict[str, object]:
        try:
            return factors.deprecate(entity_id, version_id)
        except ValueError as error:
            _raise_factor_library_error(error)
        raise AssertionError("unreachable")

    @app.delete("/api/factors/{entity_id}/{version_id}", status_code=204)
    def factor_library_delete(entity_id: str, version_id: str) -> Response:
        try:
            factors.delete(entity_id, version_id)
        except ValueError as error:
            message = str(error)
            if "not found" in message.lower() or "找不到" in message:
                status_code = 404
            elif "不能删" in message:
                status_code = 409
            else:
                status_code = 400
            _raise_factor_library_error(error, status_code=status_code)
        return Response(status_code=204)

    @app.get("/api/research-runs")
    def research_run_list(
        q: str | None = None,
        research_type: Literal["manual", "automatic"] | None = None,
        status: Literal["queued", "running", "completed", "failed"] | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=100),
    ) -> dict[str, object]:
        try:
            return research_runs.list(
                query=q, research_type=research_type, status=status,
                page=page, page_size=page_size,
            )
        except ValueError as error:
            _raise_research_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/research-runs", status_code=201)
    async def research_run_create(request: Request) -> dict[str, object]:
        body = await request.json()
        if not isinstance(body, dict):
            _raise_research_error(ValueError("research request body must be an object"))
        try:
            return research_runs.create(
                name=body.get("name", ""),
                research_type=body.get("research_type", ""),
                config=body.get("config", {}),
            )
        except ValueError as error:
            _raise_research_error(error)
        raise AssertionError("unreachable")

    @app.get("/api/research-runs/{run_id}")
    def research_run_detail(run_id: str) -> dict[str, object]:
        result = research_runs.get(run_id)
        if result is None:
            _raise_research_error(ValueError("research run not found"))
        return result

    @app.post("/api/research-runs/{run_id}/copy-config", status_code=200)
    async def research_run_copy(run_id: str, request: Request) -> dict[str, object]:
        body = await request.json()
        if not isinstance(body, dict):
            _raise_research_error(ValueError("copy-config request body must be an object"))
        try:
            return research_runs.copy_config(run_id, name=body.get("name"))
        except ValueError as error:
            _raise_research_error(error)
        raise AssertionError("unreachable")

    @app.post("/api/research-runs/{run_id}/status")
    async def research_run_status(run_id: str, request: Request) -> dict[str, object]:
        body = await request.json()
        try:
            return research_runs.transition(run_id, str(body.get("status", "")))
        except ValueError as error:
            _raise_research_error(error)
        raise AssertionError("unreachable")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(pages / "index.html")

    @app.get("/data")
    def data_page() -> FileResponse:
        return FileResponse(pages / "data.html")

    @app.get("/kline")
    def kline_page() -> FileResponse:
        return FileResponse(pages / "kline.html")

    @app.get("/data/factors")
    def factor_data_redirect() -> RedirectResponse:
        return RedirectResponse(url="/factors", status_code=307)

    @app.get("/data/factors/{factor_id}")
    def factor_detail_redirect(factor_id: str) -> RedirectResponse:
        return RedirectResponse(url=f"/factors/{factor_id}", status_code=307)

    @app.get("/factors")
    def factor_catalog_page() -> FileResponse:
        return FileResponse(
            pages / "factors.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/models")
    def models_page() -> FileResponse:
        return FileResponse(pages / "models.html")

    @app.get("/models/{model_id}/versions/{version_id}")
    def model_version_page(model_id: str, version_id: str) -> FileResponse:
        return FileResponse(pages / "model_version_detail.html")

    @app.get("/models/runs/{run_id}")
    def model_run_page(run_id: str) -> FileResponse:
        return FileResponse(pages / "model_run.html")

    @app.get("/strategies")
    def strategies_page() -> RedirectResponse:
        return RedirectResponse(url="/models", status_code=307)

    @app.get("/backtests/new")
    def backtest_workbench_page() -> FileResponse:
        return FileResponse(pages / "backtest_workbench_formal.html")

    @app.get("/backtests/plan")
    def backtest_plan_page() -> FileResponse:
        return FileResponse(pages / "backtest_plan.html")

    @app.get("/backtests/rules")
    def backtest_rules_page() -> FileResponse:
        return FileResponse(pages / "backtest_rules.html")

    @app.get("/backtests/runs")
    def backtest_archive_page() -> FileResponse:
        return FileResponse(pages / "result_archive.html")

    @app.get("/backtests/runs/{run_id}")
    def backtest_archive_run_page(run_id: str) -> FileResponse:
        return FileResponse(pages / "backtest_run_record.html", headers={"Cache-Control": "no-store"})

    @app.get("/strategies/{strategy_id}/versions/{version_id}")
    def strategy_version_page(strategy_id: str, version_id: str) -> RedirectResponse:
        return RedirectResponse(url="/models", status_code=307)

    @app.get("/factors/new")
    @app.get("/factors/new/manual")
    def factor_manual_page() -> FileResponse:
        return FileResponse(pages / "factor_manual.html")

    @app.get("/factors/{factor_id}")
    def factor_catalog_detail_page(factor_id: str) -> FileResponse:
        return FileResponse(
            pages / "factors.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/factors/{factor_id}/versions/{version_id}")
    @app.get("/factors/{factor_id}/{version_id}")
    def legacy_factor_version_redirect(factor_id: str, version_id: str) -> RedirectResponse:
        return RedirectResponse(url=f"/factors/{factor_id}", status_code=307)

    @app.get("/research/factors")
    @app.get("/research/factors/manual")
    @app.get("/research/factors/auto")
    def factor_research_redirect(request: Request) -> RedirectResponse:
        if request.url.path.endswith("/manual"):
            return RedirectResponse(url="/factors/new/manual", status_code=307)
        if request.url.path.endswith("/auto"):
            return RedirectResponse(url="/research/factor-mining", status_code=307)
        return RedirectResponse(url="/factors", status_code=307)

    @app.get("/research/factor-mining")
    def factor_mining_page() -> FileResponse:
        return FileResponse(pages / "factor_mining.html")

    @app.get("/research/factor-jobs")
    def factor_jobs_page() -> FileResponse:
        return FileResponse(pages / "factor_jobs.html")

    @app.get("/research/runs/{run_id}")
    def research_run_page(run_id: str) -> FileResponse:
        return FileResponse(pages / "research_run.html")

    @app.get("/settings")
    def settings_page() -> FileResponse:
        return FileResponse(pages / "settings.html")

    return app
