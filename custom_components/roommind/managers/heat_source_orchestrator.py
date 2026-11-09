"""Heat source orchestrator for rooms with multiple heating device types.

When a room has both thermostats (e.g. radiator TRVs connected to a gas boiler)
and ACs with heating capability (heat pumps), this module decides which devices
to activate based on temperature gap (delta-T) and outdoor temperature.

Which device type leads is configurable per room (``heat_source_primary``):
the primary source carries the full power fraction, the secondary supplements
it at a reduced share when both run. Radiators lead by default; rooms where
the heat pump is the cheaper source can put the AC in front instead.

The orchestrator sits between the MPC optimizer output (abstract mode + power_fraction)
and the device command layer (async_apply). It does NOT modify the thermal model or
optimizer. It only filters and distributes the power decision across devices.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from ..const import (
    DEFAULT_HEAT_SOURCE_AC_MIN_OUTDOOR,
    DEFAULT_HEAT_SOURCE_ASSIST_PERCENT,
    DEFAULT_HEAT_SOURCE_OUTDOOR_THRESHOLD,
    DEFAULT_HEAT_SOURCE_PRIMARY,
    DEFAULT_HEAT_SOURCE_PRIMARY_DELTA,
    HEAT_SOURCE_HYSTERESIS,
    HEAT_SOURCE_LARGE_GAP_MULTIPLIER,
    HEAT_SOURCE_PRIMARY_AC,
    HEAT_SOURCE_SECONDARY_POWER_SCALE,
    MODE_HEATING,
)
from ..utils.device_utils import get_ac_eids, get_trv_eids, has_reliable_hvac_modes

_LOGGER = logging.getLogger(__name__)

_RUNNING_SOURCES = ("primary", "secondary", "both")


@dataclass
class DeviceCommand:
    """Command for a single climate device within a heat source plan."""

    entity_id: str
    role: str  # "primary" | "secondary"
    device_type: str  # "thermostat" | "ac"
    active: bool
    power_fraction: float  # 0.0-1.0
    reason: str


@dataclass
class HeatSourcePlan:
    """Orchestration plan describing which heating devices to activate."""

    commands: list[DeviceCommand]
    active_sources: str  # "primary" | "secondary" | "both" | "none"
    reason: str


def _is_available(hass: HomeAssistant, entity_id: str) -> bool:
    """Check if an entity is available (not unavailable/unknown)."""
    state = hass.states.get(entity_id)
    if state is None:
        return False
    return state.state not in ("unavailable", "unknown")


def _ac_can_heat(hass: HomeAssistant, entity_id: str) -> bool:
    """Check if a single AC entity supports heating and is available."""
    state = hass.states.get(entity_id)
    if state is None:
        return False
    if state.state in ("unavailable", "unknown"):
        return False
    modes = state.attributes.get("hvac_modes", [])
    if "heat" in modes or "heat_cool" in modes or "auto" in modes:
        return True
    # Modes unreliable (no active modes in list) — assume it can heat.
    return not has_reliable_hvac_modes(state)


def evaluate_heat_sources(
    room_config: dict,
    mode: str,
    power_fraction: float,
    current_temp: float | None,
    target_temp: float | None,
    outdoor_temp: float | None,
    previous_active_sources: str,
    hass: HomeAssistant,
) -> HeatSourcePlan | None:
    """Evaluate which heating devices to activate.

    Returns a HeatSourcePlan for MODE_HEATING, or None if orchestration
    should not apply (wrong mode, disabled, or missing data).
    """
    if mode != MODE_HEATING:
        return None

    if not room_config.get("heat_source_orchestration", False):
        return None

    thermostats = get_trv_eids(room_config.get("devices", []))
    acs = get_ac_eids(room_config.get("devices", []))
    if not thermostats or not acs:
        return None

    if current_temp is None or target_temp is None:
        return None

    primary_delta = room_config.get("heat_source_primary_delta", DEFAULT_HEAT_SOURCE_PRIMARY_DELTA)
    outdoor_threshold = room_config.get("heat_source_outdoor_threshold", DEFAULT_HEAT_SOURCE_OUTDOOR_THRESHOLD)
    ac_min_outdoor = room_config.get("heat_source_ac_min_outdoor", DEFAULT_HEAT_SOURCE_AC_MIN_OUTDOOR)
    assist_share = room_config.get("heat_source_assist_percent", DEFAULT_HEAT_SOURCE_ASSIST_PERCENT) / 100

    delta_t = target_temp - current_temp

    # Configurable roles: whichever type leads carries the full power fraction
    ac_is_primary = room_config.get("heat_source_primary", DEFAULT_HEAT_SOURCE_PRIMARY) == HEAT_SOURCE_PRIMARY_AC
    trv_devices: list[tuple[str, str]] = [(eid, "thermostat") for eid in thermostats]
    ac_devices: list[tuple[str, str]] = [(eid, "ac") for eid in acs]
    primary_devices, secondary_devices = (ac_devices, trv_devices) if ac_is_primary else (trv_devices, ac_devices)
    # Which group the outdoor preference resolves to, now that either type can lead
    ac_group = "primary" if ac_is_primary else "secondary"
    trv_group = "secondary" if ac_is_primary else "primary"

    # Early exit: at or above target, no heating needed. A run in progress keeps its
    # sources instead: the controller deliberately heats at the target (min-run
    # continuation, holding power), and parking everything there switched the
    # devices on and off every cycle around the target.
    if delta_t <= 0 and previous_active_sources not in _RUNNING_SOURCES:
        idle_cmds = [
            DeviceCommand(
                entity_id=eid,
                role=role,
                device_type=device_type,
                active=False,
                power_fraction=0.0,
                reason="not selected",
            )
            for role, devices in (("primary", primary_devices), ("secondary", secondary_devices))
            for eid, device_type in devices
        ]
        _LOGGER.debug(
            "Room '%s': heat source orchestration → none (delta_t=%.1f, outdoor=%s)",
            room_config.get("area_id", "?"),
            delta_t,
            outdoor_temp,
        )
        return HeatSourcePlan(commands=idle_cmds, active_sources="none", reason="delta_t <= 0")

    # Hardware protection: disable AC heating in extreme cold
    is_ac_too_cold = outdoor_temp is not None and outdoor_temp < ac_min_outdoor
    ac_disabled = is_ac_too_cold or not room_config.get("heat_source_ac_heating", True)
    if ac_disabled:
        # Remove ACs from both lists, they cannot heat
        primary_devices = [(eid, dt) for eid, dt in primary_devices if dt != "ac"]
        secondary_devices = [(eid, dt) for eid, dt in secondary_devices if dt != "ac"]

    # Filter ACs that don't support heating or are unavailable
    primary_devices = [(eid, dt) for eid, dt in primary_devices if dt != "ac" or _ac_can_heat(hass, eid)]
    secondary_devices = [(eid, dt) for eid, dt in secondary_devices if dt != "ac" or _ac_can_heat(hass, eid)]

    # Filter unavailable thermostats
    primary_devices = [(eid, dt) for eid, dt in primary_devices if dt != "thermostat" or _is_available(hass, eid)]
    secondary_devices = [(eid, dt) for eid, dt in secondary_devices if dt != "thermostat" or _is_available(hass, eid)]

    # Determine which source group to activate
    large_gap_threshold = primary_delta * HEAT_SOURCE_LARGE_GAP_MULTIPLIER

    # Weather-based preference with hysteresis (None when no outdoor data available)
    prefer_ac: bool | None
    if outdoor_temp is not None:
        # Keyed on which device type ran last, not on its role name — either
        # type can be the primary one.
        if previous_active_sources == ac_group:
            # AC was active: keep unless outdoor drops below threshold - hysteresis
            prefer_ac = outdoor_temp > outdoor_threshold - HEAT_SOURCE_HYSTERESIS
        elif previous_active_sources == trv_group:
            # Boiler was active: keep unless outdoor rises above threshold + hysteresis
            prefer_ac = outdoor_temp > outdoor_threshold + HEAT_SOURCE_HYSTERESIS
        else:
            prefer_ac = outdoor_temp > outdoor_threshold
    else:
        prefer_ac = None

    # "both" when gap is large, or hysteresis holds "both" state
    if delta_t >= large_gap_threshold + HEAT_SOURCE_HYSTERESIS:
        active = "both"
    elif previous_active_sources == "both" and delta_t > primary_delta - HEAT_SOURCE_HYSTERESIS:
        active = "both"
    elif prefer_ac is True:
        active = ac_group
    elif prefer_ac is False:
        active = trv_group
    else:
        # No outdoor data: delta-T heuristic (backward compatible)
        active = "primary" if delta_t >= primary_delta + HEAT_SOURCE_HYSTERESIS else "secondary"

    # Edge case: if chosen group has no devices, fall back
    if active == "secondary" and not secondary_devices:
        active = "primary"
    if active == "primary" and not primary_devices:
        active = "secondary"
    if active == "both" and not primary_devices:
        active = "secondary"
    if active == "both" and not secondary_devices:
        active = "primary"
    if not primary_devices and not secondary_devices:
        active = "none"

    # Build device commands
    commands: list[DeviceCommand] = []
    reason_parts: list[str] = []

    if is_ac_too_cold and active != "none":
        reason_parts.append(f"AC disabled (outdoor {outdoor_temp}°C < {ac_min_outdoor}°C)")
    elif ac_disabled and active != "none":
        reason_parts.append("AC heating switched off")

    if active == "both":
        reason_parts.append(f"large gap ({delta_t:.1f}°C)")
    elif active in ("primary", "secondary"):
        outdoor_str = f"{outdoor_temp}°C" if outdoor_temp is not None else "n/a"
        lead = "AC" if active == ac_group else "boiler"
        reason_parts.append(f"{lead} preferred ({delta_t:.1f}°C gap, outdoor {outdoor_str})")

    def group_fraction(group: str) -> float:
        if active == "both" and group == "secondary":
            return round(power_fraction * HEAT_SOURCE_SECONDARY_POWER_SCALE, 2)
        if active in (group, "both"):
            return power_fraction
        # Idle group assists the lead one when a share is configured
        return round(power_fraction * assist_share, 2) if active != "none" else 0.0

    for group, devices in (("primary", primary_devices), ("secondary", secondary_devices)):
        fraction = group_fraction(group)
        is_selected = active in (group, "both")
        for eid, device_type in devices:
            commands.append(
                DeviceCommand(
                    entity_id=eid,
                    role=group,
                    device_type=device_type,
                    active=is_selected or fraction > 0,
                    power_fraction=fraction,
                    reason="active" if is_selected else ("assist" if fraction > 0 else "not selected"),
                )
            )

    reason = "; ".join(reason_parts) if reason_parts else active

    _LOGGER.debug(
        "Room '%s': heat source orchestration → %s (delta_t=%.1f, outdoor=%s)",
        room_config.get("area_id", "?"),
        active,
        delta_t,
        outdoor_temp,
    )

    return HeatSourcePlan(commands=commands, active_sources=active, reason=reason)
