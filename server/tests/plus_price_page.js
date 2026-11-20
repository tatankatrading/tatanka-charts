// The + by the price axis and its price tag in Chrome through Playwright, for test_plus_price.py: the page of a sim
// server (port) with bars, walked with a mouse (1400 x 800 and 390 x 844) and with a finger (390 x 844, a long press
// then a lift, as a phone): on the chart, on the +, past it on the axis, the moment after the pointer leaves, the 3 s
// after a finger lifts, its menu open, near the pane's top and bottom. Logs one JSON line: what each step saw
// ({ skip } when Playwright or its Chrome isn't there). With a shots folder, screenshots of each state go there.
//     node plus_price_page.js <port> [shots folder] [playwright module]
'use strict';
const [port, SHOTS, PW] = process.argv.slice(2);
let chromium;
try { ({ chromium } = require(PW || 'playwright')); } catch (e) { console.log(JSON.stringify({ skip: `no Playwright (${e.message.split('\n')[0]})` })); process.exit(0); }

const BASE = `http://127.0.0.1:${port}/?symbol=ES`;
const out = { errors: [] };

// What the page shows now: the + and its tag (where, what, how it looks), the menu (where, its price), the chart's own
// crosshair label setting, the held crosshair, the axis and pane of the chart.
function state() {
  const b = $('#ax-plus'), t = $('#ax-plus-px'), m = $('#ctx');
  const shown = (e) => !e.hidden && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().width > 0;
  const R = (e) => { const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }; };
  const c = $('#chart').getBoundingClientRect(), pane = chart.paneSize(0), cs = getComputedStyle(t);
  const menu = shown(m) ? R(m) : null, first = menu ? m.querySelector('button')?.textContent || '' : '';
  return {
    plus: shown(b), tag: shown(t), text: t.textContent, price: b.dataset.price ? px(snap(Number(b.dataset.price))) : null,
    pr: R(b), tr: R(t), fits: t.scrollWidth <= t.clientWidth + 0.5,
    menu, menuPrice: (first.match(/^Add alert at (.+)$/) || [])[1] || null,
    label: chart.options().crosshair.horzLine.labelVisible, held: !!S.plusXhair, hover: S.hoverPrice, linked: !!S.xhairLinked, plusMenu: !!ctxBeside,
    priceRow: b.dataset.price ? candles.priceToCoordinate(Number(b.dataset.price)) + c.top : null,   // where the axis has its price
    look: { color: cs.color, bg: cs.backgroundColor, size: cs.fontSize, family: cs.fontFamily,
      want: { bg: chart.options().crosshair.horzLine.labelBackgroundColor, size: `${chart.options().layout.fontSize}px`, family: chart.options().layout.fontFamily } },
    chart: { l: c.left, t: c.top, r: c.right, b: c.bottom }, axis: { l: c.left + pane.width, r: c.left + pane.width + chart.priceScale('right').width() },
    pane: { t: c.top, b: c.top + pane.height }, vw: innerWidth, vh: innerHeight,
  };
}

