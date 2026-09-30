"""HTTP route modules are kept under this package."""

from fastapi import FastAPI

from quantlab.api.routes.backtests import router as backtests_router
from quantlab.api.routes.datasets import router as datasets_router
from quantlab.api.routes.factor_data import router as factor_data_router
from quantlab.api.routes.factor_mining import router as factor_mining_router
from quantlab.api.routes.factors import router as factors_router
from quantlab.api.routes.health import router as health_router
from quantlab.api.routes.kline import router as kline_router
from quantlab.api.routes.lan import router as lan_router
from quantlab.api.routes.models import router as models_router
from quantlab.api.routes.offline_rl import router as offline_rl_router
from quantlab.api.routes.overview import router as overview_router
from quantlab.api.routes.pages import router as pages_router
from quantlab.api.routes.qlib import router as qlib_router
from quantlab.api.routes.research_runs import router as research_runs_router
from quantlab.api.routes.settings import router as settings_router
from quantlab.api.routes.strategies import router as strategies_router


def register_routers(app: FastAPI) -> None:
    app.include_router(overview_router)
    app.include_router(health_router)
    app.include_router(settings_router)
    app.include_router(lan_router)
    app.include_router(datasets_router)
    app.include_router(kline_router)
    app.include_router(factor_data_router)
    app.include_router(factors_router)
    app.include_router(factor_mining_router)
    app.include_router(models_router)
    app.include_router(strategies_router)
    app.include_router(backtests_router)
    app.include_router(offline_rl_router)
    app.include_router(research_runs_router)
    app.include_router(qlib_router)
    app.include_router(pages_router)
