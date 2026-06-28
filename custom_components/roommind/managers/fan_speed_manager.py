"""Automatic fan-speed escalation for RoomMind AC devices.

Treats a device's fan modes as an ordered ladder (weakest -> strongest) and
walks up or down it based on the MPC controller's demand signal:

* When the optimizer is asking for (near) full power yet the room still sits
  above target for a sustained period, the current speed is not delivering
  enough cooling -> step up.
* When demand is low or the room is already at/below target, a gentler speed
  suffices -> step down (so a bedroom can settle on "Mute" overnight).

The decision uses ``power_fraction`` (computed by the MPC optimizer from the
learned thermal model) plus the live temperature error, so it leans on the
data the integration already collects without expanding the EKF state.

State is held in memory per entity (like ``WindowManager``); on restart the
ladder resumes from its lower bound and re-converges within minutes.
"""

from __future__ import annotations

import time

# Demand thresholds (fraction of max MPC power).
PF_ESCALATE = 0.9  # optimizer pinned near full power -> capacity may be short
PF_DEESCALATE = 0.35  # low demand -> a gentler speed is plenty

# Temperature error thresholds (degC, in the "needs more power" direction).
ERR_ESCALATE = 0.3  # still this far from target -> not keeping up
ERR_DEESCALATE = 0.2  # at/below target by this margin -> ease off

# Minimum dwell time between steps (seconds). Escalation reacts faster than
# de-escalation to avoid hunting and keep the room comfortable.
DWELL_ESCALATE_S = 8 * 60
DWELL_DEESCALATE_S = 15 * 60


class FanSpeedManager:
    """Per-entity auto fan-speed escalation state machine."""

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
        active: bool,
        deficit: float,
        power_fraction: float,
        min_idx: int = 0,
        max_idx: int | None = None,
        now: float | None = None,
    ) -> str | None:
        """Pick the fan mode for this cycle, or None to leave the fan alone.

        Args:
            entity_id: the climate entity.
            ladder: fan modes ordered weakest -> strongest.
            active: True when the device is actively heating/cooling this cycle.
            deficit: temperature gap in the direction needing more power
                (cooling: current - target; heating: target - current). Positive
                means the room has not reached target yet.
            power_fraction: MPC power demand for this cycle (0..1).
            min_idx / max_idx: inclusive ladder bounds for escalation.
            now: injectable clock for testing.

        Returns:
            The chosen fan mode string, or None when there is no ladder or the
            device is idle (idle fan handling is the caller's concern).
        """
        if not ladder:
            return None
        hi = len(ladder) - 1 if max_idx is None else max_idx
        hi = max(0, min(hi, len(ladder) - 1))
        lo = max(0, min(min_idx, hi))

        level = self._level.get(entity_id)
        if level is None:
            level = lo
        level = max(lo, min(hi, level))

        # While idle we do not drive the active ladder, but we keep the stored
        # level so the next active cycle resumes where it left off.
        if not active:
            self._level[entity_id] = level
            return None

        now = time.time() if now is None else now
        dwell = now - self._changed_at.get(entity_id, 0.0)

        if (
            level < hi
            and power_fraction >= PF_ESCALATE
            and deficit > ERR_ESCALATE
            and dwell >= DWELL_ESCALATE_S
        ):
            level += 1
            self._changed_at[entity_id] = now
        elif (
            level > lo
            and (deficit < -ERR_DEESCALATE or power_fraction <= PF_DEESCALATE)
            and dwell >= DWELL_DEESCALATE_S
        ):
            level -= 1
            self._changed_at[entity_id] = now

        self._level[entity_id] = level
        return ladder[level]

    def reset(self, entity_id: str) -> None:
        """Forget escalation state for *entity_id* (e.g. config changed)."""
        self._level.pop(entity_id, None)
        self._changed_at.pop(entity_id, None)
