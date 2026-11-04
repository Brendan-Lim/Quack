import { afterEach, expect, it, vi } from "vitest";
import { buildFixture, downloadFixture } from "@/lib/qaFixture";

afterEach(() => vi.unstubAllEnvs());

it("does not create a download link in production even when called directly", () => {
  vi.stubEnv("NODE_ENV", "production");
  const create = vi.spyOn(document, "createElement");
  downloadFixture(buildFixture([], null, 0));
  expect(create).not.toHaveBeenCalled();
  create.mockRestore();
});
