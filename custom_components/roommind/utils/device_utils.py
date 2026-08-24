"""Device helper utilities for the unified device model.

Pure utility module with NO dependencies on HA or other RoomMind modules.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

DEVICE_TYPE_TRV = "trv"
DEVICE_TYPE_AC = "ac"
VALID_DEVICE_TYPES = {DEVICE_TYPE_TRV, DEVICE_TYPE_AC}

DEVICE_ROLE_AUTO = "auto"

VALID_HEATING_SYSTEM_TYPES = {"", "radiator", "underfloor"}

# Heating system type priority: higher = longer residual heat tau
HST_PRIORITY = {"underfloor": 2, "radiator": 1, "": 0}

IDLE_ACTION_OFF = "off"
IDLE_ACTION_FAN_ONLY = "fan_only"
DEFAULT_IDLE_FAN_MODE = "low"
# Active fan mode applied while a device is actively heating/cooling.
# Empty string = leave the device/firmware in control (legacy behavior).
DEFAULT_ACTIVE_FAN_MODE = ""
IDLE_ACTION_SETBACK = "setback"
IDLE_ACTION_LOW = "low"
DEFAULT_IDLE_SETBACK_OFFSET = 2.0

SETPOINT_MODE_PROPORTIONAL = "proportional"
SETPOINT_MODE_DIRECT = "direct"

# Fan-speed control strategy while a device is actively heating/cooling.
FAN_CONTROL_OFF = "off"  # leave fan to the device/firmware (legacy default)
FAN_CONTROL_FIXED = "fixed"  # always force the configured active_fan_mode
FAN_CONTROL_AUTO = "auto"  # auto-escalate across a speed ladder by demand
VALID_FAN_CONTROL_MODES = {FAN_CONTROL_OFF, FAN_CONTROL_FIXED, FAN_CONTROL_AUTO}
DEFAULT_FAN_CONTROL = FAN_CONTROL_OFF

# Canonical strength ranking for common fan-mode labels (weakest -> strongest).
# Used to order a device's reported ``fan_modes`` into an escalation ladder.
# Labels are matched case-insensitively by substring; non-graded labels such
# as "auto" / "diffuse" are excluded from the ladder entirely.
_FAN_STRENGTH_TOKENS: list[tuple[str, int]] = [
    ("mid-high", 45),
    ("medium-high", 45),
    ("mid-low", 25),
    ("medium-low", 25),
    ("silent", 0),
    ("mute", 0),
    ("quiet", 0),
    ("night", 0),
    ("sleep", 0),
    ("turbo", 70),
    ("strong", 70),
    ("powerful", 70),
    ("boost", 70),
    ("max", 65),
    ("high", 60),
    ("medium", 35),
    ("mid", 35),
    ("low", 20),
    ("min", 15),
]
# Labels that never belong in the escalation ladder (no defined strength).
_FAN_LADDER_EXCLUDE = ("auto", "diffuse", "natural")


def _fan_strength(label: str) -> int | None:
    """Return a coarse strength score for a fan-mode label, or None if ungraded.

    Matching is case-insensitive and substring-based; the longest/most specific
    tokens (e.g. "mid-high") are listed before their substrings ("high") so the
    specific match wins.
    """
    low = label.strip().lower()
    if any(tok in low for tok in _FAN_LADDER_EXCLUDE):
        return None
    for token, score in _FAN_STRENGTH_TOKENS:
        if token in low:
            return score
    return None


def order_fan_modes(fan_modes: list[str]) -> list[str]:
    """Order a device's fan modes weakest -> strongest for auto escalation.

    Ungraded labels (e.g. "Auto", "Diffuse") are dropped — they have no place
    on a strength ladder. Graded labels with an identical score keep their
    original relative order (stable sort). Unknown labels are excluded so the
    ladder only contains speeds we can reason about.
    """
    graded: list[tuple[int, int, str]] = []
    for idx, fm in enumerate(fan_modes):
        score = _fan_strength(fm)
        if score is None:
            continue
        graded.append((score, idx, fm))
    graded.sort(key=lambda t: (t[0], t[1]))
    return [fm for _, _, fm in graded]


# Relative thermal effectiveness of the weakest fan speed versus the strongest.
# Airflow across the coil scales heat exchange, so the slowest fan removes/adds
# less heat than the fastest at the same compressor power. 0.5 means the weakest
# speed is ~half as effective as the strongest; the mid speeds interpolate.
FAN_EFFICIENCY_MIN = 0.5


def fan_efficiency(fan_mode: str, fan_modes: list[str], *, min_efficiency: float = FAN_EFFICIENCY_MIN) -> float:
    """Relative cooling/heating effectiveness of *fan_mode* on its device's ladder.

    The device's ``fan_modes`` are ordered weakest -> strongest and the result is
    linear in ladder position: the weakest speed maps to *min_efficiency*, the
    strongest to 1.0. Used to fold fan speed into the EKF's power_fraction so the
    learned cooling rate isn't biased by whatever speed happened to run.

    Returns 1.0 (no scaling) when the mode isn't on a graded ladder or the ladder
    has fewer than two graded speeds — preserving legacy behavior for devices
    whose fan RoomMind can't reason about (e.g. "Auto"-only).
    """
    frac = fan_ladder_position(fan_mode, fan_modes)
    if frac is None:
        return 1.0
    return min_efficiency + (1.0 - min_efficiency) * frac


def fan_ladder_position(fan_mode: str, fan_modes: list[str]) -> float | None:
    """Position of *fan_mode* on its graded ladder: 0.0 weakest, 1.0 strongest.

    Returns None when the mode isn't on a graded ladder or the ladder has fewer
    than two graded speeds — i.e. when airflow can't be reasoned about.
    """
    if not fan_mode:
        return None
    ladder = order_fan_modes(fan_modes)
    if len(ladder) < 2 or fan_mode not in ladder:
        return None
    return ladder.index(fan_mode) / (len(ladder) - 1)


def get_cool_setpoint_floor(devices: list[dict], entity_id: str) -> float | None:
    """Warmest AC cooling setpoint allowed at minimum airflow, or None if unset."""
    dev = get_device_by_eid(devices, entity_id)
    if dev is None:
        return None
    raw = dev.get("cool_setpoint_floor")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def get_dehumidify_target(devices: list[dict], entity_id: str) -> float | None:
    """Per-device dry-mode target override, or None to use the room's schedule target."""
    dev = get_device_by_eid(devices, entity_id)
    if dev is None:
        return None
    raw = dev.get("dehumidify_target")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def scale_cool_floor_by_airflow(
    floor: float,
    device_min: float,
    fan_mode: str | None,
    fan_modes: list[str],
) -> float:
    """Relax the cooling setpoint floor as airflow rises.

    Cold-draft discomfort tracks compressor demand *per unit airflow*: pinning an
    inverter to its minimum setpoint while the fan crawls drives the coil deep and
    makes the supply air unpleasantly cold without buying much capacity. The floor
    therefore applies in full at the quietest speed and relaxes linearly to
    *device_min* at the top of the ladder, where the draft is no longer a problem.
    """
    if floor <= device_min:
        return device_min
    airflow = fan_ladder_position(fan_mode or "", fan_modes)
    if airflow is None:
        return floor
    return device_min + (1.0 - airflow) * (floor - device_min)


