/**
 * Shared device utilities for the unified device model.
 * Mirrors backend logic from custom_components/roommind/utils/device_utils.py.
 */

import type { DeviceConfig } from "../types";

/** Louver position keys per running direction, shared by AC devices and schedule blocks. */
export const SWING_MODE_KEYS = [
  "heat_swing_mode",
  "cool_swing_mode",
] as const satisfies readonly (keyof DeviceConfig)[];
export type SwingModeKey = (typeof SWING_MODE_KEYS)[number];

/** Find a top or upward louver position by keyword (top, up, highest). */
export const resolveTopSwingMode = (swingModes: readonly string[]): string | undefined => {
  const keywords = ["top", "up", "highest"];
  return swingModes.find((mode) => {
    const lowered = mode.toLowerCase();
    return keywords.some((keyword) => lowered.includes(keyword));
  });
};

const HST_PRIORITY: Record<string, number> = { underfloor: 2, radiator: 1, "": 0 };

/**
 * Resolve room-level heating_system_type from devices.
 * Most conservative type wins (longest residual heat tau).
 * Only TRV devices are considered.
 */
export function resolveHeatingSystemType(devices: DeviceConfig[]): string {
  let best = "";
  for (const d of devices) {
    if (d.type !== "trv") continue;
    const hst = d.heating_system_type ?? "";
    if ((HST_PRIORITY[hst] ?? 0) > (HST_PRIORITY[best] ?? 0)) best = hst;
  }
  return best;
}
