
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..models import ChartSpec


def chart_spec_to_echarts(
    spec: ChartSpec | Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """把受限业务图表契约确定性转换为 ECharts option。"""

    chart = spec if isinstance(spec, ChartSpec) else ChartSpec.model_validate(spec)
    if chart.type == "histogram":
        return _histogram(chart, rows)
    categories: list[str] = []
    values: list[float | int | None] = []
    for row in rows:
        if chart.category_field not in row or chart.value_field not in row:
            raise ValueError(f"图表字段不存在：{chart.category_field} / {chart.value_field}")
        categories.append(str(row[chart.category_field]))
        values.append(_number(row[chart.value_field]))
    categories = categories[: chart.max_items]
    values = values[: chart.max_items]
    return {
        "title": {"text": chart.title},
        "tooltip": {"trigger": "axis" if chart.type in {"bar", "line"} else "item"},
        "xAxis": {"type": "category", "data": categories},
        "yAxis": {"type": "value"},
        "series": [{
            "type": "line" if chart.type == "line" else "bar",
            "data": values,
            "smooth": False,
        }],
    } if chart.type in {"bar", "line"} else {
        "title": {"text": chart.title},
        "tooltip": {"trigger": "item"},
        "series": [{
            "type": "pie",
            "data": [
                {"name": category, "value": value}
                for category, value in zip(categories, values)
            ],
        }],
    }


def _histogram(chart: ChartSpec, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [
        number for row in rows
        if chart.value_field in row
        for number in [_number(row[chart.value_field])]
        if number is not None
    ]
    if not values:
        raise ValueError(f"直方图没有可用数值字段：{chart.value_field}")
    low, high = min(values), max(values)
    if low == high:
        labels = [str(low)]
        counts = [len(values)]
    else:
        width = (high - low) / chart.bins
        counts = [0] * chart.bins
        for value in values:
            index = min(chart.bins - 1, int((value - low) / width))
            counts[index] += 1
        labels = [
            f"{low + index * width:.2f}–{low + (index + 1) * width:.2f}"
            for index in range(chart.bins)
        ]
    return {
        "title": {"text": chart.title},
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": labels},
        "yAxis": {"type": "value", "name": "数量"},
        "series": [{"type": "bar", "data": counts, "barCategoryGap": "0%"}],
    }


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


to_echarts_option = chart_spec_to_echarts

__all__ = ["chart_spec_to_echarts", "to_echarts_option"]
