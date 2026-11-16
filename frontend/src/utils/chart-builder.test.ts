import { describe, expect, it } from "vitest";
import { fanLevelNames, fanRank } from "./chart-builder";

describe("fanRank", () => {
  it("grades compound labels before their parts", () => {
    expect(fanRank("Mid-High")).toBe(5);
    expect(fanRank("medium")).toBe(4);
    expect(fanRank("Silent")).toBe(1);
    expect(fanRank("Turbo")).toBe(7);
  });

  it("leaves ungraded labels out", () => {
    expect(fanRank("Auto")).toBeNull();
    expect(fanRank("diffuse")).toBeNull();
    expect(fanRank("")).toBeNull();
  });
});

describe("fanLevelNames", () => {
  it("keeps the first label seen per level", () => {
    const points = ["low", "Low", "high", "auto"].map((fan_mode) => ({ fan_mode }));
    expect([...fanLevelNames(points)]).toEqual([
      [2, "low"],
      [6, "high"],
    ]);
  });
});