def get_fan_control(devices: list[dict], entity_id: str) -> tuple[str, str, str]:
    """Return (fan_control, fan_min_mode, fan_max_mode) for a device.

    ``fan_control`` is one of off/fixed/auto. ``fan_min_mode`` / ``fan_max_mode``
    bound the auto-escalation ladder (empty = use the ladder's natural ends).
    """
    dev = get_device_by_eid(devices, entity_id)
    if dev is None:
        return (DEFAULT_FAN_CONTROL, "", "")
    mode = dev.get("fan_control", DEFAULT_FAN_CONTROL)
    if mode not in VALID_FAN_CONTROL_MODES:
        mode = DEFAULT_FAN_CONTROL
    return (mode, dev.get("fan_min_mode", ""), dev.get("fan_max_mode", ""))

# Active HVAC modes that indicate real device capabilities.
_ACTIVE_HVAC_MODES = {"heat", "cool", "heat_cool", "auto"}


def has_reliable_hvac_modes(state: Any) -> bool:
    """Check if a device's hvac_modes attribute is trustworthy.

    Some integrations report only limited modes like ``["off", "fan_only"]``
    regardless of device state, hiding actual capabilities (see #100).
    Returns *False* when modes are likely incomplete.
    """
    if state is None:
        return False
    modes = set(state.attributes.get("hvac_modes") or [])
    return bool(modes & _ACTIVE_HVAC_MODES)


