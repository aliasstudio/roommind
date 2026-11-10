"""Schedule utilities for resolving future target temperatures."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from homeassistant.util import dt as dt_util

from ..const import (
    CLIMATE_MODE_COOL_ONLY,
    CLIMATE_MODE_HEAT_ONLY,
    DEFAULT_COMFORT_COOL,
    DEFAULT_COMFORT_HEAT,
    DEFAULT_ECO_COOL,
    DEFAULT_ECO_HEAT,
    MAX_TARGET_TEMP,
    MIN_TARGET_TEMP,
    OVERRIDE_BOOST,
    OVERRIDE_ECO,
    TargetTemps,
    is_override_active,
)
from .device_utils import DEVICE_TYPE_AC, DEVICE_TYPE_TRV, SWING_MODE_KEYS

_LOGGER = logging.getLogger(__name__)


def find_active_block(schedule_blocks: dict, ts: float) -> dict[str, Any] | None:
    """Find the schedule block active at the given timestamp.

    Returns the block's ``data`` dict, or None if no block matches.
    Caller must check ``schedule_blocks is None`` before calling.
    """
    dt = dt_util.as_local(datetime.fromtimestamp(ts, tz=UTC))
    day_name = dt.strftime("%A").lower()
    current_time = dt.time()
    day_blocks = schedule_blocks.get(day_name, [])
    for block in day_blocks:
        from_raw = block.get("from", "00:00:00")
        to_raw = block.get("to", "00:00:00")
        from_time = from_raw if hasattr(from_raw, "hour") else datetime.strptime(str(from_raw), "%H:%M:%S").time()
        to_time = to_raw if hasattr(to_raw, "hour") else datetime.strptime(str(to_raw), "%H:%M:%S").time()
        if from_time <= current_time < to_time:
            return dict(block.get("data", {}))
    return None


def sanitize_block_temp(
    raw: Any,
    converter: Callable[[float], float] | None = None,
) -> float | None:
    """Convert a schedule block temperature to °C, or None if unusable.

    Schedule blocks are free-form YAML, so a typo (110 instead of 11) reaches us
    unchecked and would be heated against for hours (#395). Values outside
    ``MIN_TARGET_TEMP``..``MAX_TARGET_TEMP`` are rejected so the caller can fall
    back to comfort/eco.

    The range check runs *after* ``converter``, so the bounds always apply to
    Celsius regardless of the HA display unit (on a °F system the accepted
    window is 41–95 °F).
    """
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    if converter is not None:
        val = converter(val)
    # Negated comparison also rejects NaN, which would pass `MIN <= v <= MAX`.
    if not MIN_TARGET_TEMP <= val <= MAX_TARGET_TEMP:
        return None
    return val


# Weekday order used when reporting rejected blocks, so the panel lists them
# in calendar order rather than dict insertion order.
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# Block data keys that carry a target temperature.
_BLOCK_TEMP_FIELDS = ("temperature", "heat_temperature", "cool_temperature")


def find_rejected_block_temps(
    schedule_blocks: dict | None,
    converter: Callable[[float], float] | None = None,
) -> list[dict[str, Any]]:
    """Scan *every* block of a schedule for temperatures the resolver would drop.

    ``sanitize_block_temp`` only sees the block that is running right now, so a
    typo in a block scheduled for 03:00 stays invisible until it fires (#395).
    This walks the whole week so the panel can surface it at any time of day.

    Returns one entry per rejected value, each with ``day``, ``from``, ``field``
    and the offending ``value`` as a string (it may not be numeric at all).
    Only values that are present but unusable are reported — an absent field is
    not a problem, it simply falls back to comfort.
    """
    if not schedule_blocks:
        return []

    rejected: list[dict[str, Any]] = []
    for day in _WEEKDAYS:
        for block in schedule_blocks.get(day, []) or []:
            data = block.get("data") or {}
            for field in _BLOCK_TEMP_FIELDS:
                raw = data.get(field)
                if raw is None or sanitize_block_temp(raw, converter) is not None:
                    continue
                rejected.append(
                    {
                        "day": day,
                        "from": str(block.get("from", "")),
                        "field": field,
                        "value": str(raw),
                    }
                )
    return rejected


def override_preset_band(room: dict, override_type: str) -> tuple[float, float]:
    """Return the (heat, cool) band a boost/eco override stands for.

    Both presets are bands in auto rooms, never a single point: collapsing eco
    to ``eco_heat`` on both sides makes an AC cool toward the heating target (#284).
    """
    if override_type == OVERRIDE_ECO:
        return (
            room.get("eco_heat", room.get("eco_temp", DEFAULT_ECO_HEAT)),
            room.get("eco_cool", DEFAULT_ECO_COOL),
        )
    return (
        room.get("comfort_heat", room.get("comfort_temp", DEFAULT_COMFORT_HEAT)),
        room.get("comfort_cool", DEFAULT_COMFORT_COOL),
    )


def mask_override_band(heat: float | None, cool: float | None, climate_mode: str) -> tuple[float | None, float | None]:
    """Drop the side a heat_only/cool_only room cannot act on."""
    if climate_mode == CLIMATE_MODE_HEAT_ONLY:
        return heat, None
    if climate_mode == CLIMATE_MODE_COOL_ONLY:
        return None, cool
    return heat, cool


def band_in_force(room: dict, live: dict) -> tuple[float | None, float | None]:
    """The (heat, cool) band a room is held at: its active override, else the targets resolved last cycle.

    Mold prevention is taken off again (not part of the user's band). Falls back to
    the comfort band when no live targets exist yet or the room is forced off.
    """
    if is_override_active(room):
        return room.get("override_heat"), room.get("override_cool")
    heat = live.get("heat_target")
    cool = live.get("cool_target")
    if heat is None and cool is None:
        return override_preset_band(room, OVERRIDE_BOOST)
    if heat is not None and live.get("mold_prevention_active"):
        heat -= live.get("mold_prevention_delta") or 0
    return heat, cool


def set_band_sides(
    band: tuple[float | None, float | None], heat: float | None, cool: float | None, climate_mode: str
) -> tuple[float | None, float | None] | None:
    """*band* with the given sides (°C, None = unchanged) set within the target bounds.

    A side the room's climate mode does not use is ignored; None if no usable side is
    given. A set side pushes the other along instead of crossing it, so heat never
    ends above cool.
    """
    cur_heat, cur_cool = mask_override_band(*band, climate_mode)
    heat, cool = mask_override_band(heat, cool, climate_mode)
    if heat is None and cool is None:
        return None

    def bounded(value: float) -> float:
        return round(min(MAX_TARGET_TEMP, max(MIN_TARGET_TEMP, value)), 1)

    if heat is not None:
        cur_heat = bounded(heat)
        if cool is None and cur_cool is not None and cur_cool < cur_heat:
            cur_cool = cur_heat
    if cool is not None:
        cur_cool = bounded(cool)
        if cur_heat is not None and cur_cool < cur_heat:
            cur_heat = cur_cool
    return cur_heat, cur_cool


def cap_cool_only_heat(heat: float | None, cool: float | None, climate_mode: str) -> tuple[float | None, float | None]:
    """Keep the unused heat side of a cool_only room from rising above the cool target.

    A cool_only room configured with the legacy ``comfort_temp`` (API, old configs)
    carried that value on the heat side next to the default ``comfort_cool``, e.g.
    heat 24.5 / cool 24.0. The optimizer lifts the cool target to the heat side and
    the cover target takes the higher one, while guard, hysteresis and the panel use
    the cool target. The cool target is what the panel shows for cool_only rooms.
    """
    if climate_mode == CLIMATE_MODE_COOL_ONLY and heat is not None and cool is not None and heat > cool:
        return cool, cool
    return heat, cool


def resolve_target_at_time(
    ts: float,
    schedule_blocks: dict | None,
    override_until: float | None,
    override_temp: float | None,
    vacation_until: float | None,
    vacation_temp: float | None,
    comfort_temp: float,
    eco_temp: float,
    presence_away: bool = False,
    block_temp_converter: Callable[[float], float] | None = None,
    presence_away_action: str = "eco",
    schedule_off_action: str = "eco",
) -> float | None:
    """Resolve what the target temp would be at a specific timestamp.

    Returns None when the action is "off" (devices should be turned off).
    """
    # 1. Override
    if override_temp is not None and (override_until is None or ts < override_until):
        return float(override_temp)
    # 2. Vacation
    if vacation_until is not None and ts < vacation_until and vacation_temp is not None:
        return float(vacation_temp)
    # 2.5 Presence
    if presence_away:
        if presence_away_action == "off":
            return None
        return eco_temp
    # 3. Schedule blocks
    if schedule_blocks is None:
        return comfort_temp
    data = find_active_block(schedule_blocks, ts)
    if data is not None:
        block_temp = data.get("temperature")
        if block_temp is not None:
            val = sanitize_block_temp(block_temp, block_temp_converter)
            if val is not None:
                return val
        return comfort_temp
    # Not in any block → eco or off
    if schedule_off_action == "off":
        return None
    return eco_temp


def resolve_targets_at_time(
    ts: float,
    schedule_blocks: dict | None,
    override_until: float | None,
    override_heat: float | None,
    override_cool: float | None,
    vacation_until: float | None,
    vacation_temp: float | None,
    comfort_heat: float,
    comfort_cool: float,
    eco_heat: float,
    eco_cool: float,
    presence_away: bool = False,
    block_temp_converter: Callable[[float], float] | None = None,
    presence_away_action: str = "eco",
    schedule_off_action: str = "eco",
    presence_clears_override: bool = False,
    room_vacant: bool = False,
) -> TargetTemps:
    """Resolve dual heat/cool target temps at a specific timestamp.

    Returns TargetTemps(heat, cool). None values mean "force off".
    ``room_vacant`` caps the schedule result at eco (occupancy eco, #180).
    """
    # 1. Override — split heat/cool dead-band (skipped when presence-away suppresses it)
    if (override_heat is not None or override_cool is not None) and (override_until is None or ts < override_until):
        if not (presence_away and presence_clears_override):
            return TargetTemps(heat=override_heat, cool=override_cool)
    # 2. Vacation — heat setback, cooling stays at eco_cool
    if vacation_until is not None and ts < vacation_until and vacation_temp is not None:
        t = float(vacation_temp)
        return TargetTemps(heat=t, cool=max(t, eco_cool))
    # 2.5 Presence
    if presence_away:
        if presence_away_action == "off":
            return TargetTemps(heat=None, cool=None)
        return TargetTemps(heat=eco_heat, cool=eco_cool)
    # 3. Schedule blocks
    targets = _schedule_targets_at_time(
        ts, schedule_blocks, comfort_heat, comfort_cool, eco_heat, eco_cool, block_temp_converter, schedule_off_action
    )
    return cap_to_eco(targets, eco_heat, eco_cool) if room_vacant else targets


def cap_to_eco(targets: TargetTemps, eco_heat: float, eco_cool: float) -> TargetTemps:
    """Lower the demand of *targets* to at most eco; never raise it, "off" sides stay off."""
    return TargetTemps(
        heat=min(targets.heat, eco_heat) if targets.heat is not None else None,
        cool=max(targets.cool, eco_cool) if targets.cool is not None else None,
    )


def _schedule_targets_at_time(
    ts: float,
    schedule_blocks: dict | None,
    comfort_heat: float,
    comfort_cool: float,
    eco_heat: float,
    eco_cool: float,
    block_temp_converter: Callable[[float], float] | None,
    schedule_off_action: str,
) -> TargetTemps:
    if schedule_blocks is None:
        return TargetTemps(heat=comfort_heat, cool=comfort_cool)
    data = find_active_block(schedule_blocks, ts)
    if data is not None:
        heat_temp_raw = data.get("heat_temperature")
        cool_temp_raw = data.get("cool_temperature")
        if heat_temp_raw is not None or cool_temp_raw is not None:
            h = comfort_heat
            c = comfort_cool
            if heat_temp_raw is not None:
                val = sanitize_block_temp(heat_temp_raw, block_temp_converter)
                if val is not None:
                    h = val
            if cool_temp_raw is not None:
                val = sanitize_block_temp(cool_temp_raw, block_temp_converter)
                if val is not None:
                    c = val
            return TargetTemps(heat=h, cool=c)
        block_temp = data.get("temperature")
        if block_temp is not None:
            # Distinct name from the vacation branch's `t`, which is a plain float.
            single = sanitize_block_temp(block_temp, block_temp_converter)
            if single is not None:
                return TargetTemps(heat=single, cool=single)
        return TargetTemps(heat=comfort_heat, cool=comfort_cool)
    # Not in any block → eco or off
    if schedule_off_action == "off":
        return TargetTemps(heat=None, cool=None)
    return TargetTemps(heat=eco_heat, cool=eco_cool)


def resolve_schedule_idle(schedule_blocks: dict | None, ts: float) -> dict[str, dict[str, str]] | None:
    """Return per-device-type idle overrides from the active schedule block.

    Lets a schedule slot dictate how each device type idles, independently: an
    AC can go fully ``off`` overnight for silence while a radiator valve only
    drops to ``low``, since a TRV that hibernates in off mode loses later
    commands. Reads ``ac_idle_action`` / ``trv_idle_action``, falling back to
    the type-less ``idle_action`` so schedules written before the split keep
    working. Returns None when the active block says nothing about idling, so
    the per-device configured behavior is used unchanged.
    """
    if schedule_blocks is None:
        return None
    data = find_active_block(schedule_blocks, ts)
    if not data:
        return None

    shared_action = data.get("idle_action")
    fan = data.get("idle_fan_mode")
    overrides: dict[str, dict[str, str]] = {}
    for device_type in (DEVICE_TYPE_AC, DEVICE_TYPE_TRV):
        action = data.get(f"{device_type}_idle_action") or shared_action
        if not action:
            continue
        override = {"idle_action": str(action)}
        if fan is not None:
            override["idle_fan_mode"] = str(fan)
        overrides[device_type] = override
    return overrides or None


def resolve_schedule_fan(schedule_blocks: dict | None, ts: float) -> dict[str, str] | None:
    """Return fan-control overrides from the active schedule block, or None.

    Lets a schedule slot cap or steer the AC fan (e.g. ``fan_max_mode: Low``
    overnight so active cooling stays quiet). Reads any of ``fan_control``,
    ``fan_min_mode`` and ``fan_max_mode`` from the active block's ``data``.
    Returns only the keys that are present, or None when none are, so the
    per-device fan configuration is otherwise used unchanged.
    """
    return _resolve_block_overrides(schedule_blocks, ts, ("fan_control", "fan_min_mode", "fan_max_mode"))


def resolve_schedule_swing(schedule_blocks: dict | None, ts: float) -> dict[str, str] | None:
    """Return the louver positions (``heat_swing_mode``, ``cool_swing_mode``) the active block sets, or None."""
    return _resolve_block_overrides(schedule_blocks, ts, SWING_MODE_KEYS)


def _resolve_block_overrides(schedule_blocks: dict | None, ts: float, keys: tuple[str, ...]) -> dict[str, str] | None:
    """Return the non-empty values of ``keys`` in the active block's data, or None when it sets none."""
    data = find_active_block(schedule_blocks, ts) if schedule_blocks is not None else None
    if not data:
        return None
    override = {key: str(data[key]) for key in keys if data.get(key)}
    return override or None


def resolve_schedule_dehumidify_off(schedule_blocks: dict | None, ts: float) -> bool:
    """Return True when the active schedule slot turns dehumidification off.

    Reads a ``dehumidify`` flag from the active block's ``data`` (set in the
    schedule YAML). ``dehumidify: false`` disables dehumidification for that
    slot — e.g. overnight — handing the room back to normal climate control so
    the AC's Dry program can't overcool. Absent or true keeps it enabled.
    """
    if schedule_blocks is None:
        return False
    data = find_active_block(schedule_blocks, ts)
    if not data:
        return False
    val = data.get("dehumidify", True)
    if isinstance(val, str):
        return val.strip().lower() in ("false", "0", "no", "off")
    return not bool(val)


def resolve_schedule_index(
    hass: HomeAssistant,
    room: dict,
    *,
    schedules_key: str = "schedules",
    selector_key: str = "schedule_selector_entity",
) -> int:
    """Return the 0-based index of the active schedule, or -1 if none.

    This is the single source of truth for schedule selector resolution,
    used by both the coordinator and schedule_utils helpers.

    Supports custom key names for reuse with different schedule types
    (e.g. cover schedules).
    """
    schedules = room.get(schedules_key, [])
    if not schedules:
        return -1

    selector_entity = room.get(selector_key, "")
    if not selector_entity:
        return 0

    state = hass.states.get(selector_entity)
    if state is None or state.state in ("unavailable", "unknown"):
        return 0

    if selector_entity.startswith("input_boolean."):
        return 1 if state.state == "on" else 0

    if selector_entity.startswith("input_number."):
        try:
            idx = int(float(state.state)) - 1  # 1-based → 0-based
        except (ValueError, TypeError):
            return 0
        if 0 <= idx < len(schedules):
            return idx
        return -1

    # Fallback for unknown entity domains
    return 0


WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def get_active_schedule(hass: HomeAssistant, room: dict) -> dict | None:
    """Return the active schedule entry, or None when no schedule applies."""
    schedules = room.get("schedules", [])
    idx = resolve_schedule_index(hass, room)
    if 0 <= idx < len(schedules):
        return schedules[idx]
    return None


def get_active_schedule_entity(
    hass: HomeAssistant,
    room: dict,
) -> str | None:
    """Return the entity_id of the currently active schedule, or None.

    Inline (UI-editable) schedules have no backing entity and return None.
    """
    schedule = get_active_schedule(hass, room)
    return (schedule.get("entity_id", "") or None) if schedule else None


async def resolve_active_schedule_blocks(
    hass: HomeAssistant,
    room: dict,
    cache: dict[str, dict] | None = None,
) -> tuple[dict | None, str | None]:
    """Return (weekly blocks, entity_id) for the room's active schedule.

    Inline schedules carry their blocks in the room config and resolve without
    an entity; entity-backed ones are read through schedule.get_schedule.
    """
    schedule = get_active_schedule(hass, room)
    if schedule is None:
        return None, None
    blocks = schedule.get("blocks")
    if blocks is not None:
        return normalize_blocks(blocks), None
    entity_id = schedule.get("entity_id", "") or None
    if entity_id is None:
        return None, None
    return await read_schedule_blocks(hass, entity_id, cache=cache), entity_id


def normalize_time(value: Any) -> str:
    """Return ``HH:MM:SS`` for a time object or any ``HH:MM[:SS[.ffffff]]`` string.

    HA renders an end-of-day ``24:00:00`` as ``23:59:59.999999``; fractional
    seconds are truncated so the stored strings stay parseable.
    """
    if hasattr(value, "hour"):
        return f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}"
    hours, _, rest = str(value).partition(":")
    minutes, _, seconds = rest.partition(":")
    seconds = seconds.partition(".")[0]
    return f"{int(hours):02d}:{int(minutes or 0):02d}:{int(seconds or 0):02d}"


def normalize_blocks(blocks: dict) -> dict:
    """Return weekly blocks with every weekday present, times canonical and sorted."""
    return {
        day: sorted(
            (
                {
                    **block,
                    "from": normalize_time(block.get("from", "00:00:00")),
                    "to": normalize_time(block.get("to", "00:00:00")),
                }
                for block in (blocks.get(day) or [])
            ),
            key=lambda block: block["from"],
        )
        for day in WEEKDAYS
    }


def next_schedule_change(hass: HomeAssistant, room: dict) -> float | None:
    """Return the timestamp of the active schedule's next block boundary, or None without one."""
    entity_id = get_active_schedule_entity(hass, room)
    state = hass.states.get(entity_id) if entity_id else None
    raw = state.attributes.get("next_event") if state else None
    if isinstance(raw, str):
        raw = dt_util.parse_datetime(raw)
    if not isinstance(raw, datetime):
        return None
    return raw.timestamp()


async def read_schedule_blocks(
    hass: HomeAssistant,
    schedule_entity_id: str,
    cache: dict[str, dict] | None = None,
) -> dict | None:
    """Read weekly schedule blocks via schedule.get_schedule service.

    When ``cache`` is provided, successful reads are stored under the entity ID
    so subsequent failures fall back to the last good blocks. Without a cache,
    transient service failures cause the caller's target resolution to silently
    revert to comfort_temp (see #308).
    """
    if not schedule_entity_id or not schedule_entity_id.startswith("schedule."):
        return None

    blocks: dict | None = None
    error: Exception | None = None
    try:
        response = await hass.services.async_call(
            "schedule",
            "get_schedule",
            {"entity_id": schedule_entity_id},
            blocking=True,
            return_response=True,
        )
        if response and schedule_entity_id in response:
            result = response[schedule_entity_id]
            if isinstance(result, dict):
                blocks = dict(result)
    except Exception as err:  # noqa: BLE001
        error = err

    if blocks is not None:
        if cache is not None:
            cache[schedule_entity_id] = blocks
        return blocks

    cached = cache.get(schedule_entity_id) if cache is not None else None
    if cached is not None:
        _LOGGER.debug(
            "schedule.get_schedule unavailable for %s; using cached blocks (error=%r)",
            schedule_entity_id,
            error,
        )
        return cached

    _LOGGER.warning(
        "schedule.get_schedule unavailable for %s and no cached blocks; "
        "target will fall back to comfort/eco (error=%r)",
        schedule_entity_id,
        error,
    )
    return None


def make_target_resolver(
    schedule_blocks: dict | None,
    room: dict,
    settings: dict,
    hass: HomeAssistant | None = None,
    presence_away: bool = False,
    mold_prevention_delta: float = 0.0,
    room_vacant: bool = False,
) -> Callable[[float], TargetTemps]:
    """Create a sync target resolver function (schedule blocks pre-fetched).

    Returns TargetTemps with None values for timestamps where devices should
    be turned off.
    """
    comfort_heat = room.get("comfort_heat", room.get("comfort_temp", DEFAULT_COMFORT_HEAT))
    comfort_cool = room.get("comfort_cool", DEFAULT_COMFORT_COOL)
    eco_heat = room.get("eco_heat", room.get("eco_temp", DEFAULT_ECO_HEAT))
    eco_cool = room.get("eco_cool", DEFAULT_ECO_COOL)
    override_until = room.get("override_until")
    override_heat = room.get("override_heat")
    override_cool = room.get("override_cool")
    vacation_until = settings.get("vacation_until")
    vacation_temp = settings.get("vacation_temp")
    presence_away_action = settings.get("presence_away_action", "eco")
    schedule_off_action = settings.get("schedule_off_action", "eco")
    presence_clears_override = bool(settings.get("presence_clears_override", False))
    climate_mode = room.get("climate_mode", "auto")

    converter: Callable[[float], float] | None = None
    if hass is not None:
        from .temp_utils import ha_temp_to_celsius

        _hass = hass
        converter = lambda v: ha_temp_to_celsius(_hass, v)  # noqa: E731

    def resolver(ts: float) -> TargetTemps:
        targets = resolve_targets_at_time(
            ts,
            schedule_blocks,
            override_until,
            override_heat,
            override_cool,
            vacation_until,
            vacation_temp,
            comfort_heat,
            comfort_cool,
            eco_heat,
            eco_cool,
            presence_away=presence_away,
            block_temp_converter=converter,
            presence_away_action=presence_away_action,
            schedule_off_action=schedule_off_action,
            presence_clears_override=presence_clears_override,
            room_vacant=room_vacant,
        )
        if targets.heat is None and targets.cool is None:
            return targets
        return TargetTemps(
            *cap_cool_only_heat(
                targets.heat + mold_prevention_delta if targets.heat is not None else None,
                targets.cool,
                climate_mode,
            )
        )

    return resolver
