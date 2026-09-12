from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter(tags=["pages"])

_PAGES = Path(__file__).resolve().parents[2] / "web/pages"


@router.get("/")
def index() -> FileResponse:
    return FileResponse(_PAGES / "index.html")


@router.get("/data")
def data_page() -> FileResponse:
    return FileResponse(_PAGES / "data.html")


@router.get("/kline")
def kline_page() -> FileResponse:
    return FileResponse(_PAGES / "kline.html")


@router.get("/data/factors")
def factor_data_redirect() -> RedirectResponse:
    return RedirectResponse(url="/factors", status_code=307)


@router.get("/data/factors/{factor_id}")
def factor_detail_redirect(factor_id: str) -> RedirectResponse:
    return RedirectResponse(url=f"/factors/{factor_id}", status_code=307)


@router.get("/factors")
def factor_catalog_page() -> FileResponse:
    return FileResponse(
        _PAGES / "factors.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/models")
def models_page() -> FileResponse:
    return FileResponse(_PAGES / "models.html")


@router.get("/models/{model_id}/versions/{version_id}")
def model_version_page(model_id: str, version_id: str) -> FileResponse:
    return FileResponse(_PAGES / "model_version_detail.html")


@router.get("/models/runs/{run_id}")
def model_run_page(run_id: str) -> FileResponse:
    return FileResponse(_PAGES / "model_run.html")


@router.get("/strategies")
def strategies_page() -> RedirectResponse:
    return RedirectResponse(url="/models", status_code=307)


@router.get("/backtests/new")
def backtest_workbench_page() -> FileResponse:
    return FileResponse(_PAGES / "backtest_workbench_formal.html")


@router.get("/backtests/plan")
def backtest_plan_page() -> FileResponse:
    return FileResponse(
        _PAGES / "backtest_plan.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/backtests/rules")
def backtest_rules_page() -> FileResponse:
    return FileResponse(_PAGES / "backtest_rules.html")


@router.get("/backtests/runs")
def backtest_archive_page() -> FileResponse:
    return FileResponse(_PAGES / "result_archive.html")


@router.get("/backtests/runs/{run_id}")
def backtest_archive_run_page(run_id: str) -> FileResponse:
    return FileResponse(_PAGES / "backtest_run_record.html", headers={"Cache-Control": "no-store"})


@router.get("/strategies/{strategy_id}/versions/{version_id}")
def strategy_version_page(strategy_id: str, version_id: str) -> RedirectResponse:
    return RedirectResponse(url="/models", status_code=307)


@router.get("/factors/new")
@router.get("/factors/new/manual")
def factor_manual_page() -> FileResponse:
    return FileResponse(_PAGES / "factor_manual.html")


@router.get("/factors/{factor_id}")
def factor_catalog_detail_page(factor_id: str) -> FileResponse:
    return FileResponse(
        _PAGES / "factors.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/factors/{factor_id}/versions/{version_id}")
@router.get("/factors/{factor_id}/{version_id}")
def legacy_factor_version_redirect(factor_id: str, version_id: str) -> RedirectResponse:
    return RedirectResponse(url=f"/factors/{factor_id}", status_code=307)


@router.get("/research/factors")
@router.get("/research/factors/manual")
@router.get("/research/factors/auto")
def factor_research_redirect(request: Request) -> RedirectResponse:
    if request.url.path.endswith("/manual"):
        return RedirectResponse(url="/factors/new/manual", status_code=307)
    if request.url.path.endswith("/auto"):
        return RedirectResponse(url="/research/factor-mining", status_code=307)
    return RedirectResponse(url="/factors", status_code=307)


@router.get("/research/factor-mining")
def factor_mining_page() -> FileResponse:
    return FileResponse(_PAGES / "factor_mining.html")


@router.get("/research/factor-jobs")
def factor_jobs_page() -> FileResponse:
    return FileResponse(_PAGES / "factor_jobs.html")


@router.get("/research/runs/{run_id}")
def research_run_page(run_id: str) -> FileResponse:
    return FileResponse(_PAGES / "research_run.html")


@router.get("/settings")
def settings_page() -> FileResponse:
    return FileResponse(_PAGES / "settings.html")
