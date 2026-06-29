"""Proportional automatic fan-speed control for RoomMind AC devices.

Treats a device's fan modes as an ordered ladder (weakest -> strongest) and
picks a speed *proportional to demand*, so the fan eases off smoothly as the
room approaches target instead of staying pinned at maximum:

* Far above target (or high MPC power demand) -> top of the allowed ladder.
* At or below target (idle / drifting up) -> bottom of the ladder (e.g. Mute).
* In between -> linearly interpolated step.

Demand is the smaller of two signals:
  - the temperature error toward target (works even before the model is
    confident, when MPC runs bang-bang at full power), and
  - the MPC ``power_fraction`` (from the learned thermal model, which near
    target is small) — so once the model is active the fan tracks it too.

A minimum interval between changes damps jitter on sensor noise. State is held
in memory per entity (like ``WindowManager``); on restart it re-converges from
the current temperature within a cycle or two.
"""

from __future__ import annotations

import time

# Temperature error (degC above target) at/above which the fan runs flat-out.
# Below 0 (room at/under target) the fan drops to its minimum.
FAN_FULL_BAND_C = 1.0

# Minimum seconds between fan-level changes — damps oscillation between adjacent
# steps when the sensor wobbles by ±0.1 °C near a step boundary.
CHANGE_MIN_S = 120.0


class FanSpeedManager:
    """Per-entity proportional fan-speed controller."""

    def __init__(self) -> None:
        self._level: dict[str, int] = {}
        self._changed_at: dict[str, float] = {}

    def current_level(self, entity_id: str) -> int | None:
        """Return the current ladder index for *entity_id*, or None if unseen."""
        return self._level.get(entity_id)

    def evaluate(
        self,
        entity_id: str,
        ladder: list[str],
        *,
        deficit: float,
        power_fraction: float = 1.0,
        min_idx: int = 0,
        max_idx: int | None = None,
        now: float | None = None,
    ) -> str | None:
        """Pick the proportional fan mode for this cycle.

        Args:
            entity_id: the climate entity.
            ladder: fan modes ordered weakest -> strongest.
            deficit: temperature gap in the direction needing more power
                (cooling: current - target; heating: target - current). Positive
                means the room has not reached target yet; <= 0 means at/below
                target -> minimum fan.
            power_fraction: MPC power demand (0..1). Caps the speed so a model
                that only wants gentle output keeps the fan low even when the
                error is larger. Bang-bang fallback passes 1.0, so the error
                term governs until the model is confident.
            min_idx / max_idx: inclusive ladder bounds.
            now: injectable clock for testing.

        Returns:
            The chosen fan mode string, or None when there is no ladder.
        """
        if not ladder:
            return None
        hi = len(ladder) - 1 if max_idx is None else max_idx
        hi = max(0, min(hi, len(ladder) - 1))
        lo = max(0, min(min_idx, hi))

        # Demand fraction: how far above target (clamped 0..1), capped by the
        # MPC power demand so the model can hold the fan down near target.
        err_frac = max(0.0, min(1.0, deficit / FAN_FULL_BAND_C)) if FAN_FULL_BAND_C > 0 else 0.0
        frac = min(err_frac, max(0.0, min(1.0, power_fraction)))
        target_level = lo + round(frac * (hi - lo))
        target_level = max(lo, min(hi, target_level))

        level = self._level.get(entity_id)
        if level is None:
            level = target_level
            self._changed_at[entity_id] = time.time() if now is None else now
        else:
            level = max(lo, min(hi, level))  # honor possibly-tightened bounds
            if target_level != level:
                now = time.time() if now is None else now
                # Bounds clamp is immediate; demand-driven moves are rate-limited.
                if level < lo or level > hi or (now - self._changed_at.get(entity_id, 0.0)) >= CHANGE_MIN_S:
                    level = target_level
                    self._changed_at[entity_id] = now

        self._level[entity_id] = level
        return ladder[level]

    def reset(self, entity_id: str) -> None:
        """Forget fan state for *entity_id* (e.g. config changed)."""
        self._level.pop(entity_id, None)
        self._changed_at.pop(entity_id, None)
