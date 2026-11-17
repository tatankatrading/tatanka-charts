// The update card in Chrome through Playwright, for test_update_popup.py: the page of a sim server (port), its
// WebSocket passed through with the hello's update set here and 'update' broadcasts sent from here, as the server
// sends them. Logs one JSON line: what each step saw ({ skip } when Playwright or its Chrome isn't there).
//     node update_popup_page.js <port> [playwright module]
'use strict';
const [port, PW] = process.argv.slice(2);
let chromium;
try { ({ chromium } = require(PW || 'playwright')); } catch (e) { console.log(JSON.stringify({ skip: `no Playwright (${e.message.split('\n')[0]})` })); process.exit(0); }

const BASE = `http://127.0.0.1:${port}/`;
const XSS = '<img src=x onerror=alert(1)>';
const TITLES = ['Newest change: the update card', 'Second change', 'Third change', 'Fourth change', XSS, 'Sixth change, not shown'];
const update = (behind, latest, changes = TITLES.slice(0, 5)) => ({ behind, latest, changes });
const out = {};

(async () => {
  let browser;
  try { browser = await chromium.launch(); } catch (e) { console.log(JSON.stringify({ skip: `no Chrome for Playwright (${e.message.split('\n')[0]})` })); return; }
  const errors = [];
  // one browser profile (device): its storage kept across page loads; up: the update the hello carries
  async function device({ width = 1280, height = 800, reducedMotion = 'no-preference', keys = {}, init = null, touch = false } = {}) {
    const ctx = await browser.newContext({ viewport: { width, height }, serviceWorkers: 'block', reducedMotion, isMobile: touch, hasTouch: touch });
    const d = { ctx, up: update(7, 'aaaaaaaaaaaa'), sockets: [] };
    await ctx.routeWebSocket(/\/ws$/, (ws) => {
      const server = ws.connectToServer();
      d.sockets.push(ws);
      server.onMessage((m) => {
        let j = null;
        try { j = JSON.parse(m); } catch { /* not JSON: as it came */ }
        ws.send(j?.type === 'hello' ? JSON.stringify({ ...j, update: d.up }) : m);
      });
    });
    if (init) await ctx.addInitScript(init);
    if (Object.keys(keys).length) await ctx.addInitScript((k) => { if (!sessionStorage.getItem('seeded')) { for (const [a, b] of Object.entries(k)) localStorage.setItem(a, b); sessionStorage.setItem('seeded', '1'); } }, keys);
    d.page = await ctx.newPage();
    d.page.on('pageerror', (e) => errors.push(e.message));
    d.page.on('dialog', (dlg) => { errors.push(`dialog: ${dlg.message()}`); dlg.dismiss().catch(() => {}); });
    d.navAt = 0;
    d.page.on('framenavigated', (f) => { if (f === d.page.mainFrame()) d.navAt = Date.now(); });
    const ready = () => d.page.waitForFunction(() => typeof S !== 'undefined' && Object.keys(S.instruments || {}).length > 0, null, { timeout: 30000 });
    d.load = async () => {   // the page, and again after a reload of its own (a first visit opens its workspace)
      await d.page.goto(BASE);
      do { await ready(); await d.page.waitForTimeout(250); } while (Date.now() - d.navAt < 1500);
      await d.page.waitForTimeout(500);   // the card's live region speaks a moment after it shows
    };
    d.broadcast = async (u) => {   // to every page and frame connected now, as Market.broadcast
      for (const ws of d.sockets) try { ws.send(JSON.stringify({ type: 'update', ...u })); } catch { /* a page reloaded since */ }
      await d.page.waitForTimeout(300);
    };
    d.card = () => d.page.evaluate(() => {
      const box = document.querySelector('#upd'), r = box.getBoundingClientRect(), main = document.querySelector('main').getBoundingClientRect();
      const plot = document.querySelector(document.body.classList.contains('multi') ? '#grid' : '#chart-wrap').getBoundingClientRect();
      return { shown: !box.hidden && getComputedStyle(box).display !== 'none', count: document.querySelector('#upd-count').textContent,
        side: box.classList.contains('side'), plotRight: plot.right, mainTop: main.top, say: document.querySelector('#upd-say').textContent,
        list: [...document.querySelectorAll('#upd-list li')].map((li) => li.textContent),
        more: document.querySelector('#upd-more').hidden ? null : document.querySelector('#upd-more').textContent,
        how: document.querySelector('#upd-how').textContent, imgs: box.querySelectorAll('img').length, pwned: !!window.pwned,
        rect: { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }, vw: innerWidth, vh: innerHeight,
        later: localStorage.getItem('updateLater'), seen: localStorage.getItem('updateSeen') };
    });
    // the card against the price axis of the chart it is over (a layout's: the bottom right one; pick 'left': the one
    // left of that, in its row): its right edge, the axis' left and right edges and width (its cell, as the library lays
    // the chart out), what is across the axis down the card's height, and at the live price's label and the countdown
    // under it, moved to the card's height for that (a price range set, then auto scale again): the chart's each time
    // ('chart'), never the card's ('card')
    d.axis = (pick = 'br') => d.page.evaluate(async (pick) => {
      const box = document.querySelector('#upd'), u = box.getBoundingClientRect();
      let win = window, fr = null, ox = 0, oy = 0;
      if (document.body.classList.contains('multi')) {
        const fs = [...document.querySelectorAll('#grid iframe')], rs = fs.map((f) => f.getBoundingClientRect());
        const right = Math.max(...rs.map((r) => r.right)), bottom = Math.max(...rs.map((r) => r.bottom));
        let i = fs.findIndex((f, j) => rs[j].right >= right - 2 && rs[j].bottom >= bottom - 2);
        if (pick === 'left') {
          const row = fs.map((f, j) => j).filter((j) => rs[j].bottom >= bottom - 2 && rs[j].right <= rs[i].left + 2);
          i = row.reduce((a, j) => (a < 0 || rs[j].right > rs[a].right ? j : a), -1);
        }
        fr = fs[i];
        ({ left: ox, top: oy } = fr.getBoundingClientRect());
        win = fr.contentWindow;
      }
      const doc = win.document, cr = doc.querySelector('#chart').getBoundingClientRect();
      const ax = doc.querySelector('#chart table tr').lastElementChild.getBoundingClientRect(), axis = ox + ax.left;
      const at = (x, y) => {
        const e = document.elementFromPoint(x, y);
        if (e?.closest('#upd')) return 'card';
        return (fr ? e === fr : doc.querySelector('#chart').contains(e)) ? 'chart' : (e?.id || e?.tagName || null);
      };
      const column = [];
      for (let y = Math.max(u.top, oy + ax.top) + 2; y < Math.min(u.bottom, oy + ax.bottom) - 1; y += 6) {
        for (const x of [axis + 1, axis + ax.width / 2, axis + ax.width - 2]) column.push(at(x, y));
      }
      const H = win.eval('chart.paneSize(0).height'), want = (u.top + u.bottom) / 2 - oy - cr.top;
      win.eval(`{ const hi = S.bars.at(-1).c + 40 * ${want} / ${H}; chart.priceScale('right').setVisibleRange({ from: hi - 40, to: hi }); }`);
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      const ly = oy + cr.top + win.eval('candles.priceToCoordinate(S.bars.at(-1).c)');
      const label = [at(axis + 2, ly), at(axis + ax.width / 2, ly), at(axis + ax.width - 3, ly), at(axis + 2, ly + 16), at(axis + ax.width / 2, ly + 16)];
      win.eval("chart.priceScale('right').setAutoScale(true)");
      return { side: box.classList.contains('side'), left: u.left, right: u.right, axis, axisW: ax.width, axisR: axis + ax.width, sym: win.eval('S.symbol'),
        column: [...new Set(column)], label, beside: ly > u.top && ly + 16 < u.bottom, vw: innerWidth };
    }, pick);
    // it fits: nothing wider than the window, nothing scrolled away in the card or past its edges, no text cut, the
    // buttons apart, and how tall they are
    d.fit = () => d.page.evaluate(() => {
      const box = document.querySelector('#upd'), u = box.getBoundingClientRect();
      const bs = [...box.querySelectorAll('button')].map((b) => b.getBoundingClientRect());
      const hit = (a, b) => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
      return { page: document.documentElement.scrollWidth - innerWidth, hidden: box.scrollHeight - box.clientHeight,
        out: [...box.querySelectorAll('*')].filter((e) => { const r = e.getBoundingClientRect(); return r.width && (r.left < u.left - 0.5 || r.right > u.right + 0.5 || r.bottom > u.bottom + 0.5); }).map((e) => e.id || e.tagName),
        cut: [...box.querySelectorAll('.upd-hd, #upd-more, #upd-how, button')].filter((e) => e.scrollWidth > e.clientWidth + 1).map((e) => e.id || e.className),
        overlap: bs.some((a, i) => bs.some((b, j) => j > i && hit(a, b))), buttons: bs.map((r) => Math.round(r.height)),
        allLine: bs.length === 3 && bs[0].bottom <= bs[1].top + 0.5 };
    });
    d.toggleSide = async () => { await d.page.click('#side-toggle'); await d.page.waitForTimeout(400); };
    d.symbol = async (sym, win = 'window') => {   // the chart (a layout's: the frame's) on another symbol, its axis drawn
      const w0 = await d.page.evaluate((w) => eval(w).eval("chart.priceScale('right').width()"), win);
      await d.page.evaluate(([w, s]) => eval(w).eval(`setSymbol('${s}')`), [win, sym]);
      await d.page.waitForFunction(([w, s, w0]) => eval(w).eval(`S.symbol === '${s}' && S.loadedSym === '${s}' && S.bars.length > 0 && chart.priceScale('right').width() !== ${w0}`), [win, sym, w0], { timeout: 20000 }).catch(() => {});
      await d.page.waitForTimeout(300);
    };
    return d;
  }
  try {
    // ---- a device that never saw it: shown on load, with the titles and the count; the server's texts as text ----
    const a = await device();
    await a.load();
    out.load = await a.card();
    out.aria = await a.page.evaluate(() => {
      const box = document.querySelector('#upd'), say = document.querySelector('#upd-say');
      return { role: box.getAttribute('role'), modal: box.getAttribute('aria-modal'), labelled: document.getElementById(box.getAttribute('aria-labelledby'))?.textContent,
        keys: box.getAttribute('aria-keyshortcuts'),
        say: [say.getAttribute('role'), say.getAttribute('aria-live'), say.textContent], focus: document.activeElement === document.body,
        buttons: [...box.querySelectorAll('button')].map((b) => b.textContent), anim: getComputedStyle(box).animationName };
    });
    // the chart takes a click beside it (not modal): the card stays
    await a.page.mouse.click(300, 400);
    out.chartClick = (await a.card()).shown;
    // Alt+U from the chart: the focus to Got it, seen (the card a few keys away, not seventy Tabs)
    await a.page.keyboard.press('Alt+U');
    out.altU = await a.page.evaluate(() => ({ id: document.activeElement.id, visible: document.activeElement.matches(':focus-visible') }));
    await a.page.keyboard.press('Shift+Tab');
    out.altUBack = await a.page.evaluate(() => document.activeElement.id);
    // the side panel hidden: over the chart, shorter (three titles), short of its price axis; prices with more digits
    // (NQ's), a wider axis: further left; shown again: back in its column, five; hidden again: short of the axis again
    await a.toggleSide();
    out.sideOff = await a.card();
    out.axisOff = await a.axis();
    out.fitOff = await a.fit();
    await a.symbol('NQ');
    out.axisNQ = await a.axis();
    await a.symbol('ES');
    out.axisES = await a.axis();
    await a.toggleSide();
    out.sideOn = await a.card();
    await a.toggleSide();
    out.axisOff2 = await a.axis();
    await a.toggleSide();
    // a menu open (the timeframes) with the focus in the card: Esc closes the menu first, the card stays
    await a.page.click('#tf-btn');
    await a.page.focus('#upd-later');
    await a.page.keyboard.press('Escape');
    out.escMenu = await a.page.evaluate(() => ({ menu: !document.querySelector('#tf-menu').hidden, card: !document.querySelector('#upd').hidden }));
    // keyboard: the focus moves through its buttons, seen; Esc inside it is Later
    await a.page.focus('#upd-all');
    await a.page.keyboard.press('Tab');
    out.tab = await a.page.evaluate(() => ({ id: document.activeElement.id, visible: document.activeElement.matches(':focus-visible'),
      outline: getComputedStyle(document.activeElement).outlineStyle }));
    await a.page.keyboard.press('Escape');
    out.esc = await a.card();
    out.escFocus = await a.page.evaluate(() => document.activeElement.closest('#upd') === null);
    // Later: not back on a reload, back after 24 hours (the stored time moved back) ...
    await a.load();
    out.laterReload = (await a.card()).shown;
    await a.page.evaluate(() => { const l = JSON.parse(localStorage.getItem('updateLater')); l.at -= 24 * 3600e3 + 60e3; localStorage.setItem('updateLater', JSON.stringify(l)); });
    await a.load();
    out.after24h = (await a.card()).shown;
    // ... and at once for a newer version, heard live; the focus stays where it is
    await a.page.click('#upd-later');
    out.laterClick = await a.card();
    await a.page.focus('#ctab-add');
    await a.broadcast(update(9, 'bbbbbbbbbbbb', ['A newer change', ...TITLES.slice(0, 4)]));
    out.newer = await a.card();
    out.newerFocus = await a.page.evaluate(() => document.activeElement.id);
    // Got it: gone until a newer version
    await a.page.click('#upd-ok');
    out.gotIt = await a.card();
    a.up = update(9, 'bbbbbbbbbbbb');
    await a.load();
    out.gotItReload = (await a.card()).shown;
    await a.broadcast(update(10, 'cccccccccccc'));
    out.gotItNewer = (await a.card()).shown;
    // All changes: Connections
    await a.page.click('#upd-all');
    await a.page.waitForTimeout(300);
    await a.page.waitForFunction(() => document.querySelector('#cn-list .cn-card[data-id="updates"]'), null, { timeout: 10000 }).catch(() => {});
    await a.page.waitForTimeout(300);
    out.allChanges = await a.page.evaluate(() => {
      const card = document.querySelector('#cn-list .cn-card[data-id="updates"]'), list = document.querySelector('#cn-list');
      const r = card?.getBoundingClientRect(), l = list.getBoundingClientRect();
      return { open: !document.querySelector('#cn-dlg').hidden, app: !!card?.closest('details')?.open, focus: document.activeElement === card,
        inView: !!r && r.top >= l.top - 1 && r.top < l.bottom, kept: localStorage.getItem('cnOpen') };
    });
    await a.page.keyboard.press('Escape');
    // the fired-alert panel shows above it, not over it
    await a.page.evaluate(() => firedShow({ ts: Date.now(), message: 'ES crossed 6900.00' }));
    await a.page.waitForTimeout(200);
    out.fired = await a.page.evaluate(() => {
      const f = document.querySelector('#fired').getBoundingClientRect(), u = document.querySelector('#upd').getBoundingClientRect();
      return { firedBottom: f.bottom, updTop: u.top };
    });
    await a.ctx.close();

    // ---- a device that saw the 9-second note for this version ('updateSeen'): nothing ----
    const b = await device({ keys: { updateSeen: 'aaaaaaaaaaaa' } });
    await b.load();
    out.sawNote = (await b.card()).shown;
    await b.ctx.close();

    // ---- a browser that keeps no site data (storage full or blocked): Later, Got it and Esc still close it ----
    const k = await device({ init: () => { Storage.prototype.setItem = function () { throw new DOMException('blocked', 'QuotaExceededError'); }; } });
    await k.load();
    out.blocked = { load: (await k.card()).shown };
    await k.page.click('#upd-later');
    await k.page.waitForTimeout(700);
    out.blocked.later = await k.card();
    await k.broadcast(update(7, 'aaaaaaaaaaaa'));   // heard again: still put off, for this page
    out.blocked.again = (await k.card()).shown;
    await k.broadcast(update(9, 'bbbbbbbbbbbb'));   // a newer version: shown
    out.blocked.newer = (await k.card()).shown;
    await k.page.click('#upd-ok');
    await k.page.waitForTimeout(700);
    out.blocked.gotIt = await k.card();
    await k.broadcast(update(10, 'cccccccccccc'));
    await k.page.focus('#upd-later');
    await k.page.keyboard.press('Escape');
    await k.page.waitForTimeout(700);
    out.blocked.esc = await k.card();
    await k.ctx.close();

    // ---- a short window (1280 x 800 at 200 %: 640 x 400): the count alone, its top under the header ----
    const sh = await device({ width: 640, height: 400 });
    await sh.load();
    out.short = await sh.card();
    await sh.ctx.close();
    // ---- a phone turned (844 x 390): in the side panel's column, the count alone, under the header ----
    const pl = await device({ width: 844, height: 390, touch: true });
    await pl.load();
    out.land = await pl.card();
    await pl.toggleSide();   // the panel hidden: over the chart
    out.landOff = { card: await pl.card(), axis: await pl.axis(), fit: await pl.fit() };
    // turned to portrait: placed for 390 px as the browser zooms to it (innerWidth still the width before for a while)
    await pl.page.setViewportSize({ width: 390, height: 844 });
    await pl.page.waitForTimeout(1200);
    out.turned = { card: await pl.card(), axis: await pl.axis(), fit: await pl.fit() };
    await pl.ctx.close();
    // ---- 601-760 px (700 x 900): over the chart, its drawing tools a row; short of the axis ----
    const m = await device({ width: 700, height: 900 });
    await m.load();
    out.mid = { card: await m.card(), axis: await m.axis(), fit: await m.fit() };
    await m.ctx.close();
    // ---- the smallest phone, 320 x 568: narrower to the axis, the count alone; it fits, the buttons a finger's size ----
    const n = await device({ width: 320, height: 568, touch: true });
    await n.load();
    out.p320 = { card: await n.card(), axis: await n.axis(), fit: await n.fit() };
    await n.symbol('NQ');
    out.p320NQ = { axis: await n.axis(), fit: await n.fit() };
    await n.ctx.close();

    // ---- a layout of four charts (▦ > 2 × 2): one card, on the page around them ----
    const c = await device();
    await c.load();
    await c.page.click('#ws-btn');
    await c.page.click('#ws-menu button[title="2 × 2"]');   // saved in the workspace, and the page reloads
    await c.page.waitForFunction(() => document.querySelectorAll('#grid iframe').length === 4, null, { timeout: 30000 });
    await c.page.waitForFunction(() => document.querySelectorAll('#grid iframe').length === 4
      && [...document.querySelectorAll('#grid iframe')].every((f) => { try { return f.contentWindow.eval("typeof S !== 'undefined' && Object.keys(S.instruments || {}).length > 0"); } catch { return false; } }), null, { timeout: 30000 });
    await c.page.waitForTimeout(500);
    out.layout = await c.page.evaluate(() => {
      const docs = [document, ...[...document.querySelectorAll('#grid iframe')].map((f) => f.contentDocument)];
      return { frames: docs.length - 1, multi: document.body.classList.contains('multi'),
        shown: docs.map((d) => [...d.querySelectorAll('#upd')].filter((e) => !e.hidden && getComputedStyle(e).display !== 'none').length) };
    });
    await c.broadcast(update(8, 'dddddddddddd'));
    out.layoutLive = await c.page.evaluate(() => [document, ...[...document.querySelectorAll('#grid iframe')].map((f) => f.contentDocument)]
      .map((d) => [...d.querySelectorAll('#upd')].filter((e) => !e.hidden).length));
    out.layoutCard = await c.card();   // in the side panel's column, beside the charts
    // Alt+U in a chart of the layout: the page's card; Esc: back to that chart
    const fr = await c.page.locator('#grid iframe').nth(1).boundingBox();
    await c.page.mouse.click(fr.x + fr.width * 0.4, fr.y + fr.height * 0.3);
    await c.page.keyboard.press('Alt+U');
    await c.page.waitForTimeout(300);   // the chart's message to the page
    out.layoutAltU = { focus: await c.page.evaluate(() => document.activeElement.id) };
    await c.page.keyboard.press('Escape');
    Object.assign(out.layoutAltU, await c.page.evaluate(() => ({ back: document.activeElement.tagName, shown: !document.querySelector('#upd').hidden })));
    // the panel hidden (and a newer version, Esc having put this one off): over the charts, short of the bottom right
    // chart's axis; that chart on NQ (it says so), and that chart reloaded (a new page in its frame): still short of it
    await c.toggleSide();
    await c.broadcast(update(9, 'eeeeeeeeeeee'));
    await c.page.waitForTimeout(400);   // the charts' word on their axes
    out.gridOff = { card: await c.card(), axis: await c.axis(), fit: await c.fit() };
    const br = "[...document.querySelectorAll('#grid iframe')].at(-1).contentWindow";
    await c.symbol('NQ', br);
    out.gridNQ = await c.axis();
    await c.page.evaluate((w) => eval(w).location.reload(), br);
    await c.page.waitForTimeout(500);
    await c.page.waitForFunction((w) => { try { return eval(w).eval("typeof S !== 'undefined' && S.bars.length > 0 && chart.priceScale('right').width() > 0"); } catch { return false; } }, br, { timeout: 30000 });
    await c.page.waitForTimeout(800);
    out.gridReload = await c.axis();
    // the layout's window made narrow (560 px: a phone's width, the charts still side by side): inside the bottom right
    // chart, the bottom left one's axis clear
    await c.page.setViewportSize({ width: 560, height: 800 });
    await c.page.waitForTimeout(1000);
    out.grid560 = { card: await c.card(), br: await c.axis(), left: await c.axis('left'), fit: await c.fit() };
    await c.page.setViewportSize({ width: 1280, height: 800 });
    // three charts across (3 x 2, then 3 side by side), the panel hidden, on a window under 1000 px: the card inside the
    // right-hand chart, the axis and live price of the chart left of it clear; under 240 px wide, the count alone
    c.up = update(9, 'eeeeeeeeeeee');   // the hello after the layout's reload: the version shown now
    for (const [title, n, w, key] of [['3 × 2', 6, 900, 'g32'], ['3 side by side', 3, 860, 'g3c']]) {
      await c.page.click('#ws-btn');
      await c.page.click(`#ws-menu button[title="${title}"]`);
      await c.page.waitForFunction((k) => document.querySelectorAll('#grid iframe').length === k
        && [...document.querySelectorAll('#grid iframe')].every((f) => { try { return f.contentWindow.eval("typeof S !== 'undefined' && S.bars.length > 0 && chart.priceScale('right').width() > 0"); } catch { return false; } }), n, { timeout: 40000 });
      await c.page.setViewportSize({ width: w, height: 800 });
      await c.page.waitForTimeout(1200);
      out[key] = { card: await c.card(), br: await c.axis(), left: await c.axis('left'), fit: await c.fit() };
      await c.page.setViewportSize({ width: 1280, height: 800 });
      await c.page.waitForTimeout(800);
      out[`${key}Wide`] = { card: await c.card(), br: await c.axis() };
    }
    await c.ctx.close();

    // ---- a phone, 390 px: across the bottom, 16 px in from each side, nothing wider than the screen; reduced motion ----
    const p = await device({ width: 390, height: 844, reducedMotion: 'reduce' });
    await p.load();
    out.phone = await p.card();
    out.phoneAxis = await p.axis();
    out.phoneFit = await p.fit();
    out.phoneMore = await p.page.evaluate(() => ({
      scroll: [document.documentElement.scrollWidth, document.documentElement.clientWidth, document.body.scrollWidth],
      wide: [...document.querySelectorAll('#upd, #upd *')].filter((e) => e.getBoundingClientRect().right > innerWidth + 0.5).map((e) => e.id || e.tagName),
      buttons: [...document.querySelectorAll('#upd button')].map((b) => Math.round(b.getBoundingClientRect().height)),
      anim: getComputedStyle(document.querySelector('#upd')).animationName }));
    // the Watchlist page over the chart: no axis to measure, a usual one's kept clear; the chart again: its own
    await p.page.click('.tabs button[data-tab="watch"]');
    await p.page.waitForTimeout(300);
    out.phonePage = await p.card();
    await p.page.click('.tabs .pg-chart');
    await p.page.waitForTimeout(400);
    out.phoneBack = await p.axis();
    await p.ctx.close();
  } catch (e) {
    out.error = String(e.stack || e);
  }
  out.errors = errors;
  await browser.close();
  console.log(JSON.stringify(out));
})();
