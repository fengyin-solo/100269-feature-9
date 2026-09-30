"""航食配餐业务规则：按餐食类型×航班座位数×航站楼判定单次配送上限，
叠加配送车辆额度校验——额度不足的不许下达并说明差额，超出配餐阈值的单独告警；
同一航班两次下达同一批次发生冲突时以最近一次为准、前一次作废；
配送结果同步落到配送席待办。台账与配送单共用同一套超限评估口径，不各算一套。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "catering"
REQUIRED_FIELDS = ["配餐编号", "对应航班", "餐食类型"]
# 待确认：数据缺失时挂起，不放行；已作废：同航班同批次被后一次下达覆盖
STATUS_ORDER = ["待确认", "待配送", "配送中", "已送达", "已变更", "已作废"]
ACTION_RULES = {"下达配送": "配送中", "确认送达": "已送达", "变更餐食": "已变更"}
TERMINAL_STATUSES = {"已送达", "已变更", "已作废"}

# 各航站楼配餐标准（分开设置）：单位座位配餐量（份/座位）。
# 单次配送上限 = 航班座位数 × 单位标准；餐食数量超出上限即触发配餐阈值告警。
TERMINAL_MEAL_STANDARD: dict[str, dict[str, float]] = {
    "T1": {"正餐": 1.0, "轻食": 0.6, "特殊餐": 0.3, "儿童餐": 0.5},
    "T2": {"正餐": 1.1, "轻食": 0.7, "特殊餐": 0.3, "儿童餐": 0.5},
    "T3": {"正餐": 1.2, "轻食": 0.8, "特殊餐": 0.4, "儿童餐": 0.6},
}

# 配送车辆额度（份）：额度不足以覆盖餐食数量的不许下达配送，并说明差多少。
DELIVERY_VEHICLE_QUOTA: dict[str, int] = {
    "VEHI-0001": 80,
    "VEHI-0002": 200,
    "VEHI-0003": 60,
}

# 下达配送前必须补齐的数据；任一缺失即按待确认处理，不直接放行。
DELIVERY_REQUIRED = ["对应航班", "批次", "餐食类型", "餐食数量", "航班座位数", "航站楼", "配送车辆"]


def _to_int(value: Any) -> int | None:
    """把餐食数量/航班座位数解析成整数；无法解析视为缺失。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def evaluate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """统一评估一条配餐记录的配送条件。

    台账列表与配送单都调用本函数，保证超限条数口径一致（不各算一套）：
    - cap：按航站楼标准×餐食类型×航班座位数算出的单次配送上限；
    - over_limit：餐食数量超出上限（配餐阈值）即超限，需单独告警；
    - quota_shortfall：配送车辆额度不足以覆盖餐食数量的差额，大于 0 不许下达。
    """
    missing: list[str] = []
    flight = _text(entry.get("对应航班"))
    batch = _text(entry.get("批次"))
    meal_type = _text(entry.get("餐食类型"))
    terminal = _text(entry.get("航站楼"))
    vehicle = _text(entry.get("配送车辆"))
    quantity = _to_int(entry.get("餐食数量"))
    seats = _to_int(entry.get("航班座位数"))

    if not flight:
        missing.append("对应航班")
    if not batch:
        missing.append("批次")
    if not meal_type:
        missing.append("餐食类型")
    if quantity is None:
        missing.append("餐食数量")
    if seats is None:
        missing.append("航班座位数")
    if not terminal:
        missing.append("航站楼")
    elif terminal not in TERMINAL_MEAL_STANDARD:
        missing.append(f"航站楼{terminal}配餐标准")
    if not vehicle:
        missing.append("配送车辆")
    elif vehicle not in DELIVERY_VEHICLE_QUOTA:
        missing.append(f"配送车辆{vehicle}额度")

    cap: int | None = None
    if terminal and terminal in TERMINAL_MEAL_STANDARD and meal_type:
        unit = TERMINAL_MEAL_STANDARD[terminal].get(meal_type)
        if unit is None:
            missing.append(f"{terminal}航站楼{meal_type}标准")
        elif seats is not None:
            cap = round(seats * unit)

    quota = DELIVERY_VEHICLE_QUOTA.get(vehicle) if vehicle else None
    over_limit = cap is not None and quantity is not None and quantity > cap
    over_amount = quantity - cap if (over_limit and quantity is not None and cap is not None) else 0
    quota_shortfall = (
        quantity - quota if (quota is not None and quantity is not None and quantity > quota) else 0
    )
    can_issue = not missing and quota_shortfall <= 0
    return {
        "missing": missing,
        "terminal": terminal,
        "meal_type": meal_type,
        "quantity": quantity,
        "seats": seats,
        "vehicle": vehicle,
        "quota": quota,
        "cap": cap,
        "over_limit": over_limit,
        "over_amount": over_amount,
        "quota_shortfall": quota_shortfall,
        "can_issue": can_issue,
    }


