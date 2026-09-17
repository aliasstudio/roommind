"""Detects a commanded mode that produces no temperature response.

A device can accept every command and still do nothing: an AC that reports
``cool`` over its cloud API while the compressor never starts, a TRV whose
valve is mechanically stuck. RoomMind then holds the mode indefinitely with
no signal to the user. This watchdog compares the room temperature against
the one observed when the mode started and, once the grace period expires
without progress, asks for a power-cycle and then for a notification.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from ..const import (
    MODE_COOLING,
    MODE_HEATING,
    WATCHDOG_GRACE_S,
    WATCHDOG_MIN_POWER_FRACTION,
    WATCHDOG_MIN_PROGRESS_C,
    WATCHDOG_TARGET_BAND_C,
)


class WatchdogVerdict(Enum):
    """Outcome of one evaluation cycle."""

    OK = "ok"
    REARM = "rearm"
    ALERT = "alert"


@dataclass
class _Episode:
    """One uninterrupted stretch of a single mode at (near) full power."""

    mode: str
    started_at: float
    baseline_temp: float
    rearmed: bool = False
    alerted: bool = False


@dataclass
class WatchdogState:
    """What the watchdog observed for a room, for diagnostics and live state."""

    stalled: bool = False
    stalled_for_s: int = 0
    progress_c: float = 0.0


class EffectivenessWatchdog:
    """Per-room tracking of "mode commanded, nothing happens"."""

    def __init__(self) -> None:
        self._episodes: dict[str, _Episode] = {}

    def evaluate(
        self,
        area_id: str,
        mode: str,
        current_temp: float | None,
        power_fraction: float,
        target_temp: float | None,
        *,
        controllable: bool,
    ) -> WatchdogVerdict:
        """Judge the current cycle and advance the room's episode.

        The episode restarts whenever the premise of the measurement changes —
        a different mode, a room RoomMind no longer drives, a device throttled
        below full power (modulating down is evidence it works, or that the
        room is near target), a room already within reach of its target, or a
        missing sensor reading.
        """
        if (
            not controllable
            or current_temp is None
            or mode not in (MODE_HEATING, MODE_COOLING)
            or power_fraction < WATCHDOG_MIN_POWER_FRACTION
            or target_temp is None
            or self._remaining_gap(mode, current_temp, target_temp) < WATCHDOG_TARGET_BAND_C
        ):
            self.reset(area_id)
            return WatchdogVerdict.OK

        episode = self._episodes.get(area_id)
        if episode is None or episode.mode != mode:
            self._episodes[area_id] = _Episode(mode, time.time(), current_temp)
            return WatchdogVerdict.OK

        if self._progress(episode, current_temp) >= WATCHDOG_MIN_PROGRESS_C:
            # The room is responding: re-baseline so a later stall is caught too.
            self._episodes[area_id] = _Episode(mode, time.time(), current_temp)
            return WatchdogVerdict.OK

        if time.time() - episode.started_at < WATCHDOG_GRACE_S:
            return WatchdogVerdict.OK

        if not episode.rearmed:
            episode.rearmed = True
            episode.started_at = time.time()
            episode.baseline_temp = current_temp
            return WatchdogVerdict.REARM

        if not episode.alerted:
            episode.alerted = True
            return WatchdogVerdict.ALERT

        return WatchdogVerdict.OK

    def get_state(self, area_id: str, current_temp: float | None) -> WatchdogState:
        """Current stall state for the room, for live state and diagnostics."""
        episode = self._episodes.get(area_id)
        if episode is None or current_temp is None:
            return WatchdogState()
        progress = self._progress(episode, current_temp)
        elapsed = time.time() - episode.started_at
        return WatchdogState(
            stalled=episode.rearmed and progress < WATCHDOG_MIN_PROGRESS_C,
            stalled_for_s=round(elapsed),
            progress_c=round(progress, 2),
        )

    def reset(self, area_id: str) -> None:
        """Forget the room's episode (mode ended, or premise no longer holds)."""
        self._episodes.pop(area_id, None)

    @staticmethod
    def _remaining_gap(mode: str, current_temp: float, target_temp: float) -> float:
        """Distance still to cover toward the target, in °C (negative past it)."""
        delta = target_temp - current_temp
        return delta if mode == MODE_HEATING else -delta

    @staticmethod
    def _progress(episode: _Episode, current_temp: float) -> float:
        """Temperature movement in the commanded direction, in °C."""
        delta = current_temp - episode.baseline_temp
        return delta if episode.mode == MODE_HEATING else -delta
