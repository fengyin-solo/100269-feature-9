"""航食配餐接口：维护配餐任务，围绕单次配送上限、车辆额度校验、超限告警、
同航班同批次冲突作废与配送席待办提供查询与动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.catering import CateringService, evaluate_entry

router = APIRouter(prefix="/api/catering", tags=["航食配餐"])

service = CateringService()

LIST_FIELDS = ["配餐编号", "对应航班", "批次", "餐食类型", "餐食数量", "航班座位数", "航站楼", "配送车辆", "配送人员", "预计送达", "配餐状态"]
STATUSES = ["待确认", "待配送", "配送中", "已送达", "已变更", "已作废"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按配餐编号或对应航班检索"),
    status: str | None = Query(default=None, description="待确认、待配送、配送中、已送达、已变更、已作废"),
    over_limit: bool | None = Query(default=None, description="仅看超限传 true，仅看正常传 false"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按配餐编号、状态与超限口径过滤航食配餐列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, over_limit=over_limit, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/todos")
def list_todos() -> dict[str, Any]:
    """配送席待办：下达配送与确认送达的结果都会同步落到这里。"""
    return {"module": "catering", "total": len(service.list_todos()), "items": service.list_todos()}


@router.get("/overlimit-summary")
def overlimit_summary() -> dict[str, Any]:
    """超限汇总：台账与配送单共用同一套评估口径，超限条数以此为准，不各算一套。"""
    return service.overlimit_summary()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出航食配餐清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "catering", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条配餐任务明细（含统一评估结果）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"配餐任务 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/delivery-note")
def delivery_note(entry_id: int) -> dict[str, Any]:
    """配送单：与台账共用 evaluate_entry 同一口径读取超限条数。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"配餐任务 {entry_id} 不存在或已归档")
    ev = evaluate_entry(entry)
    return {"配餐编号": entry.get("配餐编号"), "对应航班": entry.get("对应航班"), "批次": entry.get("批次"), "评估": ev}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条配餐任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="配餐任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条配餐任务执行下达配送、确认送达、变更餐食；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message, ok = service.run_action(entry_id, action)
    if not ok:
        return ActionResult(ok=False, message=message, entry=entry)
    return ActionResult(ok=True, message=message, entry=entry)
