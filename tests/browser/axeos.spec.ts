import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { Locator, Page, expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

const BASE_URL = process.env.BASE_URL ?? "http://127.0.0.1:18080";
const SOURCE_NAME = process.env.SOURCE_NAME ?? "bitaxe";

type SystemInfo = {
  ASICModel?: string;
  coreVoltage: number;
  deviceModel?: string;
  fallbackStratumPort: number;
  fallbackStratumURL: string;
  fallbackStratumUser: string;
  frequency: number;
  hostname: string;
  overclockEnabled: number;
  stratumPort: number;
  stratumURL: string;
  stratumUser: string;
};

type AsicSettings = {
  frequencyOptions: number[];
};

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(new URL(path, BASE_URL), {
    headers: { Accept: "application/json" },
  });
  expect(response.ok).toBeTruthy();
  return (await response.json()) as T;
}

async function getSystemInfo(): Promise<SystemInfo> {
  return getJson<SystemInfo>("/api/system/info");
}

async function getAsicSettings(): Promise<AsicSettings> {
  return getJson<AsicSettings>("/api/system/asic");
}

async function patchSystem(update: Record<string, number | string>): Promise<void> {
  const url = new URL("/api/system", BASE_URL);
  const body = JSON.stringify(update);
  const sendRequest = url.protocol === "https:" ? httpsRequest : httpRequest;

  const statusCode = await new Promise<number | undefined>((resolve, reject) => {
    const request = sendRequest(
      url,
      {
        method: "PATCH",
        headers: {
          Accept: "application/json",
          "Content-Length": String(Buffer.byteLength(body)),
          "Content-Type": "application/json",
        },
      },
      (response) => {
        response.resume();
        response.on("end", () => resolve(response.statusCode));
      },
    );

    request.on("error", reject);
    request.write(body);
    request.end();
  });

  expect(statusCode).toBeGreaterThanOrEqual(200);
  expect(statusCode).toBeLessThan(300);
}

async function waitForSystemFields(expected: Record<string, number | string>): Promise<void> {
  await expect
    .poll(async () => {
      const payload = await getSystemInfo();
      return Object.fromEntries(
        Object.keys(expected).map((key) => [key, payload[key as keyof SystemInfo]]),
      );
    }, { timeout: 60_000 })
    .toEqual(expected);
}

async function waitForSystemPatch(page: Page, action: () => Promise<void>): Promise<void> {
  const responsePromise = page.waitForResponse((response) => {
    return response.request().method() === "PATCH" && response.url().endsWith("/api/system");
  });
  await action();
  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
}

async function saveSystemForm(page: Page): Promise<void> {
  await waitForSystemPatch(page, async () => {
    await page.getByRole("button", { name: /^Save$/ }).click();
  });
}

function nextDistinctOption(options: number[], current: number): number {
  const nextValue = options.find((value) => value !== current);
  if (nextValue === undefined) {
    throw new Error(`No alternate option found for current value ${current}`);
  }
  return nextValue;
}

function temporaryHostname(originalHostname: string): string {
  const suffix = originalHostname.endsWith("-ui-check") ? "-ui-restore" : "-ui-check";
  const prefixLength = Math.max(1, 32 - suffix.length);
  return `${originalHostname.slice(0, prefixLength)}${suffix}`;
}

test("axeos dashboard loads", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveTitle(/Bitaxe|AxeOS|Nerd/i);
  await expect(page.locator("body")).toContainText(/Dashboard|Hashrate|Pool|Nerd/i, { timeout: 15_000 });

  if (SOURCE_NAME === "nerdnos") {
    const info = await getSystemInfo();
    expect(info.deviceModel).toBe("virtualAxe Gamma");
    expect(info.ASICModel).toBe("BM1370");
  } else {
    await expect(page.getByText(/Dashboard/i)).toBeVisible();
  }
});

