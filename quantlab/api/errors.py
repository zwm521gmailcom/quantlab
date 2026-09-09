"""Shared HTTP error helpers for the QuantLab API."""

from __future__ import annotations

import json

from fastapi import HTTPException, Request


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


def _strategy_error(error: ValueError, status_code: int = 400) -> None:
    raise HTTPException(status_code=status_code, detail={"error_code": "STRATEGY_CENTER_INVALID", "message": str(error), "entity_id": None, "details": {}}) from error


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
