import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { clearApiCache } from "../api/useApi";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);
window.scrollTo = () => {};

afterEach(() => {
  cleanup();
  clearApiCache();
  vi.restoreAllMocks();
});
