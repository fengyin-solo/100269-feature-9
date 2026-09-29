"""航食配餐业务规则。

口径统一说明：
- 单次配送上限按「航站楼 + 餐食类型」取系数，上限 = 航班座位数 × 系数（向下取整），
  各航站楼标准分开维护；标准或配餐数据缺失一律按待确认处理，绝不放行。
- 车辆额度按车维护，占用额实时汇总未作废的「配送中」配送单；额度覆盖不了
  本次餐食数量时不许下达，并在返回信息里说明差多少。
- 超出配餐阈值的本次下达同样拦下，同时在告警台账单独告警；告警与超限条数
  只有 ``active_over_limit_count`` 这一个出口，台账、配送席、概览都读它。
- 同一航班同一批次重复下达时，以最近一次为准：旧配送单作废、额度释放、
  台账回退到待配送，再按新任务校验。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "catering"
ORDER_MODULE = "catering_orders"
QUOTA_MODULE = "catering_vehicle_quota"
STANDARD_MODULE = "catering_standards"
ALERT_MODULE = "catering_alerts"

# 辅助表不计入运营概览的业务模块统计
AUX_MODULES = {ORDER_MODULE, QUOTA_MODULE, STANDARD_MODULE, ALERT_MODULE}

STATUS_ORDER = ["待确认", "待配送", "配送中", "已送达", "已变更", "已作废"]
ACTION_RULES = {"下达配送": "配送中", "确认送达": "已送达", "变更餐食": "已变更"}

# 登记时缺失即落入待确认的字段；配餐编号与对应航班是硬性必填，缺了直接拒收
REQUIRED_FIELDS = ["配餐编号", "对应航班"]
PENDING_FIELDS = ["航站楼", "航班座位数", "餐食类型", "餐食数量", "配送车辆", "配送人员", "预计送达", "批次号"]

ORDER_STATUS_ACTIVE = ["配送中", "已送达"]
ORDER_STATUS_VOID = "已作废"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _to_int(value: Any) -> int | None:
    """座位数、餐食数量只认真实的正整数；空串、None、非法文本都算数据缺失。"""
    if value is None or str(value).strip() == "":
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _paginate(rows: list[dict[str, Any]], page: int, size: int) -> tuple[list[dict[str, Any]], int]:
    total = len(rows)
    start = max(page - 1, 0) * size
    return rows[start:start + size], total


def active_over_limit_count() -> int:
    """超限条数的唯一口径：未消除的超限告警条数。

    台账列表、配送席待办、概览卡片都必须读这里，禁止各算一套。
    """
    return sum(1 for alert in store.rows(ALERT_MODULE) if alert.get("状态") == "未消除")


def active_over_limit_orders() -> set[str]:
    """当前超限涉及的配送单号集合，供配送席标记同一条超限记录。"""
    return {
        str(alert["配送单号"])
        for alert in store.rows(ALERT_MODULE)
        if alert.get("状态") == "未消除" and alert.get("配送单号")
    }


class CateringService:
    # ---------- 列表与明细 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("配餐编号", "")) or keyword in str(row.get("对应航班", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        items, total = _paginate(rows, page, size)
        return [self._with_check_view(row) for row in items], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._with_check_view(entry) if entry else None

    def _with_check_view(self, entry: dict[str, Any]) -> dict[str, Any]:
        """给台账行附一份当前规则校验视图，展示上限/差额，不另起一套计数。"""
        view = dict(entry)
        check = self._evaluate(entry)
        view["规则校验"] = {
            "配餐上限": check["cap"],
            "超限数量": check["over"],
            "车辆剩余额度": check["remaining"],
            "额度差额": check["shortage"],
            "待确认项": check["missing"],
        }
        view["超限"] = check["over"] > 0
        return view

    # ---------- 登记与补录确认 ----------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing_required = [
            field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()
        ]
        if missing_required:
            return None, missing_required

        rows = store.rows(MODULE)
        code = str(values["配餐编号"]).strip()
        if any(str(row.get("配餐编号")) == code for row in rows):
            return None, [f"配餐编号 {code} 已存在，请勿重复登记"]

        entry: dict[str, Any] = {"id": _next_id(rows)}
        for field in ["配餐编号", "对应航班", "航站楼", "餐食类型", "批次号", "配送车辆", "配送人员", "预计送达"]:
            entry[field] = str(values.get(field) or "").strip()
        entry["航班座位数"] = _to_int(values.get("航班座位数"))
        entry["餐食数量"] = _to_int(values.get("餐食数量"))

        pending_fields = self._missing_fields(entry)
        entry["待确认项"] = pending_fields
        entry["status"] = "待确认" if pending_fields else "待配送"
        entry["pending"] = entry["status"] != "已送达"
        entry["abnormal"] = False
        rows.append(entry)
        return self._with_check_view(entry), []

    def confirm_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """配餐席补录缺失数据后确认；数据仍不齐则继续待确认，并说明差什么。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"配餐任务 {entry_id} 不存在或已归档"
        if entry["status"] == "配送中":
            return None, "配餐任务已下达配送，补录请先走变更餐食"
        if entry["status"] in ("已送达", "已变更"):
            return None, f"配餐任务已{entry['status']}，无需再确认"

        for field in ["航站楼", "餐食类型", "批次号", "配送车辆", "配送人员", "预计送达"]:
            if field in values and str(values.get(field) or "").strip():
                entry[field] = str(values[field]).strip()
        if "航班座位数" in values:
            entry["航班座位数"] = _to_int(values.get("航班座位数"))
        if "餐食数量" in values:
            parsed = _to_int(values.get("餐食数量"))
            if parsed is not None:
                entry["餐食数量"] = parsed

        missing = self._missing_fields(entry)
        entry["待确认项"] = missing
        if missing:
            entry["status"] = "待确认"
            entry["pending"] = True
            return self._with_check_view(entry), f"仍有配餐数据待确认：{'、'.join(missing)}"

        # 数据补齐且此前没有被新批次顶替过时，才进入待配送
        if entry["status"] == "已作废":
            return self._with_check_view(entry), "该批次已被同航班的新批次顶替，确认后仍保持作废"
        entry["status"] = "待配送"
        entry["pending"] = True
        return self._with_check_view(entry), "配餐数据已确认，可下达配送"

    def _missing_fields(self, entry: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for field in PENDING_FIELDS:
            value = entry.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(field)
        if not missing:
            # 数据形式齐了，还要看航站楼标准是否存在；标准缺失也算待确认
            if self._find_standard(entry["航站楼"], entry["餐食类型"]) is None:
                missing.append(f"配餐标准（{entry['航站楼']}/{entry['餐食类型']}）")
        return missing

    # ---------- 配餐标准（各航站楼分开） ----------
    def list_standards(self) -> list[dict[str, Any]]:
        return [dict(row) for row in store.rows(STANDARD_MODULE)]

    def upsert_standard(self, terminal: str, meal_type: str, ratio: Any) -> tuple[dict[str, Any] | None, str]:
        terminal = terminal.strip()
        meal_type = meal_type.strip()
        if not terminal or not meal_type:
            return None, "航站楼与餐食类型都不能为空"
        try:
            factor = float(ratio)
        except (TypeError, ValueError):
            return None, "餐食系数必须是数字"
        if factor <= 0:
            return None, "餐食系数必须大于 0"

        rows = store.rows(STANDARD_MODULE)
        for row in rows:
            if row["航站楼"] == terminal and row["餐食类型"] == meal_type:
                row["餐食系数"] = factor
                return dict(row), f"{terminal} {meal_type} 配餐标准已更新"
        row = {"id": _next_id(rows), "航站楼": terminal, "餐食类型": meal_type, "餐食系数": factor}
        rows.append(row)
        return dict(row), f"{terminal} {meal_type} 配餐标准已新增"

    def _find_standard(self, terminal: str, meal_type: str) -> dict[str, Any] | None:
        for row in store.rows(STANDARD_MODULE):
            if row["航站楼"] == terminal and row["餐食类型"] == meal_type:
                return row
        return None

    # ---------- 车辆额度 ----------
    def list_quotas(self) -> list[dict[str, Any]]:
        result = []
        for row in store.rows(QUOTA_MODULE):
            item = dict(row)
            item["已占用额度"] = self._occupied_quota(row["配送车辆"])
            item["剩余额度"] = int(row["车辆额度"]) - item["已占用额度"]
            result.append(item)
        return result

    def upsert_quota(self, vehicle: str, quota: Any) -> tuple[dict[str, Any] | None, str]:
        vehicle = vehicle.strip()
        parsed = _to_int(quota)
        if not vehicle:
            return None, "配送车辆编号不能为空"
        if parsed is None:
            return None, "车辆额度必须是正整数"
        rows = store.rows(QUOTA_MODULE)
        for row in rows:
            if row["配送车辆"] == vehicle:
                row["车辆额度"] = parsed
                return dict(row), f"{vehicle} 车辆额度已更新为 {parsed}"
        row = {"id": _next_id(rows), "配送车辆": vehicle, "车辆额度": parsed}
        rows.append(row)
        return dict(row), f"{vehicle} 车辆额度已登记为 {parsed}"

    def _quota_row(self, vehicle: str) -> dict[str, Any] | None:
        for row in store.rows(QUOTA_MODULE):
            if row["配送车辆"] == vehicle:
                return row
        return None

    def _occupied_quota(self, vehicle: str) -> int:
        """占用额只统计未作废且仍在配送中的配送单；作废即释放。"""
        return sum(
            int(order.get("餐食数量", 0))
            for order in store.rows(ORDER_MODULE)
            if order.get("配送车辆") == vehicle and order.get("status") == "配送中"
        )

    # ---------- 规则校验：一次给出上限、超限、额度差额、缺失项 ----------
    def _evaluate(self, entry: dict[str, Any]) -> dict[str, Any]:
        seats = _to_int(entry.get("航班座位数"))
        quantity = _to_int(entry.get("餐食数量"))
        terminal = str(entry.get("航站楼") or "").strip()
        meal_type = str(entry.get("餐食类型") or "").strip()
        vehicle = str(entry.get("配送车辆") or "").strip()

        cap: int | None = None
        if seats is not None and terminal and meal_type:
            standard = self._find_standard(terminal, meal_type)
            if standard is not None:
                cap = math.floor(seats * float(standard["餐食系数"]))

        over = 0
        if cap is not None and quantity is not None and quantity > cap:
            over = quantity - cap

        remaining: int | None = None
        shortage = 0
        if vehicle:
            quota_row = self._quota_row(vehicle)
            if quota_row is not None and quantity is not None:
                remaining = int(quota_row["车辆额度"]) - self._occupied_quota(vehicle)
                if remaining < quantity:
                    shortage = quantity - remaining

        return {
            "cap": cap,
            "over": over,
            "remaining": remaining,
            "shortage": shortage,
            "missing": self._missing_fields(entry),
        }

    # ---------- 下达配送 ----------
    def dispatch(self, entry_id: int) -> tuple[dict[str, Any] | None, str, bool]:
        """返回 (台账行, 说明, 是否放行)；任何拦截分支放行标志都为 False。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"配餐任务 {entry_id} 不存在或已归档", False

        if entry["status"] in ("配送中", "已送达"):
            return None, f"配餐任务已{entry['status']}，不能重复下达配送", False
        if entry["status"] == "已变更":
            return None, "配餐任务已变更，如需配送请重新登记", False
        if entry["status"] == "已作废":
            return None, "该配餐批次已被同航班的新批次顶替作废，不能再下达配送", False

        # 1) 同航班同批次冲突处理：以最近一次为准，旧配送单与旧台账作废，先释放额度
        voided = self._void_conflicts(entry)

        # 2) 配餐数据缺失：待确认，不许放行
        missing = self._missing_fields(entry)
        if missing:
            entry["status"] = "待确认"
            entry["待确认项"] = missing
            entry["pending"] = True
            return (
                self._with_check_view(entry),
                f"配餐数据缺失，按待确认处理，不予配送：{'、'.join(missing)}",
                False,
            )

        check = self._evaluate(entry)
        quantity = int(entry["餐食数量"])

        # 3) 车辆额度不足：拒下并说明差多少
        if check["remaining"] is None:
            return self._with_check_view(entry), f"配送车辆 {entry['配送车辆']} 未登记额度，不予下达", False
        if check["shortage"] > 0:
            return (
                self._with_check_view(entry),
                f"车辆额度不足：{entry['配送车辆']} 剩余 {check['remaining']} 份，"
                f"本次需 {quantity} 份，还差 {check['shortage']} 份，不予下达配送",
                False,
            )

        # 4) 超出配餐阈值：同样拦下，并单独告警（同一任务只保留一条未消除告警）
        if check["over"] > 0:
            self._raise_alert(
                entry,
                order_id="",
                reason=(
                    f"{entry['航站楼']} {entry['餐食类型']} 单次上限 {check['cap']} 份"
                    f"（座位 {entry['航班座位数']}），申报 {quantity} 份，超限 {check['over']} 份"
                ),
            )
            entry["abnormal"] = True
            return (
                self._with_check_view(entry),
                f"超出{entry['航站楼']}{entry['餐食类型']}配餐阈值，超限 {check['over']} 份"
                f"（上限 {check['cap']} 份/申报 {quantity} 份），已单独告警，本次配送不予下达",
                False,
            )

        # 5) 全部通过：生成配送单，落到配送席待办；台账转配送中
        order = self._create_order(entry)
        entry["status"] = "配送中"
        entry["pending"] = True
        entry["abnormal"] = False
        self._resolve_alerts(str(entry["配餐编号"]))

        tail = f"；同航班同批次原配送单已作废 {len(voided)} 单" if voided else ""
        return self._with_check_view(entry), f"配送单 {order['配送单号']} 已下达并进入配送席待办{tail}", True

    def _void_conflicts(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """同航班同批次的其它有效配送单一律作废，对应台账退回待配送等待新下达。"""
        flight = str(entry.get("对应航班") or "").strip()
        batch = str(entry.get("批次号") or "").strip()
        if not flight or not batch:
            return []

        voided: list[dict[str, Any]] = []
        # 已送达的配送单代表实物已交付，不可再作废；只处理仍在途的配送单
        for order in store.rows(ORDER_MODULE):
            if (
                order.get("对应航班") == flight
                and order.get("批次号") == batch
                and order.get("status") == "配送中"
            ):
                order["status"] = ORDER_STATUS_VOID
                order["pending"] = False
                order["作废原因"] = f"同航班同批次由配餐任务 {entry['配餐编号']} 重新下达，以最近一次为准"
                voided.append(order)

        for other in store.rows(MODULE):
            if (
                other.get("id") != entry["id"]
                and other.get("对应航班") == flight
                and other.get("批次号") == batch
                and other.get("status") in ("配送中", "待配送")
            ):
                other["status"] = "已作废"
                other["pending"] = False
        return voided

    def _create_order(self, entry: dict[str, Any]) -> dict[str, Any]:
        rows = store.rows(ORDER_MODULE)
        new_id = _next_id(rows)
        order = {
            "id": new_id,
            "配送单号": f"CORD-{new_id:04d}",
            "配餐编号": entry["配餐编号"],
            "对应航班": entry["对应航班"],
            "航站楼": entry["航站楼"],
            "批次号": entry["批次号"],
            "餐食类型": entry["餐食类型"],
            "餐食数量": int(entry["餐食数量"]),
            "配送车辆": entry["配送车辆"],
            "配送人员": entry["配送人员"],
            "status": "配送中",
            "pending": True,
            "abnormal": False,
            "作废原因": "",
            "下达时间": _now_text(),
        }
        rows.append(order)
        return order

    # ---------- 告警台账：超限条数唯一来源 ----------
    def list_alerts(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(ALERT_MODULE))
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        return _paginate(rows, page, size)

    def _raise_alert(self, entry: dict[str, Any], order_id: str, reason: str) -> dict[str, Any]:
        rows = store.rows(ALERT_MODULE)
        for alert in rows:
            if alert.get("配餐编号") == entry["配餐编号"] and alert.get("状态") == "未消除":
                alert["告警原因"] = reason
                alert["告警时间"] = _now_text()
                if order_id:
                    alert["配送单号"] = order_id
                return alert
        new_id = _next_id(rows)
        alert = {
            "id": new_id,
            "告警编号": f"CALT-{new_id:04d}",
            "配餐编号": entry["配餐编号"],
            "配送单号": order_id,
            "对应航班": entry["对应航班"],
            "航站楼": entry["航站楼"],
            "批次号": entry["批次号"],
            "状态": "未消除",
            "告警原因": reason,
            "告警时间": _now_text(),
        }
        rows.append(alert)
        return alert

    def _resolve_alerts(self, catering_code: str) -> None:
        for alert in store.rows(ALERT_MODULE):
            if alert.get("配餐编号") == catering_code and alert.get("状态") == "未消除":
                alert["状态"] = "已消除"
                alert["处置说明"] = "已按阈值内数量重新下达配送"

    # ---------- 配送席待办 ----------
    def list_orders(
        self,
        *,
        status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(ORDER_MODULE))
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("配送单号", "")) or keyword in str(row.get("对应航班", ""))
            ]
        over_orders = active_over_limit_orders()
        for row in rows:
            # 配送席看到的超限标记直接取告警台账，不另算
            row["超限"] = str(row.get("配送单号")) in over_orders
        return _paginate(rows, page, size)

    def deliver_order(self, order_id: int) -> tuple[dict[str, Any] | None, str]:
        """配送席确认送达：配送单与配餐台账同步落已送达，两边结果保持一致。"""
        order = store.find(ORDER_MODULE, order_id)
        if order is None:
            return None, f"配送单 {order_id} 不存在"
        if order["status"] == "已作废":
            return None, "配送单已作废，不能确认送达"
        if order["status"] == "已送达":
            return None, "配送单已确认送达，请勿重复操作"

        order["status"] = "已送达"
        order["pending"] = False
        entry = next(
            (row for row in store.rows(MODULE) if row.get("配餐编号") == order["配餐编号"]),
            None,
        )
        if entry is not None and entry["status"] == "配送中":
            entry["status"] = "已送达"
            entry["pending"] = False
        return order, f"配送单 {order['配送单号']} 已确认送达"

    # ---------- 其它动作 ----------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str, bool]:
        """统一返回 (台账行或 None, 说明, 是否执行成功)。"""
        if action == "下达配送":
            return self.dispatch(entry_id)

        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"配餐任务 {entry_id} 不存在或已归档", False
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于航食配餐可执行范围", False

        if action == "确认送达":
            active_order = next(
                (
                    order
                    for order in store.rows(ORDER_MODULE)
                    if order.get("配餐编号") == entry["配餐编号"] and order.get("status") == "配送中"
                ),
                None,
            )
            if active_order is None:
                return None, "尚未下达配送或配送单已作废，无法确认送达", False
            active_order["status"] = "已送达"
            active_order["pending"] = False
            entry["status"] = "已送达"
            entry["pending"] = False
            return self._with_check_view(entry), f"配送单 {active_order['配送单号']} 已确认送达", True

        # 变更餐食：未完成的配送单一并作废，占用额度立刻释放
        for order in store.rows(ORDER_MODULE):
            if order.get("配餐编号") == entry["配餐编号"] and order.get("status") == "配送中":
                order["status"] = "已作废"
                order["pending"] = False
                order["作废原因"] = "配餐任务变更餐食，配送单作废"
        entry["status"] = "已变更"
        entry["pending"] = False
        return self._with_check_view(entry), "配餐任务已变更，相关配送单已作废", True

    # ---------- 概览数字 ----------
    def summary(self) -> dict[str, int]:
        rows = store.rows(MODULE)
        return {
            "配餐任务总数": len(rows),
            "待确认": sum(1 for row in rows if row.get("status") == "待确认"),
            "待配送": sum(1 for row in rows if row.get("status") == "待配送"),
            "配送中": sum(1 for row in rows if row.get("status") == "配送中"),
            "配送席待办": sum(1 for row in store.rows(ORDER_MODULE) if row.get("pending")),
            "超限条数": active_over_limit_count(),
        }
