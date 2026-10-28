import { describe, expect, it } from "vitest";
import { buildUrl } from "../api/client";
import { resolveApiConfig } from "../config";
import {
  MISSING,
  formatDate,
  formatDateTime,
  formatDays,
  formatDaysFromHours,
  formatNumber,
  formatPercent,
  formatSigned,
} from "../format";

describe("format", () => {
  it("renders missing values as an em dash, never zero", () => {
    for (const fn of [formatNumber, formatPercent, formatDaysFromHours, formatDays, formatSigned]) {
      expect(fn(null)).toBe(MISSING);
      expect(fn(undefined)).toBe(MISSING);
      expect(fn(Number.NaN)).toBe(MISSING);
    }
    expect(formatDate(null)).toBe(MISSING);
    expect(formatDateTime("garbage")).toBe(MISSING);
  });

  it("formats real zeros and values", () => {
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(1234567)).toBe("1,234,567");
    expect(formatPercent(0.756)).toBe("75.6%");
    expect(formatPercent(0)).toBe("0.0%");
    expect(formatDaysFromHours(0.8)).toBe("0.8 hours");
    expect(formatDaysFromHours(36)).toBe("36.0 hours");
    expect(formatDaysFromHours(72)).toBe("3.0 days");
    expect(formatDays(1)).toBe("1 day");
    expect(formatSigned(5)).toBe("+5");
  });

  it("formats dates in UTC", () => {
    expect(formatDate("2026-10-28")).toBe("Oct 28, 2026");
    expect(formatDateTime("2026-10-28T23:30:00Z")).toContain("UTC");
    expect(formatDateTime("2026-10-28T23:30:00Z")).toContain("23:30");
  });
});

describe("config", () => {
  it("requires https and a non-local host in production", () => {
    expect(resolveApiConfig("https://api-xyz.a.run.app/", true)).toEqual({ baseUrl: "https://api-xyz.a.run.app", error: null });
    expect(resolveApiConfig("http://api.example.com", true).error).toMatch(/HTTPS/);
    expect(resolveApiConfig("https://localhost:8000", true).error).toMatch(/localhost/);
    expect(resolveApiConfig("", true).error).toMatch(/VITE_API_BASE_URL/);
    expect(resolveApiConfig("not a url", true).error).toMatch(/not a valid URL/);
    expect(resolveApiConfig("https://user:pw@api.example.com", true).error).toMatch(/credentials/);
  });

  it("uses the dev fallback only outside production", () => {
    expect(resolveApiConfig(undefined, false, "http://localhost:8000").baseUrl).toBe("http://localhost:8000");
    expect(resolveApiConfig(undefined, true, "http://localhost:8000").baseUrl).toBeNull();
  });
});

describe("buildUrl", () => {
  it("drops empty parameters and encodes values", () => {
    const url = buildUrl("https://api.example.com/", "/api/v1/repositories", { search: "a b", language: "", repository_id: null, limit: 5 });
    expect(url).toBe("https://api.example.com/api/v1/repositories?search=a+b&limit=5");
  });
});