class CateringService:
    def __init__(self) -> None:
        # 配送席待办与超限告警单独留痕，供配送席读取
        self._todos: list[dict[str, Any]] = []
        self._alerts: list[dict[str, Any]] = []

    def _attach_evaluation(self, row: dict[str, Any]) -> dict[str, Any]:
        row["_评估"] = evaluate_entry(row)
        return row

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        over_limit: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._attach_evaluation(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("配餐编号", "")) or keyword in str(row.get("对应航班", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if over_limit is True:
            rows = [row for row in rows if row["_评估"]["over_limit"]]
        elif over_limit is False:
            rows = [row for row in rows if not row["_评估"]["over_limit"]]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def overlimit_summary(self) -> dict[str, Any]:
        """超限汇总：台账与配送单共用同一套评估口径，超限条数以此为准。"""
        rows = [self._attach_evaluation(row) for row in store.rows(MODULE)]
        over_rows = [row for row in rows if row["_评估"]["over_limit"]]
        return {
            "total": len(rows),
            "over_limit_count": len(over_rows),
            "over_limit": over_rows,
            "alerts": list(self._alerts),
        }

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            self._attach_evaluation(entry)
        return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _text(values.get(field))]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in [
            "配餐编号",
            "对应航班",
            "批次",
            "餐食类型",
            "餐食数量",
            "航班座位数",
            "航站楼",
            "配送车辆",
            "配送人员",
            "预计送达",
        ]:
            if field in values:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[1]
        entry["配餐状态"] = STATUS_ORDER[1]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def _add_todo(self, entry: dict[str, Any], item: str, status: str) -> dict[str, Any]:
        """把配送结果落到配送席待办里。"""
        todo = {
            "id": len(self._todos) + 1,
            "配餐编号": entry.get("配餐编号"),
            "对应航班": entry.get("对应航班"),
            "批次": entry.get("批次"),
            "餐食数量": entry.get("餐食数量"),
            "配送车辆": entry.get("配送车辆"),
            "配送人员": entry.get("配送人员"),
            "事项": item,
            "状态": status,
            "写入时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._todos.append(todo)
        return todo

    def list_todos(self) -> list[dict[str, Any]]:
        return list(self._todos)

    def _void_conflicts(self, entry: dict[str, Any]) -> list[str]:
        """同一航班两次下达同一批次：以最近一次为准，前一次作废。"""
        flight = _text(entry.get("对应航班"))
        batch = _text(entry.get("批次"))
        if not flight or not batch:
            return []
        voided: list[str] = []
        for row in store.rows(MODULE):
            if int(row.get("id", 0)) == int(entry.get("id", -1)):
                continue
            if row.get("status") in TERMINAL_STATUSES:
                continue
            if _text(row.get("对应航班")) == flight and _text(row.get("批次")) == batch:
                row["status"] = "已作废"
                row["配餐状态"] = "已作废"
                row["pending"] = False
                row["abnormal"] = False
                voided.append(str(row.get("配餐编号", "")))
        return voided

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str, bool]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"配餐任务 {entry_id} 不存在或已归档", False
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于航食配餐可执行范围", False
        target = ACTION_RULES[action]

        if action == "下达配送":
            ev = evaluate_entry(entry)
            # 配餐数据缺失 → 按待确认处理，不直接放行
            if ev["missing"]:
                entry["status"] = "待确认"
                entry["配餐状态"] = "待确认"
                entry["pending"] = True
                entry["abnormal"] = False
                return entry, "配餐数据缺失，已置为待确认，补齐后再下达：" + "、".join(ev["missing"]), False
            # 配送车辆额度不足以覆盖餐食数量 → 不许下达并说明差额
            if ev["quota_shortfall"] > 0:
                entry["abnormal"] = True
                return (
                    entry,
                    f"配送车辆{ev['vehicle']}额度{ev['quota']}份，餐食数量{ev['quantity']}份，"
                    f"额度不足，还差{ev['quota_shortfall']}份，不许下达配送",
                    False,
                )
            # 同一航班同一批次冲突：以最近一次为准，前一次作废
            voided = self._void_conflicts(entry)
            # 超出配餐阈值 → 单独告警（仍下达，但标记异常并留痕）
            alert_msg = ""
            if ev["over_limit"]:
                entry["abnormal"] = True
                self._alerts.append({
                    "id": len(self._alerts) + 1,
                    "配餐编号": entry.get("配餐编号"),
                    "对应航班": entry.get("对应航班"),
                    "批次": entry.get("批次"),
                    "餐食数量": ev["quantity"],
                    "配送上限": ev["cap"],
                    "超出量": ev["over_amount"],
                    "航站楼": ev["terminal"],
                    "餐食类型": ev["meal_type"],
                    "告警时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                alert_msg = (
                    f"；超限告警：餐食数量{ev['quantity']}份超过{ev['terminal']}航站楼"
                    f"{ev['meal_type']}单次配送上限{ev['cap']}份，超出{ev['over_amount']}份"
                )
            entry["status"] = target
            entry["配餐状态"] = target
            entry["pending"] = target not in TERMINAL_STATUSES
            self._add_todo(entry, "下达配送", target)
            message = f"配餐任务已下达配送（{target}）"
            if voided:
                message += f"；同航班同批次前次已作废：{'、'.join(voided)}"
            message += alert_msg
            return entry, message, True

        # 确认送达 / 变更餐食：正常状态流转
        entry["status"] = target
        entry["配餐状态"] = target
        entry["pending"] = target not in TERMINAL_STATUSES
        entry["abnormal"] = False
        if action == "确认送达":
            self._add_todo(entry, "确认送达", target)
        return entry, f"配餐任务已{action}", True