def legacy_to_devices(
    thermostats: list[str],
    acs: list[str],
    heating_system_type: str = "",
) -> list[dict]:
    """Create devices[] from legacy thermostats/acs lists.

    heating_system_type is transferred to TRV devices (was previously room-level).
    ACs get "" (no heating system profile).
    """
    devices: list[dict] = []
    for eid in thermostats:
        devices.append(
            {
                "entity_id": eid,
                "type": DEVICE_TYPE_TRV,
                "role": DEVICE_ROLE_AUTO,
                "heating_system_type": heating_system_type,
                "idle_action": IDLE_ACTION_OFF,
                "idle_fan_mode": DEFAULT_IDLE_FAN_MODE,
                "active_fan_mode": DEFAULT_ACTIVE_FAN_MODE,
                "setpoint_mode": SETPOINT_MODE_PROPORTIONAL,
            }
        )
    for eid in acs:
        devices.append(
            {
                "entity_id": eid,
                "type": DEVICE_TYPE_AC,
                "role": DEVICE_ROLE_AUTO,
                "heating_system_type": "",
                "idle_action": IDLE_ACTION_OFF,
                "idle_fan_mode": DEFAULT_IDLE_FAN_MODE,
                "active_fan_mode": DEFAULT_ACTIVE_FAN_MODE,
                "setpoint_mode": SETPOINT_MODE_PROPORTIONAL,
            }
        )
    return devices


def devices_to_legacy(devices: list[dict]) -> tuple[list[str], list[str]]:
    """Extract thermostats/acs lists from devices[].

    TRV -> thermostats, AC -> acs.
    Devices with unknown types or missing entity_id are logged and skipped.
    """
    thermostats: list[str] = []
    acs: list[str] = []
    for d in devices:
        eid = d.get("entity_id")
        if not eid:
            _LOGGER.warning("Skipping device with missing entity_id: %s", d)
            continue
        dtype = d.get("type")
        if dtype == DEVICE_TYPE_TRV:
            thermostats.append(eid)
        elif dtype == DEVICE_TYPE_AC:
            acs.append(eid)
        else:
            _LOGGER.warning("Skipping device with unknown type '%s': %s", dtype, eid)
    return thermostats, acs


def ensure_room_has_devices(room: dict) -> dict:
    """One-time migration + read-time safety net.

    - No 'devices' key: generate from legacy + room-level heating_system_type
    - 'devices' present but inconsistent with legacy: prefer legacy (downgrade recovery)
    - 'devices' present and consistent: regenerate legacy from devices
    Mutates and returns room.
    """
    if "devices" not in room:
        room["devices"] = legacy_to_devices(
            room.get("thermostats", []),
            room.get("acs", []),
            room.get("heating_system_type", ""),
        )
    else:
        # Downgrade recovery: if legacy fields were edited while devices was stale,
        # the legacy entity sets won't match devices. Prefer legacy in that case.
        expected_t, expected_a = devices_to_legacy(room["devices"])
        actual_t = room.get("thermostats", [])
        actual_a = room.get("acs", [])
        if set(expected_t) != set(actual_t) or set(expected_a) != set(actual_a):
            _LOGGER.info(
                "Device list inconsistent with legacy fields for room '%s', "
                "re-generating devices from legacy (downgrade recovery)",
                room.get("area_id", "unknown"),
            )
            room["devices"] = legacy_to_devices(
                actual_t,
                actual_a,
                room.get("heating_system_type", ""),
            )
    # Always regenerate legacy from devices (devices is source of truth after migration)
    thermostats, acs = devices_to_legacy(room["devices"])
    room["thermostats"] = thermostats
    room["acs"] = acs
    # Room-level heating_system_type derived from devices for backend compat
    room["heating_system_type"] = get_room_heating_system_type(room["devices"])
    return room