async function openPoolSettings(page: Page) {
  await page.goto("/");
  const poolLink = page.getByRole("link", { name: /Pool$/ });
  await expect(poolLink).toBeVisible({ timeout: 15_000 });
  await poolLink.click();
  await expect(page).toHaveURL(/#\/pool$/);
}

async function openSettingsPage(page: Page) {
  await page.goto("/");
  const settingsLink = page.getByRole("link", { name: /Settings/i });
  await expect(settingsLink).toBeVisible({ timeout: 15_000 });
  await settingsLink.click();
  await expect(page).toHaveURL(/#\/settings$/);
}

async function openNetworkPage(page: Page) {
  await page.goto("/");
  const networkLink = page.getByRole("link", { name: /Network/i });
  await expect(networkLink).toBeVisible({ timeout: 15_000 });
  await networkLink.click();
  await expect(page).toHaveURL(/#\/network$/);
}

async function expectVisibleWithSingleReload(page: Page, locator: Locator) {
  try {
    await expect(locator).toBeVisible({ timeout: 15_000 });
  } catch (error) {
    await page.reload();
    await expect(locator).toBeVisible({ timeout: 15_000 });
  }
}

test("axeos pool settings save through the WebUI", async ({ page }) => {
  test.skip(SOURCE_NAME !== "bitaxe", "Bitaxe-only AxeOS pool form contract");

  await openPoolSettings(page);

  const fallbackUser = page.locator("#fallbackStratumUser");
  await expectVisibleWithSingleReload(page, fallbackUser);

  const originalUser = await fallbackUser.inputValue();
  const temporaryUser = `${originalUser}.webui-check`;

  try {
    await fallbackUser.fill(temporaryUser);
    await saveSystemForm(page);
    await expect(page.getByText(/Saved pool settings/i)).toBeVisible();
    await waitForSystemFields({ fallbackStratumUser: temporaryUser });

    await page.reload();
    await expect(page.locator("#fallbackStratumUser")).toHaveValue(temporaryUser);
  } finally {
    await patchSystem({ fallbackStratumUser: originalUser });
    await waitForSystemFields({ fallbackStratumUser: originalUser });
  }
});

test("axeos network settings save through the WebUI", async ({ page }) => {
  test.skip(SOURCE_NAME !== "bitaxe", "Bitaxe-only AxeOS network form contract");

  await openNetworkPage(page);

  const hostnameField = page.locator("#hostname");
  await expectVisibleWithSingleReload(page, hostnameField);

  const originalInfo = await getSystemInfo();
  const originalHostname = originalInfo.hostname;
  const updatedHostname = temporaryHostname(originalHostname);

  try {
    await hostnameField.fill(updatedHostname);
    await saveSystemForm(page);
    await expect(page.getByText(/Saved network settings/i)).toBeVisible();
    await waitForSystemFields({ hostname: updatedHostname });

    await page.reload();
    await expect(page.locator("#hostname")).toHaveValue(updatedHostname);
  } finally {
    await patchSystem({ hostname: originalHostname });
    await waitForSystemFields({ hostname: originalHostname });
  }
});

test("axeos tuning settings save through the WebUI", async ({ page }) => {
  test.skip(SOURCE_NAME !== "bitaxe", "Bitaxe-only AxeOS tuning form contract");

  const originalInfo = await getSystemInfo();
  const asicSettings = await getAsicSettings();
  const targetFrequency = nextDistinctOption(asicSettings.frequencyOptions, originalInfo.frequency);

  await patchSystem({ overclockEnabled: 1 });
  await waitForSystemFields({ overclockEnabled: 1 });

  await openSettingsPage(page);

  const frequencyField = page.locator("#frequency");
  const coreVoltageField = page.locator("#coreVoltage");
  await expectVisibleWithSingleReload(page, frequencyField);
  await expectVisibleWithSingleReload(page, coreVoltageField);

  try {
    await frequencyField.fill(String(targetFrequency));
    await saveSystemForm(page);

    await waitForSystemFields({
      frequency: targetFrequency,
      overclockEnabled: 1,
    });

    await page.reload();
    await expect(page.locator("#frequency")).toHaveValue(String(targetFrequency));
  } finally {
    await patchSystem({
      coreVoltage: originalInfo.coreVoltage,
      frequency: originalInfo.frequency,
      overclockEnabled: originalInfo.overclockEnabled,
    });
    await waitForSystemFields({
      coreVoltage: originalInfo.coreVoltage,
      frequency: originalInfo.frequency,
      overclockEnabled: originalInfo.overclockEnabled,
    });
  }
});
