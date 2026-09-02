"""Direct valve regulation for TRVs exposing an opening-degree number.

Heads like the Sonoff TRVZB (firmware >= 1.3.0, Zigbee2MQTT) expose
``number.*_valve_opening_degree`` / ``number.*_valve_closing_degree``.  When a
TRV is configured with those entities RoomMind drives the valve position
directly from the MPC power fraction, instead of faking demand through a
proportional setpoint and letting the head's own thermostat decide how far to
open.

The head's internal thermostat still gates the valve, so the underlying
``climate`` entity is parked at a boost setpoint and left there; the physical
position is whatever we write to the number entities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from ..const import (
    DEFAULT_VALVE_DEADBAND,
    DEFAULT_VALVE_MAX_OPENING,
    DEFAULT_VALVE_MIN_OPENING,
    DEFAULT_VALVE_OPENING_THRESHOLD,
    make_roommind_context,
)
from ..utils.device_utils import get_device_by_eid

_LOGGER = logging.getLogger(__name__)

# Last opening degree written per opening-degree entity, for deadband
# suppression. Mirrors the command cache in mpc_controller.
_last_opening: dict[str, int] = {}


def clear_valve_cache() -> None:
    """Forget the last written opening degrees (tests, reload)."""
    _last_opening.clear()


@dataclass(frozen=True)
class ValveConfig:
    """Valve regulation settings of a single TRV."""

    opening_entity: str
    closing_entity: str | None
    min_opening: int
    max_opening: int
    opening_threshold: int
    deadband: int


def get_valve_config(devices: list[dict], entity_id: str) -> ValveConfig | None:
    """Return the valve config of a TRV, or None when it is setpoint-driven."""
    device = get_device_by_eid(devices, entity_id)
    if device is None:
        return None
    opening_entity = device.get("valve_opening_entity")
    if not opening_entity:
        return None

    min_opening = int(device.get("valve_min_opening") or DEFAULT_VALVE_MIN_OPENING)
    max_opening = int(device.get("valve_max_opening") or DEFAULT_VALVE_MAX_OPENING)
    threshold = int(device.get("valve_opening_threshold") or DEFAULT_VALVE_OPENING_THRESHOLD)
    # A min above max would invert the interpolation slope; fall back to the
    # threshold, which is the lowest position we ever command.
    if min_opening >= max_opening:
        _LOGGER.warning(
            "TRV '%s': valve_min_opening (%d) >= valve_max_opening (%d), using threshold",
            entity_id,
            min_opening,
            max_opening,
        )
        min_opening = threshold

    return ValveConfig(
        opening_entity=opening_entity,
        closing_entity=device.get("valve_closing_entity") or None,
        min_opening=min_opening,
        max_opening=max_opening,
        opening_threshold=threshold,
        deadband=int(device.get("valve_deadband") or DEFAULT_VALVE_DEADBAND),
    )


def calculate_opening_degree(demand_percent: float, config: ValveConfig) -> int:
    """Map a 0-100 heat demand onto the head's usable opening range.

    Demand at or above the threshold is interpolated into
    [min_opening, max_opening]; anything below shuts the valve.
    """
    demand = max(0.0, min(100.0, demand_percent))
    if demand < config.opening_threshold or demand <= 0:
        return 0
    span = 100 - config.opening_threshold
    if span <= 0:
        return config.max_opening
    slope = (config.max_opening - config.min_opening) / span
    return round(config.min_opening + slope * (demand - config.opening_threshold))


async def async_apply_valve_demand(
    hass: HomeAssistant,
    entity_id: str,
    config: ValveConfig,
    demand_percent: float,
    *,
    area_id: str = "unknown",
    force: bool = False,
) -> None:
    """Write the opening (and mirrored closing) degree for one TRV.

    Changes smaller than the configured deadband are skipped, except for a
    full close/open which must always land.
    """
    degree = calculate_opening_degree(demand_percent, config)
    previous = _last_opening.get(config.opening_entity)
    is_endpoint = degree in (0, config.max_opening)
    if not force and previous is not None and not is_endpoint and abs(degree - previous) < config.deadband:
        return

    await _async_set_number(hass, config.opening_entity, degree, area_id=area_id)
    if config.closing_entity:
        await _async_set_number(hass, config.closing_entity, 100 - degree, area_id=area_id)
    _last_opening[config.opening_entity] = degree
    _LOGGER.debug(
        "Area '%s': TRV '%s' demand %.0f%% → opening degree %d%%",
        area_id,
        entity_id,
        demand_percent,
        degree,
    )


async def _async_set_number(hass: HomeAssistant, entity_id: str, value: int, *, area_id: str) -> None:
    """Send number.set_value, clamped to the entity's own min/max."""
    state = hass.states.get(entity_id)
    if state is None:
        _LOGGER.warning("Area '%s': valve entity '%s' not found", area_id, entity_id)
        return
    value = max(int(state.attributes.get("min", 0)), min(int(state.attributes.get("max", 100)), value))
    try:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": value},
            blocking=True,
            context=make_roommind_context(),
        )
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Area '%s': number.set_value(%d) failed on '%s'", area_id, value, entity_id, exc_info=True)
