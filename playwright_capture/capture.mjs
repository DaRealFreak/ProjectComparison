// Capture helper: writes HTML and screenshots for one URL, plus interaction states.
// Prints one JSON result object on stdout. Never print cookies or credentials.
import { parseArgs } from 'node:util';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from 'playwright';

const DEFAULT_TIMEOUT_MS = 5000;
// The Selenium path takes its full-page shot at this viewport, too.
const FULLSCREEN_VIEWPORT = { width: 1920, height: 1080 };

function parseSize(size) {
  const [width, height] = size.split('x').map(Number);
  return { width, height };
}

function describeError(error) {
  // Playwright errors are multi-line; the first line is enough for a report.
  return String(error && error.message ? error.message : error).split('\n')[0];
}

function viewportFor(sizeName) {
  return sizeName === 'fullscreen' ? FULLSCREEN_VIEWPORT : parseSize(sizeName);
}

async function loadPage(context, request, sizeName) {
  const page = await context.newPage();
  await page.setViewportSize(viewportFor(sizeName));
  const response = await page.goto(request.url, { waitUntil: 'load', timeout: 60000 });
  await page.waitForLoadState('networkidle');
  return { page, response };
}

async function runStep(page, step, timeoutMs) {
  const target = () => page.locator(step.selector).nth(step.index);
  switch (step.action) {
    case 'click':
      await target().click({ timeout: timeoutMs });
      break;
    case 'hover':
      await target().hover({ timeout: timeoutMs });
      break;
    case 'focus':
      await target().focus({ timeout: timeoutMs });
      break;
    case 'press':
      await page.keyboard.press(step.key);
      break;
    case 'waitFor':
      await target().waitFor({ state: 'visible', timeout: timeoutMs });
      break;
    case 'wait':
      await page.waitForTimeout(step.ms);
      break;
    default:
      throw new Error(`unknown action: ${step.action}`);
  }
}

function widthsForState(state, requestSizes) {
  // An empty list means "no restriction", the same as null.
  const wanted = state.sizes && state.sizes.length > 0 ? state.sizes : requestSizes;
  return wanted.filter((size) => size !== 'fullscreen');
}

async function captureState(context, request, state, sizeName, timeoutMs) {
  const entry = { id: state.id, size: sizeName, outcome: 'failed', reason: null, files: [] };
  let page = null;
  try {
    ({ page } = await loadPage(context, request, sizeName));
    if (state.when && (await page.locator(state.when).count()) === 0) {
      entry.outcome = 'absent';
      return entry;
    }
    for (const [position, step] of state.steps.entries()) {
      try {
        await runStep(page, step, timeoutMs);
      } catch (error) {
        entry.reason = `step ${position + 1}: ${describeError(error)}`;
        return entry;
      }
    }
    const prefix = `state-${state.id}_${sizeName}`;
    const wanted = [`${prefix}_html_${request.label}.html`];
    writeFileSync(join(request.outDir, wanted[0]), await page.content());
    if (!request.omitScreenshots) {
      wanted.push(`${prefix}_${request.label}.png`);
      await page.screenshot({ path: join(request.outDir, wanted[1]) });
    }
    for (const name of wanted) {
      if (!existsSync(join(request.outDir, name))) {
        entry.reason = `missing file ${name}`;
        return entry;
      }
      entry.files.push(name);
    }
    entry.outcome = 'captured';
    return entry;
  } catch (error) {
    entry.reason = describeError(error);
    return entry;
  } finally {
    if (page) {
      await page.close();
    }
  }
}

// Each size gets its own page load: scripts that read the width at load time and lazy
// images must see that viewport, whatever size came before it.
async function captureBase(context, request, result) {
  const sizes = request.sizes || [];
  // The HTML and the status come from the first width.
  const htmlSize = sizes.find((size) => size !== 'fullscreen') || 'fullscreen';
  const htmlName = `html_${request.label}.html`;
  const shotSizes = request.omitScreenshots ? [] : sizes;
  const loads = shotSizes.includes(htmlSize) ? shotSizes : [htmlSize, ...shotSizes];
  for (const size of loads) {
    const { page, response } = await loadPage(context, request, size);
    try {
      if (size === htmlSize && !result.files.includes(htmlName)) {
        result.status = response ? response.status() : null;
        writeFileSync(join(request.outDir, htmlName), await page.content());
        result.files.push(htmlName);
      }
      if (shotSizes.includes(size)) {
        const name = `${size}_${request.label}.png`;
        await page.screenshot({ path: join(request.outDir, name), fullPage: size === 'fullscreen' });
        result.files.push(name);
      }
    } finally {
      await page.close();
    }
  }
}

async function main() {
  const { values } = parseArgs({ options: { request: { type: 'string' } } });
  if (!values.request) {
    throw new Error('missing --request <file.json>');
  }
  const request = JSON.parse(readFileSync(values.request, 'utf8'));
  const timeoutMs = request.timeoutMs || DEFAULT_TIMEOUT_MS;
  const requestSizes = request.sizes || [];
  const result = { ok: false, status: null, error: null, files: [], states: [] };

  const browser = await chromium.launch();
  try {
    const contextOptions = { ignoreHTTPSErrors: true };
    if (request.httpCredentials) {
      contextOptions.httpCredentials = request.httpCredentials;
    }
    if (request.userAgent) {
      contextOptions.userAgent = request.userAgent;
    }
    const context = await browser.newContext(contextOptions);
    if (request.cookies && request.cookies.length > 0) {
      await context.addCookies(request.cookies);
    }

    try {
      await captureBase(context, request, result);
      result.ok = true;
    } catch (error) {
      result.error = describeError(error);
    }

    for (const state of request.states || []) {
      for (const sizeName of widthsForState(state, requestSizes)) {
        if (!result.ok) {
          // Keep one entry per state and width even when the base load failed.
          result.states.push({
            id: state.id,
            size: sizeName,
            outcome: 'failed',
            reason: 'page did not load',
            files: [],
          });
          continue;
        }
        result.states.push(await captureState(context, request, state, sizeName, timeoutMs));
      }
    }
  } finally {
    await browser.close();
  }

  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exit(result.ok ? 0 : 1);
}

main().catch((error) => {
  process.stdout.write(
    `${JSON.stringify({ ok: false, status: null, error: describeError(error), files: [], states: [] })}\n`,
  );
  process.exit(1);
});