(async () => {
  let browser;
  try { browser = await chromium.launch(); } catch (e) { console.log(JSON.stringify({ skip: `no Chrome for Playwright (${e.message.split('\n')[0]})` })); return; }
  // the rows (CSS px, in the page) the crosshair's horizontal line is on in a box, the line drawn magenta for the look;
  // frame: a layout's chart (off: where its frame is in the page)
  async function lineRows(page, box, frame = page, off = { x: 0, y: 0 }) {
    const was = await frame.evaluate(() => {
      const h = chart.options().crosshair.horzLine, was = { color: h.color, style: h.style, width: h.width };   // (the options' own object)
      chart.applyOptions({ crosshair: { horzLine: { color: '#ff00ff', style: 0, width: 1 } } });
      return was;
    });
    await frame.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    const clip = { x: box.l + off.x, y: box.t + off.y, width: Math.max(1, box.r - box.l), height: Math.max(1, box.b - box.t) };
    const png = (await page.screenshot({ clip })).toString('base64');
    await frame.evaluate((h) => chart.applyOptions({ crosshair: { horzLine: h } }), was);
    return page.evaluate(async ([b64, y0, w]) => {
      const img = new Image();
      img.src = `data:image/png;base64,${b64}`;
      await img.decode();
      const c = document.createElement('canvas');
      c.width = img.width; c.height = img.height;
      const g = c.getContext('2d');
      g.drawImage(img, 0, 0);
      // magenta: red and blue well over green (also under a level's see-through band)
      const px = g.getImageData(0, 0, c.width, c.height).data, k = img.width / w, rows = new Set();
      for (let y = 0; y < c.height; y++) {
        let n = 0;
        for (let x = 0; x < c.width; x++) { const i = (y * c.width + x) * 4; if (px[i] - px[i + 1] > 80 && px[i + 2] - px[i + 1] > 80) n++; }
        if (n > c.width * 0.3) rows.add(Math.round((y0 + (y + 0.5) / k) * 2) / 2);
      }
      return [...rows];
    }, [png, box.t, clip.width]);
  }
  // the held line's rows in a band around the tag (q: a state), looked at again a moment later while it is not on the
  // tag's row (a tick redrawing the chart just as it is looked at)
  async function heldRows(page, q, box, frame, off) {
    let rows = [];
    for (let i = 0; i < 4; i++) {
      rows = await lineRows(page, box, frame, off);
      if (rows.some((r) => Math.abs(r - (q.tr.t + q.tr.b) / 2) <= 1.5)) break;
      await page.waitForTimeout(100);
    }
    return rows;
  }
  async function device({ width, height, touch = false }) {
    const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 2, serviceWorkers: 'block', isMobile: touch, hasTouch: touch });
    const page = await ctx.newPage();
    page.on('pageerror', (e) => out.errors.push(e.message));
    let navAt = 0;
    page.on('framenavigated', (f) => { if (f === page.mainFrame()) navAt = Date.now(); });
    await page.goto(BASE);
    do {   // a first visit may load the page again (its workspace)
      await page.waitForFunction(() => typeof S !== 'undefined' && S.bars?.length > 50 && chart.paneSize(0).height > 100, null, { timeout: 30000 });
      await page.waitForTimeout(250);
    } while (Date.now() - navAt < 1500);
    await page.waitForTimeout(1500);
    // what each frame shows, while recording: the tag up, the chart's own label on, the tag's text
    await page.evaluate(() => {
      window.__frames = null;
      const f = () => { if (window.__frames) window.__frames.push([!$('#ax-plus-px').hidden, chart.options().crosshair.horzLine.labelVisible, $('#ax-plus-px').textContent]); requestAnimationFrame(f); };
      requestAnimationFrame(f);
    });
    const d = { ctx, page, wait: (ms) => page.waitForTimeout(ms), st: () => page.evaluate(state), at: null };
    for (const k of ['move', 'click']) {   // where the mouse is, for the screenshots' pointer
      const f = page.mouse[k].bind(page.mouse);
      page.mouse[k] = (x, y, opt) => { d.at = [x, y]; return f(x, y, opt); };
    }
    d.record = () => page.evaluate(() => { window.__frames = []; });
    d.frames = () => page.evaluate(() => { const f = window.__frames; window.__frames = null; return f; });
    // the chart's own crosshair label in the price axis at the tag's row (our tag hidden for the look): its color's
    // pixels there; with control, the label turned on for a moment first (the check sees it when it is there)
    d.labelPixels = async (control = false, row = null) => {   // row: the label's middle row to look at (else the tag's)
      const s = await d.st(), t = row == null ? s.tr.t : row - s.tr.h / 2;
      const clip = { x: s.axis.l, y: Math.max(s.pane.t, t - 6), width: s.axis.r - s.axis.l, height: Math.min(s.tr.h + 12, s.pane.b - Math.max(s.pane.t, t - 6)) };
      const bg = await page.evaluate((on) => {
        $('#ax-plus-px').style.visibility = 'hidden';
        if (on) chart.applyOptions({ crosshair: { horzLine: { labelVisible: true } } });
        return getComputedStyle($('#ax-plus-px')).backgroundColor;
      }, control);
      await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
      const png = (await page.screenshot({ clip })).toString('base64');
      await page.evaluate((on) => {
        $('#ax-plus-px').style.visibility = '';
        if (on) chart.applyOptions({ crosshair: { horzLine: { labelVisible: false } } });
      }, control);
      return page.evaluate(async ([b64, color]) => {
        const img = new Image();
        img.src = `data:image/png;base64,${b64}`;
        await img.decode();
        const c = document.createElement('canvas');
        c.width = img.width; c.height = img.height;
        const g = c.getContext('2d');
        g.drawImage(img, 0, 0);
        const px = g.getImageData(0, 0, c.width, c.height).data, [r, gg, b] = color.match(/\d+/g).map(Number);
        let n = 0;
        for (let i = 0; i < px.length; i += 4) if (Math.abs(px[i] - r) <= 3 && Math.abs(px[i + 1] - gg) <= 3 && Math.abs(px[i + 2] - b) <= 3) n++;
        return n;
      }, [png, bg]);
    };
    d.lineRows = (box, frame, off) => lineRows(page, box, frame, off);
    d.heldRows = (q, box, frame, off) => heldRows(page, q, box, frame, off);
    d.shot = async (name, s, menu = false) => {   // the + and the axis around it (with the menu: it too), and the mouse pointer
      if (!SHOTS) return;
      const at = touch ? null : d.at, boxes = [s.pr, s.tr, ...(menu && s.menu ? [s.menu] : []), ...(at ? [{ l: at[0], t: at[1], r: at[0] + 14, b: at[1] + 20 }] : [])];
      let x0 = Math.min(...boxes.map((r) => r.l)) - 12, x1 = Math.max(s.chart.r, ...boxes.map((r) => r.r)) + 2;
      let y0 = Math.min(...boxes.map((r) => r.t)) - 40, y1 = Math.max(...boxes.map((r) => r.b)) + 40;
      if (!menu) { x0 = Math.min(x0, x1 - Math.min(380, s.vw)); }
      x0 = Math.max(0, x0); y0 = Math.max(0, y0); x1 = Math.min(s.vw, x1); y1 = Math.min(s.vh, y1);
      if (at) {   // drawn over the page for the picture only: it takes no pointer events
        await page.evaluate(([x, y]) => {
          const c = document.createElement('div');
          c.id = 'shot-pointer';
          c.style.cssText = `position:fixed;left:${x}px;top:${y}px;z-index:99999;pointer-events:none;width:14px;height:20px`;
          c.innerHTML = '<svg viewBox="0 0 14 20" width="14" height="20"><path d="M1 1v15l4-4 3 7 2.5-1-3-6.5H13z" fill="#fff" stroke="#000" stroke-width="1.2" stroke-linejoin="round"/></svg>';
          document.body.append(c);
        }, at);
      }
      await page.screenshot({ path: `${SHOTS}/${name}.png`, clip: { x: x0, y: y0, width: x1 - x0, height: y1 - y0 } });
      if (at) await page.evaluate(() => document.getElementById('shot-pointer')?.remove());
    };
    return d;
  }
  const mid = (r) => [(r.l + r.r) / 2, (r.t + r.b) / 2];

  // ---- a mouse: chart -> + -> the axis past it -> back -> its menu, then the ways it goes ----------------------------
  async function mouse(width, height, key) {
    const d = await device({ width, height }), p = d.page, o = {};
    let s = await d.st();
    const row = s.pane.t + (s.pane.b - s.pane.t) * 0.45, x = s.chart.l + (s.axis.l - s.chart.l) * 0.5;
    await p.mouse.move(x, row);
    await d.wait(250);
    o.onChart = await d.st();
    o.onChart.pixels = await d.labelPixels();
    o.onChart.control = await d.labelPixels(true);
    await d.shot(`${key}_1_on_chart`, o.onChart);
    await d.record();
    await p.mouse.move(...mid(o.onChart.pr), { steps: 12 });
    await d.wait(350);
    o.onPlus = await d.st();
    await d.shot(`${key}_2_on_plus`, o.onPlus);
    const tag = o.onPlus.tr;   // past the + onto the axis: on its tag
    await p.mouse.move(Math.min(tag.r - 4, tag.l + 16), (tag.t + tag.b) / 2, { steps: 4 });
    await d.wait(150);
    o.onAxis = await d.st();
    await d.wait(500);   // longer than the moment the + stays after the pointer leaves it
    o.onAxisLater = await d.st();
    await d.shot(`${key}_3_on_axis_past_plus`, o.onAxisLater);
    await p.mouse.move(...mid(o.onAxisLater.pr), { steps: 4 });
    await d.wait(300);
    o.backOnPlus = await d.st();
    await p.mouse.click(...mid(o.backOnPlus.pr));
    await d.wait(300);
    o.menu = await d.st();
    // slowly onto the menu along the +'s row, a pixel or two a move: across the plot's few pixels left of the +
    const [gx, gy] = mid(o.menu.pr);
    await p.mouse.move(o.menu.menu.r - 30, gy, { steps: Math.ceil((gx - (o.menu.menu.r - 30)) / 1.5) });
    await p.mouse.move(o.menu.menu.l + 30, o.menu.menu.t + 14, { steps: 8 });   // onto the menu
    await d.wait(600);
    o.onMenu = await d.st();
    await d.shot(`${key}_4_menu_open`, o.onMenu, true);
    o.frames = await d.frames();
    o.onAxisLater.pixels = await d.labelPixels();
    o.onMenu.pixels = await d.labelPixels();
    const band = (q, r = q.pr.l) => ({ l: q.chart.l + 2, r: r - 2, t: q.tr.t - 3, b: q.tr.b + 3 });   // the +'s row, left of it
    // the held line still on the +'s row, after the pointer crossed the plot left of the + on its way to the menu
    o.onMenu.line = await d.heldRows(o.onMenu, band(o.onMenu, o.onMenu.menu.l));
    // the price scale moves with the menu open (a new high, the axis wheeled): the + and its tag go to the row of the
    // price the menu says, and the held line with them
    await p.evaluate(() => { const v = PS().getVisibleRange(), k = (v.to - v.from) * 0.06; PS().setAutoScale(false); PS().setVisibleRange({ from: v.from + k, to: v.to + k }); });
    await d.wait(250);
    o.menuRescaled = await d.st();
    o.menuRescaled.line = await d.heldRows(o.menuRescaled, band(o.menuRescaled, o.menuRescaled.menu.l));
    await p.evaluate(() => setAuto(true));
    await d.wait(250);
    await p.mouse.move(o.onMenu.menu.l + 30, o.onMenu.chart.t - 30, { steps: 6 });   // off the chart, the menu still open
    await d.wait(600);
    o.menuStays = await d.st();
    await p.keyboard.press('Escape');
    await d.wait(100);
    o.closing = await d.st();
    await d.wait(700);
    o.closed = await d.st();
    // past the + on the axis, then off its row along the axis: the + goes a moment later
    await p.mouse.move(x, row, { steps: 6 });
    await d.wait(200);
    s = await d.st();
    await p.mouse.move(...mid(s.pr), { steps: 8 });
    await d.wait(200);
    await p.mouse.move(s.tr.l + 16, (s.tr.t + s.tr.b) / 2, { steps: 3 });
    await d.wait(200);
    await p.mouse.move(s.tr.l + 16, (s.tr.t + s.tr.b) / 2 + 80, { steps: 4 });
    await d.wait(150);
    o.axisOffRow = await d.st();
    await d.wait(600);
    o.axisOffRowLater = await d.st();
    // the moment after the pointer leaves the chart (up over the header)
    await p.mouse.move(x, row, { steps: 6 });
    await d.wait(200);
    o.again = await d.st();
    await d.record();
    await p.mouse.move(x, o.again.chart.t - 30, { steps: 2 });
    await d.wait(150);
    o.leaving = await d.st();
    await d.shot(`${key}_5_after_leaving_the_chart`, o.leaving);
    await d.wait(700);
    o.left = await d.st();
    o.leaveFrames = await d.frames();
    // the right-click menu: where it was clicked, as before
    await p.mouse.click(x, row, { button: 'right' });
    await d.wait(250);
    o.right = { at: [x, row], ...(await d.st()) };
    await p.keyboard.press('Escape');
    await d.wait(600);
    // the scale moves under the + with the pointer on it: the + stays under the pointer and takes the price its row has
    // now, and the held line stays on it: the range moved, a new high on the last bar (autoscale), the wheel on its tag
    await p.mouse.move(x, row, { steps: 4 });
    await d.wait(200);
    s = await d.st();
    const onP = mid(s.pr);
    await p.mouse.move(...onP, { steps: 6 });
    await d.wait(300);
    o.rescalePre = await d.st();
    await p.evaluate(() => { const v = PS().getVisibleRange(), k = (v.to - v.from) * 0.06; PS().setAutoScale(false); PS().setVisibleRange({ from: v.from + k, to: v.to + k }); });
    await d.wait(250);
    o.rescaled = { ...(await d.st()), at: onP };
    o.rescaled.line = await d.heldRows(o.rescaled, band(o.rescaled));
    await p.evaluate(() => setAuto(true));
    await d.wait(250);
    await p.evaluate(() => { const b = S.bars[S.bars.length - 1], r = PS().getVisibleRange(); candles.update({ time: timeOf(b), open: b.o, high: r.to + (r.to - r.from) * 0.3, low: b.l, close: b.c }); });
    await d.wait(200);
    o.newHigh = { ...(await d.st()), at: onP };
    o.newHigh.line = await d.heldRows(o.newHigh, band(o.newHigh));
    await p.evaluate(() => { const b = S.bars[S.bars.length - 1]; candles.update({ time: timeOf(b), open: b.o, high: b.h, low: b.l, close: b.c }); });
    await d.wait(250);
    s = await d.st();
    const onTag = [s.tr.l + 16, (s.tr.t + s.tr.b) / 2];
    await p.mouse.move(...onTag, { steps: 3 });
    await d.wait(200);
    o.wheelPre = await d.st();
    await p.mouse.wheel(0, 120);
    await d.wait(300);
    o.wheeled = { ...(await d.st()), at: onTag };
    o.wheeled.line = await d.heldRows(o.wheeled, band(o.wheeled));
    await p.evaluate(() => setAuto(true));
    await p.mouse.move(x, row, { steps: 4 });
    await d.wait(600);
    // a watchlist button's menu replaces the + menu: when it closes, the + goes as after its own menu
    const wl = await p.evaluate(() => { const e = $('#wl-name'), r = e.getBoundingClientRect(); return r.width > 0 && r.top >= 0 && r.bottom <= innerHeight ? [(r.left + r.right) / 2, (r.top + r.bottom) / 2] : null; });
    if (wl) {
      s = await d.st();
      await p.mouse.move(...mid(s.pr), { steps: 6 });
      await d.wait(300);
      await p.mouse.click(...mid(s.pr));
      await d.wait(300);
      await p.mouse.move(mid(s.pr)[0], 3, { steps: 6 });
      await p.mouse.move(...wl, { steps: 6 });
      await p.mouse.click(...wl);
      await d.wait(300);
      o.wlMenu = await d.st();
      await p.keyboard.press('Escape');
      await d.wait(1000);
      o.wlClosed = await d.st();
      await p.mouse.move(x, row, { steps: 6 });
      await d.wait(300);
    }
    // magnet mode: the chart's line goes to the open, high, low or close (of the bar under the pointer) nearest it; its
    // own label shows there, unless that is on the tag's row (then the tag is the one price on that row)
    await p.evaluate(() => { S.cs.crosshair = 'magnet'; csChanged('crosshair'); PS().setAutoScale(false); });   // the scale still while looked at
    s = await d.st();
    const plot = { l: s.chart.l + 2, r: s.axis.l - 30, t: s.pane.t + 2, b: s.pane.b - 2 };
    let far = null;
    for (const f of [0.12, 0.88, 0.3, 0.7]) {
      await p.mouse.move(x, s.pane.t + (s.pane.b - s.pane.t) * f, { steps: 4 });
      await d.wait(250);
      const q = await d.st(), rows = await d.lineRows(plot), mine = await p.evaluate(() => plusLineY(plusY, chart.options()) + $('#chart').getBoundingClientRect().top);
      const tagMid = (q.tr.t + q.tr.b) / 2, dist = rows.length ? Math.min(...rows.map((r) => Math.abs(r - tagMid))) : 0;
      if (!far || dist > far.dist) far = { ...q, rows, mine, dist, y: s.pane.t + (s.pane.b - s.pane.t) * f };
    }
    o.magnetFar = far;
    if (!far.rows.length) throw new Error(`magnet: no line found ${JSON.stringify(far)}`);
    await p.mouse.move(x, far.y, { steps: 3 });
    await d.wait(250);
    far.labelAtLine = await d.labelPixels(false, far.rows[0]);
    far.labelAtTag = await d.labelPixels();
    o.magnetFar = far;
    await d.shot(`${key}_8_magnet`, { ...far, tr: { ...far.tr, t: Math.min(far.tr.t, far.rows[0] - 12), b: Math.max(far.tr.b, far.rows[0] + 12) } });
    await p.mouse.move(x, far.rows[0], { steps: 6 });   // onto the line's row: the same bar, so the line stays there
    await d.wait(250);
    o.magnetOn = await d.st();
    o.magnetOn.rows = await d.lineRows(plot);
    o.magnetOn.labelAtTag = await d.labelPixels();
    await p.evaluate(() => { S.cs.crosshair = 'normal'; csChanged('crosshair'); setAuto(true); });
    await p.mouse.move(x, row, { steps: 3 });
    await d.wait(250);
    // the crosshair label's colors changed (a light one): the tag takes them, with dark text
    await p.evaluate(() => chart.applyOptions({ crosshair: { horzLine: { labelBackgroundColor: '#e1ecf2' } } }));
    await p.mouse.move(x, row + 20, { steps: 3 });
    await d.wait(250);
    o.light = await d.st();
    await d.shot(`${key}_7_light_label`, o.light);
    await p.evaluate(() => chart.applyOptions({ crosshair: { horzLine: { labelBackgroundColor: '#131722' } } }));
    // a price wider than the axis (the text the + shows longer than the library's own): the tag stays whole inside the
    // chart, over the plot's edge, and the + moves left of it
    o.wide = await p.evaluate(`plusAt(${row - o.light.chart.t}, 1234567.25); (${state})()`);   // read at once: a tick moves it back
    await p.mouse.move(x, row, { steps: 3 });
    await d.wait(250);
    // near the pane's top and bottom: the tag inside the pane and the axis, the + beside it; its menu beside them
    for (const [k, dy] of [['top', 2], ['bottom', -2]]) {
      s = await d.st();
      const y = dy > 0 ? s.pane.t + dy : s.pane.b + dy;
      await p.mouse.move(x, y, { steps: 6 });
      await d.wait(250);
      const at = await d.st();
      await p.mouse.move(...mid(at.pr), { steps: 8 });
      await d.wait(300);
      await p.mouse.click(...mid(at.pr));
      await d.wait(300);
      o[k] = { y, at, menu: await d.st() };
      await d.shot(`${key}_6_menu_near_the_${k}`, o[k].menu, true);
      await p.keyboard.press('Escape');
      await p.mouse.move(x, row, { steps: 4 });
      await d.wait(600);
    }
    // a pane below (RSI): no + there, and at once (its crosshair's own label is the chart's); back up, the + again
    await p.evaluate(() => indAdd('rsi'));
    await d.wait(800);
    s = await d.st();
    const low = await p.evaluate(() => { const r = chart.panes()[1].getHTMLElement().getBoundingClientRect(); return { t: r.top, b: r.bottom }; });
    const up = s.pane.t + (s.pane.b - s.pane.t) * 0.5;
    await p.mouse.move(x, up, { steps: 4 });
    await d.wait(250);
    o.abovePane = await d.st();
    await p.mouse.move(x, (low.t + low.b) / 2, { steps: 6 });
    await d.wait(60);
    o.lowerPane = await d.st();
    await p.mouse.move(x, up, { steps: 6 });
    await d.wait(250);
    o.backUp = await d.st();
    await d.ctx.close();
    return o;
  }

  // ---- a finger: long-press the chart, lift, the 3 s, tap the +, its menu ------------------------------------------
  async function finger(width, height, key) {
    const d = await device({ width, height, touch: true }), p = d.page, o = {};
    const cdp = await d.ctx.newCDPSession(p);
    const touch = (type, x, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] });
    async function press(x, y) { await touch('touchStart', x, y); await d.wait(900); const s = await d.st(); await touch('touchEnd'); return s; }
    let s = await d.st();
    const x = s.chart.l + (s.axis.l - s.chart.l) * 0.45;
    for (const [k, frac] of [['mid', 0.45], ['top', 0], ['bottom', 1]]) {
      s = await d.st();
      const y = frac === 0 ? s.pane.t + 3 : frac === 1 ? s.pane.b - 3 : s.pane.t + (s.pane.b - s.pane.t) * frac;
      const r = { y };
      r.pressed = await press(x, y);
      await d.wait(200);
      r.lifted = await d.st();
      if (k === 'mid') await d.shot(`${key}_touch_1_lifted`, r.lifted);
      await d.wait(1000);
      r.lifted1s = await d.st();
      await p.touchscreen.tap(...mid(r.lifted1s.pr));
      await d.wait(400);
      r.menu = await d.st();
      if (k === 'mid') {
        await d.shot(`${key}_touch_2_menu_open`, r.menu, true);
        await d.wait(3200);   // past the 3 s with its menu open
        r.menuLater = await d.st();
      }
      await p.touchscreen.tap(s.chart.l + 30, s.pane.t + 40);   // a tap away from the menu closes it
      await d.wait(600);
      r.closed = await d.st();
      await d.wait(3000);
      r.gone = await d.st();
      o[k] = r;
    }
    await d.ctx.close();
    return o;
  }

  // ---- a layout of 6 charts (3 x 2 at 1280 x 720): its cells are narrow and short ---------------------------------------
  async function cells(width, height, key) {
    const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 2, serviceWorkers: 'block' });
    const page = await ctx.newPage(), o = {};
    page.on('pageerror', (e) => out.errors.push(e.message));
    await page.goto(BASE);
    await page.waitForFunction(() => typeof S !== 'undefined' && S.bars?.length > 50, null, { timeout: 30000 });
    await page.waitForTimeout(1500);
    await page.evaluate(() => localStorage.setItem('layout', '6'));
    await page.reload();
    let frames = [];
    for (let i = 0; i < 60 && frames.length < 6; i++) { await page.waitForTimeout(500); frames = page.frames().filter((f) => /[?&]cell=\d/.test(f.url())); }
    for (const f of frames) await f.waitForFunction(() => typeof S !== 'undefined' && S.bars?.length > 50 && chart.paneSize(0).height > 50, null, { timeout: 30000 });
    await page.waitForTimeout(1500);
    const at = [];
    for (const f of frames) { const b = await (await f.frameElement()).boundingBox(); at.push({ f, x: b.x, y: b.y }); }
    at.sort((a, b) => a.y - b.y || a.x - b.x);
    const d = { page, wait: (ms) => page.waitForTimeout(ms) };
    d.heldRows = (q, box, frame, off) => heldRows(page, q, box, frame, off);
    const st = (c) => c.f.evaluate(state), go = (c, px, py, steps = 6) => page.mouse.move(c.x + px, c.y + py, { steps });
    const [c0, c1, c2] = at;
    let s = await st(c1);
    const x = s.chart.l + (s.axis.l - s.chart.l) * 0.5;
    // its + menu, in the middle of the pane and near its top and bottom: beside the + and its tag, inside the cell (it
    // scrolls where it is taller than the room), over neither
    for (const [k, y] of [['mid', s.pane.t + (s.pane.b - s.pane.t) * 0.5], ['bottom', s.pane.b - 3], ['top', s.pane.t + 3]]) {
      await go(c1, x, y);
      await d.wait(250);
      const q = await st(c1);
      await go(c1, ...mid(q.pr), 8);
      await d.wait(300);
      await page.mouse.click(c1.x + mid(q.pr)[0], c1.y + mid(q.pr)[1]);
      await d.wait(300);
      o[k] = await st(c1);
      o[k].scroll = await c1.f.evaluate(() => { const m = $('#ctx'); return [m.scrollHeight, m.clientHeight]; });
      if (SHOTS && k === 'bottom') await page.screenshot({ path: `${SHOTS}/${key}_menu_near_the_bottom.png`, clip: { x: c1.x, y: c1.y, width: o[k].vw, height: o[k].vh } });
      await page.keyboard.press('Escape');
      await go(c1, x, s.pane.t + (s.pane.b - s.pane.t) * 0.5, 4);
      await d.wait(600);
    }
    // the pointer from one chart into the next in two moves: the first chart's last word (its crosshair gone) can come
    // after the pointer got to the second; the second keeps its + and price
    await go(c0, x, s.pane.t + (s.pane.b - s.pane.t) * 0.4, 4);
    await d.wait(300);
    await page.mouse.move(c1.x + x, c1.y + s.pane.t + (s.pane.b - s.pane.t) * 0.4, { steps: 2 });
    await d.wait(400);
    o.across = await st(c1);
    // the + menu open in one chart and the pointer on another: the first keeps its + menu, its + and price and the
    // crosshair held at them, not the other's linked one
    await go(c1, x, s.pane.t + (s.pane.b - s.pane.t) * 0.5);
    await d.wait(250);
    s = await st(c1);
    await go(c1, ...mid(s.pr), 8);
    await d.wait(300);
    await page.mouse.click(c1.x + mid(s.pr)[0], c1.y + mid(s.pr)[1]);
    await d.wait(300);
    await page.mouse.move(c2.x + x, c2.y + s.pane.t + (s.pane.b - s.pane.t) * 0.3, { steps: 10 });
    await d.wait(400);
    o.otherChart = await st(c1);
    o.otherChart.line = await d.heldRows(o.otherChart, { l: o.otherChart.chart.l + 2, r: o.otherChart.menu.l - 2, t: o.otherChart.tr.t - 3, b: o.otherChart.tr.b + 3 }, c1.f, c1);
    o.otherChart.other = await st(c2);
    await page.keyboard.press('Escape');
    await ctx.close();
    return o;
  }

  try {
    out.desktop = await mouse(1400, 800, 'desktop');
    out.phoneMouse = await mouse(390, 844, 'phone');
    out.phoneTouch = await finger(390, 844, 'phone');
    out.cells = await cells(1280, 720, 'cell');
  } catch (e) {
    out.error = String(e.stack || e);
  }
  await browser.close();
  console.log(JSON.stringify(out));
})();
