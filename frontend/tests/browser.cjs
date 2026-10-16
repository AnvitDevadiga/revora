// Requires Playwright on NODE_PATH and its ffmpeg recorder installed.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');

(async () => {
  const output = path.resolve(__dirname, '../../docs/artifacts');
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1360, height: 1000 }, recordVideo: { dir: output, size: { width: 1360, height: 1000 } } });
  const page = await context.newPage();
  const crashes = [];
  page.on('pageerror', error => crashes.push(error.message));
  try {
    await page.goto('http://127.0.0.1:3000');
    await page.getByLabel('Workspace API key (if required)').fill('local-audit-key');
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByRole('region', { name: 'An agent that learns from a real failure.' }).getByLabel('Service', { exact: true }).fill(`browser-${Date.now()}`);
    await page.getByRole('button', { name: 'Run learning agent', exact: true }).click();
    await page.getByText('2 tool attempt(s) · 0 prior observations retrieved').waitFor();
    await page.getByRole('button', { name: 'Inspect observation 1', exact: true }).click();
    await page.getByText('Observation 1: Failure retained', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(output, 'first-run.png'), fullPage: true });
    await page.waitForTimeout(1800);
    await page.getByRole('button', { name: 'Run learning agent', exact: true }).click();
    await page.getByText('1 tool attempt(s) · 2 prior observations retrieved').waitFor();
    await page.screenshot({ path: path.join(output, 'learned-run.png'), fullPage: true });
    await page.waitForTimeout(1800);
    await page.getByRole('button', { name: 'Run held-out evaluation' }).click();
    await page.getByRole('cell', { name: 'duplicates-25', exact: true }).waitFor();
    assert.equal(await page.locator('tbody tr').count(), 12);
    await page.getByRole('cell', { name: 'canary_regression', exact: true }).waitFor();
    await page.getByText('4/4', { exact: true }).waitFor();
    await page.getByRole('heading', { name: 'Measured comparison' }).scrollIntoViewIfNeeded();
    await page.waitForTimeout(1800);
    await page.screenshot({ path: path.join(output, 'evaluation.png'), fullPage: true });
    await page.getByRole('button', { name: 'Replay experiment' }).click();
    await page.getByRole('button', { name: /OBSERVATION 3/ }).click();
    await page.getByText('Decision revised by this evidence').waitFor();
    await page.waitForTimeout(1800);
    await page.getByLabel('Scenario', { exact: true }).selectOption('migration');
    await page.getByRole('button', { name: 'Replay experiment' }).click();
    await page.getByText('The migration that locks the database · retries reopen the same saved experiment').waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Mobile horizontal overflow');
    await page.reload();
    await page.getByLabel('Workspace API key (if required)').fill('local-audit-key');
    await page.getByRole('button', { name: 'Load saved history' }).click();
    await page.getByRole('heading', { name: 'Saved runs' }).waitFor();
    assert.deepEqual(crashes, []);
    await page.getByRole('heading', { name: 'Groq reasoning agent' }).waitFor();
    console.log('PASS: real loop, learned second run, graph inspection, database/deployment evaluations, Groq panel, both replays, mobile width, persisted history, no browser crashes');
  } finally {
    await context.close();
    await page.video().saveAs(path.join(output, 'demo-walkthrough.webm'));
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