def get_room_heating_system_type(devices: list[dict]) -> str:
    """Return the most conservative heating_system_type for a room.

    With mixed types (e.g., radiator TRV + underfloor TRV), the one with the
    longest residual heat tau wins:
    underfloor (tau=90min) > radiator (tau=10min) > "" (no residual heat).
    Only TRV devices are considered (ACs/HPs have no heating system profile).
    """
    best = ""
    for d in devices:
        if d.get("type") != DEVICE_TYPE_TRV:
            continue
        hst = d.get("heating_system_type", "")
        if HST_PRIORITY.get(hst, 0) > HST_PRIORITY.get(best, 0):
            best = hst
    return best


def get_all_entity_ids(devices: list[dict]) -> list[str]:
    """All entity_ids from devices, TRVs first for deterministic ordering.

    Preserves relative order within each group (TRV, non-TRV).
    This matters for functions like _read_device_temp that take the first
    entity with a valid value.
    """
    trvs = [d["entity_id"] for d in devices if "entity_id" in d and d.get("type") == DEVICE_TYPE_TRV]
    others = [d["entity_id"] for d in devices if "entity_id" in d and d.get("type") != DEVICE_TYPE_TRV]
    return trvs + others


def get_entity_ids_by_type(devices: list[dict], *types: str) -> list[str]:
    """Entity IDs filtered by type(s)."""
    return [d["entity_id"] for d in devices if "entity_id" in d and d.get("type") in types]


def get_trv_eids(devices: list[dict]) -> list[str]:
    """Shortcut for get_entity_ids_by_type(devices, "trv")."""
    return get_entity_ids_by_type(devices, DEVICE_TYPE_TRV)


def get_ac_eids(devices: list[dict]) -> list[str]:
    """Shortcut for get_entity_ids_by_type(devices, "ac")."""
    return get_entity_ids_by_type(devices, DEVICE_TYPE_AC)


def get_device_by_eid(devices: list[dict], entity_id: str) -> dict | None:
    """Find a single device by entity_id."""
    for d in devices:
        if d.get("entity_id") == entity_id:
            return d
    return None


def is_trv_type(device: dict) -> bool:
    """True if device type is TRV."""
    return device.get("type") == DEVICE_TYPE_TRV


def is_ac_type(device: dict) -> bool:
    """True if device type is AC."""
    return device.get("type") == DEVICE_TYPE_AC


def get_idle_action(devices: list[dict], entity_id: str) -> tuple[str, str]:
    """Return (idle_action, idle_fan_mode) for a device."""
    dev = get_device_by_eid(devices, entity_id)
    if dev is None:
        return (IDLE_ACTION_OFF, DEFAULT_IDLE_FAN_MODE)
    return (
        dev.get("idle_action", IDLE_ACTION_OFF),
        dev.get("idle_fan_mode", DEFAULT_IDLE_FAN_MODE),
    )


# Idle actions applicable per device type. A schedule override naming an
# action outside a device's set is ignored for that device (keeps its config).
_IDLE_ACTION_APPLICABLE: dict[str, set[str]] = {
    DEVICE_TYPE_AC: {IDLE_ACTION_OFF, IDLE_ACTION_FAN_ONLY, IDLE_ACTION_SETBACK},
    DEVICE_TYPE_TRV: {IDLE_ACTION_OFF, IDLE_ACTION_LOW, IDLE_ACTION_SETBACK},
}


