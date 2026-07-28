import type { OverrideType } from "../types";

/** Override presets in display order. */
export const OVERRIDE_TYPES = [
  "boost",
  "eco",
  "turbo",
  "custom",
] as const satisfies readonly OverrideType[];

export const OVERRIDE_ICONS = {
  boost: "mdi:fire",
  eco: "mdi:leaf",
  turbo: "mdi:snowflake",
  custom: "mdi:thermometer",
} as const satisfies Record<OverrideType, string>;

export const OVERRIDE_LABEL_KEYS = {
  boost: "override.comfort",
  eco: "override.eco",
  turbo: "override.turbo",
  custom: "override.custom",
} as const satisfies Record<OverrideType, string>;
