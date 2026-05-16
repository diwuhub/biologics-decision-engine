"""
Unit Normalizer — standalone unit conversion for biologics analytical data.

Converts raw value+unit strings into canonical normalized forms across
supported unit families: concentration, percentage, temperature, time,
molecular weight, volume, and pH.

Extracted from bio-cmc-ai-suite/cmc-harmonizer (archived 2026-03-25).
Stripped of Streamlit/SDK dependencies for use as shared infrastructure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Unit families: source_unit -> (canonical_unit, factor, is_approximate)
# Factor: canonical = raw * factor  (except temperature, handled specially)
# ---------------------------------------------------------------------------
_CONCENTRATION: dict[str, tuple[str, float, bool]] = {
    "mg/ml":  ("mg/mL", 1.0,       False),
    "g/l":    ("mg/mL", 1.0,       False),
    "g/ml":   ("g/mL",  1.0,       False),
    "µg/ml":  ("mg/mL", 0.001,     False),
    "ug/ml":  ("mg/mL", 0.001,     False),
    "ng/ml":  ("mg/mL", 0.000001,  False),
    "mg/l":   ("mg/mL", 0.001,     False),
    "µg/l":   ("mg/mL", 0.000001,  False),
    "ug/l":   ("mg/mL", 0.000001,  False),
}

_PERCENTAGE: dict[str, tuple[str, float, bool]] = {
    "%":     ("%",    1.0, False),
    "%w/v":  ("%w/v", 1.0, False),
    "%w/w":  ("%w/w", 1.0, False),
    "%v/v":  ("%v/v", 1.0, False),
}

_TEMPERATURE_CANONICAL = "°C"
_TEMPERATURE_SOURCES = {"°c", "c", "°f", "f", "k"}

_TIME: dict[str, tuple[str, float, bool]] = {
    "hours":  ("hours",   1.0,    False),
    "hour":   ("hours",   1.0,    False),
    "hr":     ("hours",   1.0,    False),
    "h":      ("hours",   1.0,    False),
    "min":    ("hours",   1/60,   False),
    "minutes":("hours",   1/60,   False),
    "minute": ("hours",   1/60,   False),
    "s":      ("hours",   1/3600, False),
    "sec":    ("hours",   1/3600, False),
    "seconds":("hours",   1/3600, False),
    "months": ("months",  1.0,    False),
    "month":  ("months",  1.0,    False),
    "weeks":  ("months",  0.2301, True),
    "week":   ("months",  0.2301, True),
    "days":   ("months",  0.03285,True),
    "day":    ("months",  0.03285,True),
}

_MOLECULAR_WEIGHT: dict[str, tuple[str, float, bool]] = {
    "kda":   ("kDa", 1.0,   False),
    "da":    ("kDa", 0.001, False),
    "g/mol": ("kDa", 0.001, False),
}

_VOLUME: dict[str, tuple[str, float, bool]] = {
    "ml": ("mL",  1.0,    False),
    "l":  ("mL",  1000.0, False),
    "µl": ("mL",  0.001,  False),
    "ul": ("mL",  0.001,  False),
}

# Recognized biopharmaceutical units — preserved without conversion
_PRESERVED_UNITS = {
    "iu/ml", "u/ml", "iu/mg",
    "od", "au",
    "ppm", "ppb",
    "eu/ml", "eu/mg", "eu/dose",
    "cfu/ml", "cfu/g",
    "pg/mg", "ng/mg", "pg/dose", "ng/dose",
    "particles/ml", "particles/container", "per container",
    "particles/vial", "per ml",
    "mosm/kg", "mosmol/kg",
    "mpa.s", "mpa·s", "cp",
    "ntu", "fnu",
    "ph",
}

# Pattern to parse "5 mg/mL", "< 0.1 EU/mL", "37 °C", etc.
_VALUE_UNIT_RE = re.compile(
    r"^\s*(?P<prefix>[<>≤≥±]?)\s*(?P<value>-?\d+\.?\d*)\s*(?P<unit>.+)?\s*$"
)


@dataclass
class NormalizedUnit:
    """Result of normalizing a value+unit string."""
    value: float
    unit: str
    normalized_value: float
    normalized_unit: str
    is_approximate: bool = False
    family: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize_unit_str(unit: str | None) -> str:
    if not unit:
        return ""
    return unit.strip().lower()


def _convert_temperature(value: float, source_unit: str) -> tuple[float, bool]:
    """Convert temperature to °C."""
    su = source_unit.lower().strip()
    if su in ("°c", "c"):
        return value, False
    elif su in ("°f", "f"):
        return round((value - 32) * 5 / 9, 4), False
    elif su == "k":
        return round(value - 273.15, 4), False
    return value, False


def _find_unit_family(unit_lower: str) -> tuple[str, float, bool, str] | None:
    """Look up unit in all families.
    Returns (canonical_unit, factor, is_approximate, family_name) or None.
    """
    for family, name in [
        (_CONCENTRATION, "concentration"),
        (_PERCENTAGE, "percentage"),
        (_TIME, "time"),
        (_MOLECULAR_WEIGHT, "molecular_weight"),
        (_VOLUME, "volume"),
    ]:
        if unit_lower in family:
            canon, factor, approx = family[unit_lower]
            return canon, factor, approx, name
    return None


def normalize(input_str: str) -> NormalizedUnit:
    """Normalize a value+unit string.

    Examples:
        >>> normalize("5 mg/mL")
        NormalizedUnit(value=5.0, unit='mg/mL', normalized_value=5.0,
                       normalized_unit='mg/mL', ...)

        >>> normalize("500 µg/mL")
        NormalizedUnit(value=500.0, unit='µg/mL', normalized_value=0.5,
                       normalized_unit='mg/mL', ...)

        >>> normalize("310 K")
        NormalizedUnit(value=310.0, unit='K', normalized_value=36.85,
                       normalized_unit='°C', ...)
    """
    input_str = input_str.strip()
    m = _VALUE_UNIT_RE.match(input_str)
    if not m:
        raise ValueError(f"Cannot parse value+unit from: {input_str!r}")

    value = float(m.group("value"))
    raw_unit = (m.group("unit") or "").strip()

    if not raw_unit:
        return NormalizedUnit(
            value=value, unit="", normalized_value=value,
            normalized_unit="", family=None,
        )

    unit_lower = _normalize_unit_str(raw_unit)

    # Preserved units — no conversion possible
    if unit_lower in _PRESERVED_UNITS:
        return NormalizedUnit(
            value=value, unit=raw_unit, normalized_value=value,
            normalized_unit=raw_unit, family="preserved",
        )

    # Temperature
    if unit_lower in _TEMPERATURE_SOURCES:
        converted, _ = _convert_temperature(value, unit_lower)
        return NormalizedUnit(
            value=value, unit=raw_unit, normalized_value=converted,
            normalized_unit=_TEMPERATURE_CANONICAL, family="temperature",
        )

    # Standard unit families
    result = _find_unit_family(unit_lower)
    if result:
        canon, factor, approx, family_name = result
        normalized_value = round(value * factor, 6)
        return NormalizedUnit(
            value=value, unit=raw_unit, normalized_value=normalized_value,
            normalized_unit=canon, is_approximate=approx, family=family_name,
        )

    # Unrecognized — pass through unchanged
    return NormalizedUnit(
        value=value, unit=raw_unit, normalized_value=value,
        normalized_unit=raw_unit, family=None,
    )


def normalize_value(value: float, unit: str) -> NormalizedUnit:
    """Normalize a pre-parsed numeric value with its unit.

    Convenience wrapper when value and unit are already separated.
    """
    return normalize(f"{value} {unit}")