def apply_idle_override(devices: list[dict], override: dict[str, str] | None) -> list[dict]:
    """Return a copy of *devices* with a schedule idle override applied.

    The override (``{"idle_action": ..., "idle_fan_mode"?: ...}``) replaces the
    configured idle behavior only for devices where the action is applicable to
    their type; other devices are copied through unchanged. When *override* is
    None or empty the original list is returned (no copy), so the common path
    stays allocation-free.
    """
    if not override or not override.get("idle_action"):
        return devices
    action = override["idle_action"]
    fan = override.get("idle_fan_mode")
    result: list[dict] = []
    for dev in devices:
        applicable = _IDLE_ACTION_APPLICABLE.get(dev.get("type", ""), set())
        if action in applicable:
            new_dev = {**dev, "idle_action": action}
            if fan is not None and action == IDLE_ACTION_FAN_ONLY:
                new_dev["idle_fan_mode"] = fan
            result.append(new_dev)
        else:
            result.append(dev)
    return result


def get_active_fan_mode(devices: list[dict], entity_id: str) -> str:
    """Return the configured active fan mode for a device.

    The active fan mode is applied while the device is actively heating or
    cooling. An empty string (the default) means RoomMind leaves the fan
    speed under the device/firmware's own control, preserving legacy behavior.
    """
    dev = get_device_by_eid(devices, entity_id)
    if dev is None:
        return DEFAULT_ACTIVE_FAN_MODE
    return dev.get("active_fan_mode", DEFAULT_ACTIVE_FAN_MODE)


def get_direct_setpoint_eids(devices: list[dict]) -> set[str]:
    """Return entity IDs of devices with setpoint_mode='direct'."""
    return {d["entity_id"] for d in devices if d.get("entity_id") and d.get("setpoint_mode") == SETPOINT_MODE_DIRECT}


def build_rooms_devices_map(rooms: dict) -> dict[str, list[dict]]:
    """Return {entity_id: devices[]} map across all rooms.

    Used by managers that need to resolve a device's configuration (e.g.
    idle_action) by entity_id without carrying the full rooms dict.
    """
    return {
        d["entity_id"]: room.get("devices", [])
        for room in rooms.values()
        for d in room.get("devices", [])
        if d.get("entity_id")
    }


def migrate_heat_pump_devices(devices: list[dict]) -> bool:
    """Convert any heat_pump devices to ac. Returns True if any were migrated."""
    migrated = False
    for d in devices:
        if d.get("type") == "heat_pump":
            d["type"] = DEVICE_TYPE_AC
            migrated = True
    return migrated


def room_contributes_to_group(
    room_devices: list[dict],
    member_set: set[str],
    active_heat_sources: str | None,
) -> bool:
    """Whether a room's group members are heating-active per orchestration.

    Used by coordinator._collect_member_room_modes to decide whether a
    heating room contributes heating demand to a compressor group's
    master device.

    - ``None``       -> no orchestration data (disabled, missing temp,
                        no TRV+AC, or popped state). Preserve legacy
                        behavior: count the room.
    - ``"none"``     -> orchestration chose nothing (delta_t <= 0) ->
                        False (all devices idled by orchestrator).
    - ``"both"``     -> TRV + AC both active -> True.
    - ``"primary"``  -> only TRVs active -> True iff any group member
                        in this room is a TRV.
    - ``"secondary"``-> only ACs active -> True iff any group member
                        in this room is an AC.

    Unknown string values -> False (fail-safe: master stays idle).
    """
    if active_heat_sources is None:
        return True
    if active_heat_sources == "none":
        return False
    if active_heat_sources == "both":
        return True
    if active_heat_sources == "primary":
        required_type = DEVICE_TYPE_TRV
    elif active_heat_sources == "secondary":
        required_type = DEVICE_TYPE_AC
    else:
        return False  # defensive: unknown orchestration state
    for dev in room_devices:
        eid = dev.get("entity_id", "")
        if eid and eid in member_set and dev.get("type") == required_type:
            return True
    return False
