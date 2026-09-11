"""FastAPI application factory for the local-only foundation service."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from quantlab import __version__
from quantlab.config import Settings
from quantlab.repositories.artifacts import ArtifactRepository
from quantlab.repositories.database import Database
from quantlab.services.catalog import DatasetCatalog
from quantlab.services.overview import OverviewService
from quantlab.services.kline import KlineQueryService
from quantlab.services.factor_data import FactorDataService
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
from quantlab.services.result_archive import ResultArchiveService
from quantlab.services.result_sync import sync_result_catalog
from quantlab.services.settings import SettingsService
from quantlab.services.lan_peers import PeerRegistry
from quantlab.services.tushare_download import TushareDownloadService
from quantlab.api.errors import _error_payload
from quantlab.api.routes import register_routers
from quantlab.api.static import NoStoreStaticFiles


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_database = database or Database(resolved_settings.database_path)
    resolved_database.initialize()
    sync_result_catalog(resolved_settings, resolved_database)
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
    app = FastAPI(title="QuantLab", version=__version__)
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
    app.state.dataset_catalog = catalog
    app.state.artifact_repository = artifacts
    app.state.model_training_service = model_training
    app.state.settings = resolved_settings
    app.state.database = resolved_database
    app.state.peer_registry = PeerRegistry()
    register_routers(app)
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

    return app
