"""Active dehumidification manager for RoomMind.

Drives an AC's "dry" hvac_mode from room air relative humidity.  This is a
separate mechanism from mold prevention (see :mod:`mold_manager`): prevention
*raises* the heat target so cold wall surfaces stay above the dew point, while
this manager runs the compressor to remove water from the air outright.

While a room is dehumidifying it is withheld from the normal MPC command
cycle, because "dry" is not one of the modes the controller reasons about.
For the same reason the EKF must not train on these periods — the AC is
cooling as a side effect of drying, and labelling that as "idle" would teach
the model that the room cools on its own.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from ..const import (
    DEFAULT_DEHUMIDIFY_HUMIDITY_THRESHOLD,
    DEFAULT_DEHUMIDIFY_STOP_THRESHOLD,
    DEHUMIDIFY_HVAC_MODE,
    DEHUMIDIFY_MIN_MINUTES,
    make_roommind_context,
)
from ..utils.temp_utils import celsius_to_ha_temp

_LOGGER = logging.getLogger(__name__)


@dataclass
class DehumidifyResult:
    """Result of a dehumidification evaluation for a room."""

    active: bool = False
    threshold: float | None = None


def supports_dry(state) -> bool:
    """Return True when a climate entity reports the "dry" hvac_mode.

    Deliberately reads the reported hvac_modes rather than the assumed set
    used elsewhere for ACs: sending "dry" to a device that never advertised it
    has no sensible fallback, so an unknown capability means "don't".
    """
    return _supports_mode(state, DEHUMIDIFY_HVAC_MODE)


def _supports_mode(state, mode: str) -> bool:
    """Return True when a climate entity reports *mode* in its hvac_modes."""
    if state is None:
        return False
    return mode in (state.attributes.get("hvac_modes") or [])


class DehumidifyManager:
    """Decides when a room should run its AC in dry mode, and applies it."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._active: dict[str, bool] = {}
        self._active_since: dict[str, float] = {}

    def evaluate(
        self,
        area_id: str,
        settings: dict,
        current_humidity: float | None,
        ac_eids: list[str],
    ) -> DehumidifyResult:
        """Decide whether *area_id* should be dehumidifying this cycle.

        The two thresholds are global (like mold detection): every room with a
        humidity reading and a dry-capable AC follows them.  Rooms that lack
        either simply never activate.  Drying starts at/above *start* and runs
        until RH falls to/below *stop* — *stop* is the humidity level the user
        wants the room brought down to.
        """
        result = DehumidifyResult()

        if not settings.get("dehumidify_enabled"):
            self._deactivate(area_id)
            return result

        start = float(
            settings.get(
                "dehumidify_humidity_threshold",
                DEFAULT_DEHUMIDIFY_HUMIDITY_THRESHOLD,
            )
        )
        stop = float(
            settings.get(
                "dehumidify_stop_threshold",
                DEFAULT_DEHUMIDIFY_STOP_THRESHOLD,
            )
        )
        # Guard against a misconfigured stop >= start, which would otherwise
        # chatter: keep at least a 1% band below the start threshold.
        stop = min(stop, start - 1.0)
        result.threshold = start

        if current_humidity is None:
            self._deactivate(area_id)
            return result

        capable = [eid for eid in ac_eids if supports_dry(self.hass.states.get(eid))]
        if not capable:
            if self._active.get(area_id):
                _LOGGER.debug(
                    "Room '%s': dehumidification enabled but no AC reports a 'dry' mode",
                    area_id,
                )
            self._deactivate(area_id)
            return result

        was_active = self._active.get(area_id, False)
        now = time.time()

        if was_active:
            # Hold until RH reaches the stop level *and* the compressor has had
            # its minimum run — short dry bursts are hard on the hardware and
            # barely move humidity.
            elapsed_minutes = (now - self._active_since.get(area_id, now)) / 60.0
            if current_humidity <= stop and elapsed_minutes >= DEHUMIDIFY_MIN_MINUTES:
                self._deactivate(area_id)
            else:
                result.active = True
        elif current_humidity >= start:
            self._active[area_id] = True
            self._active_since[area_id] = now
            result.active = True
            _LOGGER.debug(
                "Room '%s': dehumidification on (RH %.0f%% >= %.0f%%, drying to %.0f%%)",
                area_id,
                current_humidity,
                start,
                stop,
            )

        return result

    def _deactivate(self, area_id: str) -> None:
        if self._active.pop(area_id, None):
            _LOGGER.debug("Room '%s': dehumidification off", area_id)
        self._active_since.pop(area_id, None)

    async def async_apply(
        self,
        area_id: str,
        ac_eids: list[str],
        target_temp: float | None = None,
        fan_caps: dict[str, str] | None = None,
        hvac_mode: str = DEHUMIDIFY_HVAC_MODE,
    ) -> None:
        """Drive every capable AC in *area_id* for dehumidification.

        *hvac_mode* is "dry" by default; a schedule slot can ask for "cool"
        instead (``dehumidify_cool`` overnight), where the AC's thermostat stops
        at the target instead of Dry's fixed program that overcools.

        *target_temp* (°C) is the room's scheduled target, passed through so the
        AC dries toward the temperature the user actually asked for.  The MPC's
        proportional setpoint is deliberately not used here: it can be driven
        down to min_temp to force cooling, which would freeze the room for what
        is a humidity job.

        *fan_caps* maps entity_id -> a fan-speed label (the effective
        ``fan_max_mode`` from the schedule/device).  This AC keeps its fan
        controllable and otherwise runs it on Auto — which blows hard and makes
        the room feel cold.  Setting the fan to the configured ceiling keeps it
        quiet and gentle.  No entry (or no cap configured) leaves the fan alone.
        """
        fan_caps = fan_caps or {}
        for eid in ac_eids:
            state = self.hass.states.get(eid)
            if not _supports_mode(state, hvac_mode):
                continue
            if state is None or state.state != hvac_mode:
                try:
                    await self.hass.services.async_call(
                        "climate",
                        "set_hvac_mode",
                        {"entity_id": eid, "hvac_mode": hvac_mode},
                        blocking=True,
                        context=make_roommind_context(),
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.warning(
                        "Area '%s': climate.set_hvac_mode(%s) failed on '%s'",
                        area_id,
                        hvac_mode,
                        eid,
                        exc_info=True,
                    )
                    continue
            if target_temp is not None:
                await self._async_set_target(area_id, eid, target_temp)
            cap = fan_caps.get(eid)
            if cap:
                await self._async_set_fan(area_id, eid, cap)

    async def _async_set_target(self, area_id: str, entity_id: str, target_c: float) -> None:
        """Send the scheduled target to a drying AC, clamped to its own limits.

        Many ACs restrict or ignore the setpoint in dry mode; a rejected call
        is logged and left at that, since the dry mode itself is what matters.
        """
        state = self.hass.states.get(entity_id)
        setpoint = celsius_to_ha_temp(self.hass, target_c)

        for attr, clamp in (("min_temp", max), ("max_temp", min)):
            limit = state.attributes.get(attr) if state else None
            if limit is not None:
                try:
                    setpoint = clamp(setpoint, float(limit))
                except (ValueError, TypeError):
                    pass

        current = state.attributes.get("temperature") if state else None
        if current is not None and round(float(current), 1) == round(setpoint, 1):
            return

        try:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": entity_id, "temperature": round(setpoint, 1)},
                blocking=True,
                context=make_roommind_context(),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Area '%s': climate.set_temperature(%.1f) failed on '%s' while drying",
                area_id,
                setpoint,
                entity_id,
                exc_info=True,
            )

    async def _async_set_fan(self, area_id: str, entity_id: str, fan_mode: str) -> None:
        """Cap the drying AC's fan speed. Best-effort, skipped when unsupported."""
        state = self.hass.states.get(entity_id)
        fan_modes = (state.attributes.get("fan_modes") or []) if state else []
        if fan_mode not in fan_modes:
            _LOGGER.debug(
                "Area '%s': device '%s' does not support fan_mode '%s' (available: %s)",
                area_id,
                entity_id,
                fan_mode,
                fan_modes,
            )
            return
        if state is not None and state.attributes.get("fan_mode") == fan_mode:
            return
        try:
            await self.hass.services.async_call(
                "climate",
                "set_fan_mode",
                {"entity_id": entity_id, "fan_mode": fan_mode},
                blocking=True,
                context=make_roommind_context(),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Area '%s': climate.set_fan_mode('%s') failed on '%s' while drying",
                area_id,
                fan_mode,
                entity_id,
                exc_info=True,
            )

    def is_active(self, area_id: str) -> bool:
        """Return True when *area_id* is currently dehumidifying."""
        return self._active.get(area_id, False)

    def remove_room(self, area_id: str) -> None:
        """Clean up state for a removed room."""
        self._deactivate(area_id)
