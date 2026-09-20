from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Salary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    salary_raw: str
    salary_min_k: float | None = None
    salary_max_k: float | None = None
    salary_months: int = 12
    salary_annual_min_k: float | None = None
    salary_annual_max_k: float | None = None
    is_negotiable: bool = False
    salary_unit: str | None = None
    salary_period: str | None = None
    salary_parse_status: Literal["parsed", "negotiable", "unsupported", "invalid"]
    salary_conversion_rule: str | None = None


_NUMBER = r"\d+(?:\.\d+)?"
_PATTERN = re.compile(
    rf"(?P<annual>年薪)?(?P<low>{_NUMBER})(?P<unit1>K|万|元)?"
    rf"(?:-(?P<high>{_NUMBER}))?(?P<unit2>K|万|元)?"
    r"(?:/(?P<period>月|天|日|年|小时|时))?(?:[·•](?P<months>\d+)薪)?",
    re.IGNORECASE,
)


def parse_salary(raw: str) -> Salary:
    """月薪单位为 K；日薪按 22 天估算，年薪按 12 月折算，未知口径不猜测。"""
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw))
    text = text.replace("—", "-").replace("–", "-").replace("~", "-").replace("～", "-")
    if text in {"面议", "薪资面议", "工资面议"}:
        return Salary(salary_raw=raw, is_negotiable=True, salary_parse_status="negotiable")
    match = _PATTERN.fullmatch(text)
    if not match:
        return Salary(salary_raw=raw, salary_parse_status="unsupported" if text else "invalid")
    low = Decimal(match["low"])
    high = Decimal(match["high"] or match["low"])
    unit = (match["unit2"] or match["unit1"] or "").upper()
    period = match["period"] or ("年" if match["annual"] else "月")
    months = int(match["months"] or 12)
    if (low <= 0 or high < low or not 1 <= months <= 36
        or (match["unit1"] and match["unit2"] and match["unit1"].upper() != unit)
        or (match["annual"] and period != "年")
        or (period != "月" and match["months"])):
        return Salary(salary_raw=raw, salary_parse_status="invalid")
    periods = {"月": "month", "天": "day", "日": "day", "年": "year", "小时": "hour", "时": "hour"}
    if not unit or period in {"小时", "时"} or (unit in {"万", "元"} and not match["period"] and not match["annual"]):
        return Salary(salary_raw=raw, salary_unit=unit or None, salary_period=periods[period], salary_parse_status="unsupported")
    factor = {"K": Decimal(1), "万": Decimal(10), "元": Decimal("0.001")}[unit]
    rule = "monthly_native"
    if period in {"天", "日"}:
        factor *= 22
        rule = "daily_x_22"
    elif period == "年":
        factor /= 12
        rule = "annual_div_12"
    return Salary(
        salary_raw=raw, salary_min_k=float(low * factor), salary_max_k=float(high * factor),
        salary_months=months, salary_annual_min_k=float(low * factor * months),
        salary_annual_max_k=float(high * factor * months), salary_unit=unit,
        salary_period=periods[period], salary_parse_status="parsed", salary_conversion_rule=rule,
    )
