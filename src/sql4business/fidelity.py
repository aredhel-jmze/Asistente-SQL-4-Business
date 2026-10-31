"""Report fidelity: the report must state the verified answer and no other figures.

A figure is faithful when it equals a verified value at the precision it is
written with (60.1 % for 60.1257, "18,5 millones" for 18 508 100), or when it
comes from the question or the executed SQL (years, days, product names).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .sql_tools import normalize_text


NUMBER = re.compile(
    r"(?<![\w.,])(\d+(?:[.,]\d+)*)(?:\s*(%|por ciento|percent))?"
    r"(?:\s*(millones|millon|millón|millions|million|mil|thousand))?",
    re.IGNORECASE,
)
SCALES = {"millones": 1e6, "millon": 1e6, "millón": 1e6, "millions": 1e6, "million": 1e6, "mil": 1e3, "thousand": 1e3}
NUMBER_WORDS = {
    0: {"cero", "zero"}, 1: {"uno", "una", "one"}, 2: {"dos", "two"}, 3: {"tres", "three"},
    4: {"cuatro", "four"}, 5: {"cinco", "five"}, 6: {"seis", "six"}, 7: {"siete", "seven"},
    8: {"ocho", "eight"}, 9: {"nueve", "nine"}, 10: {"diez", "ten"},
}
INCREASE = ("aument", "crec", "subi", "increment", "alza", "mayor", "increas", "grew", "grow", "rose", "rise", "higher")
DECREASE = ("disminu", "decrec", "cay", "redu", "menor", "caida", "descend", "decreas", "declin", "fell", "fall", "drop", "lower")
SAME = (
    "no cambi", "se mantuv", "mantuvo", "mismo", "misma", "same", "did not change",
    "didn t change", "unchanged", "remained", "no hubo cambio", "sigue siendo",
)
CHANGED = ("cambi", "changed", "different", "distint", "diferent", "switched", "replaced", "reemplaz")


@dataclass
class FidelityCheck:
    faithful: bool
    issues: list[str] = field(default_factory=list)


def _readings(token: str) -> list[tuple[float, float]]:
    """(value, tolerance) readings of a written number, tolerance from its precision."""

    separators = re.findall(r"[.,]", token)
    groups = re.split(r"[.,]", token)
    readings: list[tuple[float, float]] = []
    if not separators:
        return [(float(token), 0.5)]
    if len(set(separators)) == 2:  # 108,273.55 or 108.273,55
        decimal = separators[-1]
        integer = re.sub(r"[.,]", "", token[: token.rfind(decimal)])
        fraction = token[token.rfind(decimal) + 1 :]
        return [(float(f"{integer}.{fraction}"), 0.5 * 10 ** -len(fraction))]
    if all(len(group) == 3 for group in groups[1:]):  # 1,655 / 97.658.120 read as thousands
        readings.append((float("".join(groups)), 0.5))
    if len(separators) == 1:  # 18,5 / 60.13 read as decimals
        readings.append((float(f"{groups[0]}.{groups[1]}"), 0.5 * 10 ** -len(groups[1])))
    return readings


def extract_figures(text: str) -> list[tuple[str, list[tuple[float, float]]]]:
    figures = []
    for match in NUMBER.finditer(text):
        token, _, scale = match.groups()
        factor = SCALES.get((scale or "").lower(), 1.0)
        readings = [(value * factor, tolerance * factor) for value, tolerance in _readings(token)]
        figures.append((match.group(0).strip(), readings))
    return figures


def _numbers(value: Any) -> list[float]:
    """Numeric values, also as magnitudes: direction is checked separately."""

    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value), abs(float(value))]
    if isinstance(value, dict):
        return [n for item in value.values() for n in _numbers(item)]
    if isinstance(value, (list, tuple)):
        return [n for item in value for n in _numbers(item)]
    return []


def _matches(readings: list[tuple[float, float]], target: float) -> bool:
    return any(abs(value - target) <= tolerance + 1e-9 * abs(target) for value, tolerance in readings)


def _required(answer: Any) -> list[list[Any]]:
    """Alternatives of required values; each alternative is a list of numbers/strings."""

    if isinstance(answer, dict):
        if "growth_pct" in answer:
            return [[answer["growth_pct"]]]
        if "share_pct" in answer:
            return [[answer["share_pct"]], [answer["total"], answer["subset"]]]
        if "winner" in answer:
            return [[answer["winner"]]]
        if "first" in answer:
            return [[answer["first"], answer["second"]]]
    if isinstance(answer, list):
        items: list[Any] = []
        for row in answer:
            labels = [value for value in row if isinstance(value, str)]
            items.extend(labels or [value for value in row if isinstance(value, (int, float))])
        return [items]
    return [[answer]]


def _stated(item: Any, report: str, figures: list[tuple[str, list[tuple[float, float]]]]) -> bool:
    if isinstance(item, str):
        return normalize_text(item) in normalize_text(report)
    if any(_matches(readings, abs(float(item))) for _, readings in figures):
        return True
    words = set(normalize_text(report).split())
    return float(item).is_integer() and bool(NUMBER_WORDS.get(int(item), set()) & words)


def _has(text: str, stems: tuple[str, ...]) -> bool:
    words = normalize_text(text).split()
    phrase = " ".join(words)
    return any((" " in stem and stem in phrase) or any(word.startswith(stem) for word in words) for stem in stems)


def check_report(report: str, answer: Any, evidence: str = "") -> FidelityCheck:
    """Check a model-written report against the verified answer.

    ``evidence`` is the question plus the executed SQL, step purposes and rows:
    figures that appear there (dates, product names, step values) are allowed.
    """

    issues: list[str] = []
    figures = extract_figures(report)
    if not any(all(_stated(item, report, figures) for item in option) for option in _required(answer)):
        issues.append(f"The report does not state the verified answer {json.dumps(answer, ensure_ascii=False)}.")

    # Allowed figures: verified values, digits inside verified names ("Notebook 14
    # pulgadas") and figures of the evidence (question dates, SQL ranges, step rows).
    context = json.dumps(answer, ensure_ascii=False) + "\n" + evidence
    allowed = _numbers(answer) + [value for _, readings in extract_figures(context) for value, _ in readings]
    for text, readings in figures:
        if not any(_matches(readings, target) for target in allowed):
            issues.append(f"Unsupported figure in the report: {text}.")

    if isinstance(answer, dict) and "growth_pct" in answer:
        up, down = _has(report, INCREASE), _has(report, DECREASE)
        increase = answer["growth_pct"] >= 0
        if (increase and (not up or down)) or (not increase and (not down or up)):
            issues.append(f"The report must describe the change as an {answer['direction']}.")
        # One change between two periods: stating it twice attributes it to each period
        # ("60.13% between March and May and 60.13% between June and August").
        stated = sum(1 for _, readings in figures if _matches(readings, abs(answer["growth_pct"])))
        if stated > 1:
            issues.append(
                "The report states the percentage change more than once; it is a single change from "
                "the first period to the second, so state it once."
            )
    if isinstance(answer, dict) and "changed" in answer:
        same, changed = _has(report, SAME), _has(report, CHANGED)
        if answer["changed"] and (same or not changed):
            issues.append("The report must say that the result changed.")
        if not answer["changed"] and not same:
            issues.append("The report must say that the result did not change.")
    return FidelityCheck(not issues, issues)
