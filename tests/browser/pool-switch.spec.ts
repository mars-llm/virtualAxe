import { expect, Page, test } from "@playwright/test";

const TARGET_POOL_HOST = process.env.TARGET_POOL_HOST;
const TARGET_POOL_PORT = process.env.TARGET_POOL_PORT ?? "3333";
const TARGET_POOL_USER = process.env.TARGET_POOL_USER;
const TARGET_POOL_PASS = process.env.TARGET_POOL_PASS ?? "x";
const POOL_SWITCH_SCREENSHOT = process.env.POOL_SWITCH_SCREENSHOT;
const SOURCE_NAME = process.env.SOURCE_NAME ?? "bitaxe";

async function waitForSystemPatch(page: Page, action: () => Promise<void>): Promise<void> {
  const responsePromise = page.waitForResponse((response) => {
    return response.request().method() === "PATCH" && response.url().endsWith("/api/system");
  });
  await action();
  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
}

async function fillPoolSection(page: Page, prefix: "stratum" | "fallbackStratum"): Promise<void> {
  await page.locator(`#${prefix}URL`).fill(TARGET_POOL_HOST ?? "");
  await page.locator(`#${prefix}Port`).fill(TARGET_POOL_PORT);
  await page.locator(`#${prefix}User`).fill(TARGET_POOL_USER ?? "");
  await page.locator(`#${prefix}Password`).fill(TARGET_POOL_PASS);
}

async function openPoolPage(page: Page): Promise<void> {
  await page.goto("/");
  const poolLink = page.getByRole("link", { name: /Pool$/ });
  await expect(poolLink).toBeVisible({ timeout: 15_000 });
  await poolLink.click();
  await expect(page).toHaveURL(/#\/pool$/);
}

test("axeos pool page saves and restarts on pool switch", async ({ page }) => {
  test.skip(SOURCE_NAME !== "bitaxe", "Bitaxe-only AxeOS pool switch form contract");
  test.skip(!TARGET_POOL_HOST || !TARGET_POOL_USER, "set TARGET_POOL_HOST and TARGET_POOL_USER to run the live pool switch");

  await openPoolPage(page);
  await expect(page.locator("#stratumURL")).toBeVisible({ timeout: 15_000 });

  await fillPoolSection(page, "stratum");
  await fillPoolSection(page, "fallbackStratum");

  await waitForSystemPatch(page, async () => {
    await page.getByRole("button", { name: /^Save$/ }).click();
  });

  await expect(page.getByText(/Saved pool settings/i)).toBeVisible();

  const restartRequest = page.waitForRequest((request) => {
    return request.method() === "POST" && request.url().endsWith("/api/system/restart");
  });

  await page.getByRole("button", { name: /^Restart$/ }).click();
  await restartRequest;

  if (POOL_SWITCH_SCREENSHOT) {
    await page.screenshot({ path: POOL_SWITCH_SCREENSHOT, fullPage: true });
  }
});
