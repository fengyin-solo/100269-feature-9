"""航食配餐接口。

覆盖五块：配餐台账、下达配送（阈值/额度/数据完整性校验）、配送席待办、
超限告警台账、航站楼标准与车辆额度维护。超限条数统一取
``active_over_limit_count``，台账与配送席读到的是同一个数字。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.catering import (
    CateringService,
    active_over_limit_count,
)

router = APIRouter(prefix="/api/catering", tags=["航食配餐"])

service = CateringService()

LIST_FIELDS = ["配餐编号", "对应航班", "航站楼", "批次号", "餐食类型", "餐食数量", "航班座位数", "配送车辆", "配送人员", "预计送达"]
STATUSES = ["待确认", "待配送", "配送中", "已送达", "已变更", "已作废"]


# ---------- 概览 ----------
@router.get("/summary")
def summary() -> dict[str, Any]:
    """配餐席/配送席共用的统计卡片；超限条数只有这一个出口。"""
    return service.summary()


# ---------- 配餐标准与车辆额度 ----------
@router.get("/standards")
def list_standards() -> dict[str, Any]:
    """各航站楼分开维护的配餐系数标准。"""
    return {"items": service.list_standards()}


@router.post("/standards", response_model=ActionResult)
def upsert_standard(payload: EntryPayload) -> ActionResult:
    """新增或调整某航站楼某餐食类型的系数；不存在的航站楼标准按待确认处理。"""
    values = payload.values
    standard, message = service.upsert_standard(
        str(values.get("航站楼") or ""),
        str(values.get("餐食类型") or ""),
        values.get("餐食系数"),
    )
    return ActionResult(ok=standard is not None, message=message, entry=standard)


@router.get("/quotas")
def list_quotas() -> dict[str, Any]:
    """车辆额度总览：已占用额实时取自未作废的配送中配送单。"""
    return {"items": service.list_quotas()}


@router.post("/quotas", response_model=ActionResult)
def upsert_quota(payload: EntryPayload) -> ActionResult:
    """登记或调整某台配送车辆的单次可用额度（按餐份数）。"""
    values = payload.values
    quota, message = service.upsert_quota(
        str(values.get("配送车辆") or ""),
        values.get("车辆额度"),
    )
    return ActionResult(ok=quota is not None, message=message, entry=quota)


# ---------- 配送席待办 ----------
@router.get("/orders", response_model=PageResult[dict])
def list_orders(
    status: str | None = Query(default=None, description="配送中、已送达、已作废"),
    keyword: str | None = Query(default=None, description="按配送单号或航班检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """配送席待办：下达成功的配送单在这里读取，超限标记与台账同源。"""
    items, total = service.list_orders(status=status, keyword=keyword, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/orders/{order_id}/deliver", response_model=ActionResult)
def deliver_order(order_id: int) -> ActionResult:
    """配送席确认送达：配送单与配餐台账同步落已送达。"""
    order, message = service.deliver_order(order_id)
    if order is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=order)


# ---------- 超限告警 ----------
@router.get("/alerts", response_model=PageResult[dict])
def list_alerts(
    status: str | None = Query(default=None, description="未消除、已消除"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """超限告警台账；未消除条数即台账与配送席共用的超限条数。"""
    items, total = service.list_alerts(status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# ---------- 配餐台账 ----------
@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按配餐编号或航班检索"),
    status: str | None = Query(default=None, description="待确认、待配送、配送中、已送达、已变更、已作废"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按配餐编号/航班与状态过滤配餐台账；没有数据时返回空页，不报错。"""
    if size > 200:
        return PageResult(items=[], total=0, page=page, size=size)
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出航食配餐清单及当前规则口径，台账与配送席的超限条数保持一致。"""
    items, total = service.list_entries(page=1, size=10000)
    orders, order_total = service.list_orders(page=1, size=10000)
    alerts, alert_total = service.list_alerts(page=1, size=10000)
    over_limit = active_over_limit_count()
    return {
        "module": "catering",
        "total": total,
        "items": items,
        "配送单": orders,
        "配送单总数": order_total,
        "告警": alerts,
        "告警总数": alert_total,
        "超限条数": over_limit,
        "配送席超限条数": over_limit,
    }


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条配餐任务明细（含规则校验视图）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"配餐任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条配餐任务；配餐数据缺失的落待确认，绝不直接放行。"""
    entry, problems = service.create_entry(payload.values)
    if problems:
        return ActionResult(ok=False, message=f"登记失败：{'、'.join(problems)}")
    if entry and entry.get("status") == "待确认":
        return ActionResult(
            ok=True,
            message=f"配餐任务已登记，数据缺失按待确认处理：{'、'.join(entry.get('待确认项', []))}",
            entry=entry,
        )
    return ActionResult(ok=True, message="配餐任务已登记，等待下达配送", entry=entry)


@router.post("/{entry_id}/confirm", response_model=ActionResult)
def confirm_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """配餐席补录缺失数据并确认；仍缺字段时继续待确认并逐项说明。"""
    entry, message = service.confirm_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=entry.get("status") != "待确认", message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """下达配送走完整规则校验；确认送达/变更餐食同步处理配送单。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message, allowed = service.run_action(entry_id, action)
    return ActionResult(ok=allowed, message=message, entry=entry)
