import { WEEKDAYS } from "../types";
import type {
  ScheduleBlock,
  ScheduleBlockData,
  ScheduleBlocks,
  ScheduleEntry,
  Weekday,
} from "../types";

/**
 * Build weekly blocks with every weekday present, so callers never see
 * undefined days. The single cast here keeps every call site cast-free.
 */
export const mapWeekdays = (blocksOf: (day: Weekday) => ScheduleBlock[]): ScheduleBlocks =>
  Object.fromEntries(WEEKDAYS.map((day) => [day, blocksOf(day)])) as ScheduleBlocks;

export const emptyBlocks = (): ScheduleBlocks => mapWeekdays(() => []);

export const isInlineSchedule = (schedule: ScheduleEntry): boolean => schedule.blocks !== undefined;

const toSeconds = (time: string): number => {
  const [hours = 0, minutes = 0, seconds = 0] = time.split(":").map(Number);
  return hours * 3600 + minutes * 60 + seconds;
};

/** Normalize "HH:MM" from a time input to the "HH:MM:SS" the backend stores. */
export const padTime = (time: string): string => (time.length === 5 ? `${time}:00` : time);

/** Trim stored "HH:MM:SS" for `<input type="time">`, which rejects seconds. */
export const trimTime = (time: string): string => time.slice(0, 5);

export const sortBlocks = (blocks: ScheduleBlock[]): ScheduleBlock[] =>
  [...blocks].sort((first, second) => toSeconds(first.from) - toSeconds(second.from));

/** Indexes of blocks overlapping any earlier block on the same day. */
export const overlappingBlocks = (blocks: ScheduleBlock[]): Set<number> => {
  const conflicting = new Set<number>();
  blocks.forEach((block, index) => {
    blocks.forEach((other, otherIndex) => {
      if (otherIndex === index) return;
      if (
        toSeconds(block.from) < toSeconds(other.to) &&
        toSeconds(other.from) < toSeconds(block.to)
      ) {
        conflicting.add(index);
      }
    });
  });
  return conflicting;
};

export const weekdayOf = (date: Date): Weekday => WEEKDAYS[(date.getDay() + 6) % 7];

/** The block covering `date`, mirroring find_active_block on the backend. */
export const findActiveBlock = (
  blocks: ScheduleBlocks | undefined,
  date: Date = new Date(),
): ScheduleBlock | undefined => {
  if (!blocks) return undefined;
  const now = date.getHours() * 3600 + date.getMinutes() * 60 + date.getSeconds();
  return (blocks[weekdayOf(date)] ?? []).find(
    (block) => toSeconds(block.from) <= now && now < toSeconds(block.to),
  );
};

/** Block data keys that only mean anything for a given device type. */
const TYPE_ONLY_KEYS = {
  ac: [
    "ac_idle_action",
    "idle_fan_mode",
    "fan_control",
    "fan_min_mode",
    "fan_max_mode",
    "heat_swing_mode",
    "cool_swing_mode",
    "dehumidify",
  ],
  trv: ["trv_idle_action"],
} as const satisfies Record<string, readonly (keyof ScheduleBlockData)[]>;

/**
 * Drop block settings the target room's devices cannot act on, so a schedule
 * copied from an AC room does not carry dead fan keys into a radiator-only one.
 */
export const stripInapplicableData = (
  blocks: ScheduleBlocks,
  deviceTypes: readonly string[],
): ScheduleBlocks => {
  const dropped = Object.entries(TYPE_ONLY_KEYS)
    .filter(([deviceType]) => !deviceTypes.includes(deviceType))
    .flatMap(([, keys]) => keys as readonly string[]);
  if (dropped.length === 0) return blocks;

  return mapWeekdays((day) =>
    (blocks[day] ?? []).map((block) => ({
      ...block,
      data: Object.fromEntries(
        Object.entries(block.data ?? {}).filter(([key]) => !dropped.includes(key)),
      ),
    })),
  );
};
