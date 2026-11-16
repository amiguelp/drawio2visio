// Runs the real draw.io app in headless Chromium.
//
//   node drawio_headless.mjs dump   <in.drawio> <out.json>   [--page N]
//   node drawio_headless.mjs export <in.drawio> <out.vsdx>
//   node drawio_headless.mjs raster <jobs.json> -      (SVG -> PNG sin cairo)
//
// dump   : loads the diagram and writes, for every cell, its absolute bounds,
//          the bounds of its label as draw.io laid it out, and for every edge
//          the exact routed points draw.io computed. This is what keeps arrows
//          and texts where the user saw them.
// export : runs draw.io's VsdxExport on the diagram and saves the .vsdx.
//
// Env: DRAWIO_VSDX_HOME (default ~/.cache/drawio-vsdx), set up by setup_env.sh
import http from 'http';
import fs from 'fs';
import path from 'path';
import os from 'os';
import { createRequire } from 'module';
import { pathToFileURL } from 'url';

const W = process.env.DRAWIO_VSDX_HOME || path.join(os.homedir(), '.cache', 'drawio-vsdx');
const WEB = path.join(W, 'webroot');
const req = createRequire(path.join(W, 'node', 'package.json'));
const pwMod = await import(pathToFileURL(req.resolve('playwright-core')).href);
const pw = pwMod.chromium || (pwMod.default && pwMod.default.chromium);

const [mode, inFile, outFile] = process.argv.slice(2);
const pageArg = process.argv.indexOf('--page');
const pageIndex = pageArg > 0 ? parseInt(process.argv[pageArg + 1], 10) : 0;
if (!['dump', 'export', 'raster'].includes(mode) || !inFile || !outFile) {
  console.error('usage: node drawio_headless.mjs dump|export|raster <in> <out> [--page N]');
  process.exit(2);
}

// --- tiny static server for the web app (diagrams.net is not reachable) ---
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.gif': 'image/gif', '.json': 'application/json', '.txt': 'text/plain',
  '.xml': 'application/xml', '.woff2': 'font/woff2', '.woff': 'font/woff' };
const server = http.createServer((rq, rs) => {
  try {
    const u = decodeURIComponent(new URL(rq.url, 'http://x').pathname);
    let f = path.normalize(path.join(WEB, u));
    if (!f.toLowerCase().startsWith(path.normalize(WEB).toLowerCase())) { rs.writeHead(403); return rs.end(); }
    if (fs.existsSync(f) && fs.statSync(f).isDirectory()) f = path.join(f, 'index.html');
    if (!fs.existsSync(f)) { rs.writeHead(404); return rs.end(); }
    rs.writeHead(200, { 'Content-Type': MIME[path.extname(f).toLowerCase()] || 'application/octet-stream' });
    fs.createReadStream(f).pipe(rs);
  } catch (e) { rs.writeHead(500); rs.end(); }
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const ORIGIN = `http://127.0.0.1:${server.address().port}`;

// The diagram refers to images as __WEBROOT__/... (see prepare step); point them at this server.
const xml = mode === 'raster' ? '' : fs.readFileSync(inFile, 'utf8').split('__WEBROOT__').join(ORIGIN);

// Browser: DRAWIO_BROWSER (ruta a un chrome/msedge.exe) > Chromium de npm (Linux)
// > Microsoft Edge instalado > Google Chrome instalado > Chromium de Playwright.
async function launch() {
  const tried = [];
  if (process.env.DRAWIO_BROWSER) return pw.launch({ executablePath: process.env.DRAWIO_BROWSER, headless: true });
  if (process.platform === 'linux') {
    try {
      const chMod = await import(pathToFileURL(req.resolve('@sparticuz/chromium')).href);
      const ch = chMod.default && chMod.default.executablePath ? chMod.default : (chMod.default && chMod.default.default) || chMod;
      return await pw.launch({ executablePath: await ch.executablePath(), args: ch.args, headless: true });
    } catch (e) { tried.push('@sparticuz/chromium: ' + e.message.split('\n')[0]); }
  }
  for (const channel of ['msedge', 'chrome']) {
    try { return await pw.launch({ channel, headless: true }); }
    catch (e) { tried.push(channel + ': ' + e.message.split('\n')[0]); }
  }
  try { return await pw.launch({ headless: true }); }
  catch (e) { tried.push('playwright chromium: ' + e.message.split('\n')[0]); }
  console.error('No se encontró navegador (Edge/Chrome). Define DRAWIO_BROWSER con la ruta a msedge.exe o chrome.exe.\n' + tried.join('\n'));
  process.exit(3);
}
const browser = await launch();

if (mode === 'raster') {
  // jobs: [{svg: path, png: path, width: px}] -> PNG con fondo transparente
  const jobs = JSON.parse(fs.readFileSync(inFile, 'utf8'));
  const page = await browser.newPage();
  await page.goto(`${ORIGIN}/index.html?offline=1`, { waitUntil: 'domcontentloaded' }).catch(() => {});
  let ok = 0;
  for (const j of jobs) {
    const svg = fs.readFileSync(j.svg, 'utf8');
    const b64 = await page.evaluate(async ({ svg, width }) => {
      const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
      const el = doc.documentElement;
      let vw = parseFloat(el.getAttribute('width')), vh = parseFloat(el.getAttribute('height'));
      const vb = (el.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
      if (vb.length === 4 && vb[2] > 0 && vb[3] > 0) { if (!(vw > 0 && vh > 0)) { vw = vb[2]; vh = vb[3]; } }
      else if (vw > 0 && vh > 0) el.setAttribute('viewBox', `0 0 ${vw} ${vh}`);
      if (!(vw > 0 && vh > 0)) { vw = 100; vh = 100; }
      const W = Math.round(width), H = Math.max(1, Math.round(width * vh / vw));
      el.setAttribute('width', W); el.setAttribute('height', H);
      el.setAttribute('preserveAspectRatio', 'none');
      const src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(new XMLSerializer().serializeToString(doc))));
      const img = new Image();
      await new Promise((ok, ko) => { img.onload = ok; img.onerror = () => ko(new Error('svg no válido')); img.src = src; });
      const c = document.createElement('canvas'); c.width = W; c.height = H;
      c.getContext('2d').drawImage(img, 0, 0, W, H);
      return c.toDataURL('image/png').split(',')[1];
    }, { svg, width: j.width }).catch(e => { console.error('raster', j.svg, e.message.slice(0, 120)); return null; });
    if (b64) { fs.writeFileSync(j.png, Buffer.from(b64, 'base64')); ok++; }
  }
  console.log('rasterized', ok, 'of', jobs.length, 'svg');
  await browser.close(); server.close(); process.exit(0);
}
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
page.on('pageerror', e => console.error('pageerror:', e.message.slice(0, 200)));
await page.addInitScript(() => {
  const iv = setInterval(() => {
    if (window.EditorUi && !EditorUi.__hooked) {
      EditorUi.__hooked = true;
      const orig = EditorUi.prototype.init;
      EditorUi.prototype.init = function () { window.theUi = this; return orig.apply(this, arguments); };
      clearInterval(iv);
    }
  }, 1);
});
await page.goto(`${ORIGIN}/index.html?offline=1&lang=en&pwa=0&gapi=0&db=0&od=0&tr=0&gh=0&gl=0&mode=device&splash=0`, { waitUntil: 'load' });
await page.waitForFunction(() => window.theUi && window.theUi.editor, null, { timeout: 90000 });

// load the diagram
await page.evaluate(async ({ xml, pageIndex }) => {
  const ui = window.theUi;
  try { ui.hideDialog(); } catch (e) {}
  ui.fileLoaded(new LocalFile(ui, xml, 'diagram.drawio', true));
  await new Promise(r => setTimeout(r, 1500));
  if (ui.pages && ui.pages[pageIndex] && ui.currentPage !== ui.pages[pageIndex]) ui.selectPage(ui.pages[pageIndex]);
  await new Promise(r => setTimeout(r, 2500));   // let images and fonts settle
}, { xml, pageIndex });

if (mode === 'dump') {
  const out = await page.evaluate(() => {
    const g = window.theUi.editor.graph, v = g.view, s = v.scale, t = v.translate, m = g.model;
    const abs = (x, y) => [x / s - t.x, y / s - t.y];
    const res = [];
    const walk = (cell) => {
      for (let i = 0; i < m.getChildCount(cell); i++) {
        const c = m.getChildAt(cell, i);
        const st = v.getState(c);
        if (st && g.isCellVisible(c) && c.parent !== m.root) {
          const o = { id: c.id, parent: c.parent ? c.parent.id : null, edge: c.edge ? 1 : 0, style: c.style || '', value: (typeof c.value === 'string') ? c.value : (c.value && c.value.getAttribute ? (c.value.getAttribute('label') || '') : '') };
          if (c.edge) o.pts = st.absolutePoints.filter(Boolean).map(q => abs(q.x, q.y));
          else { const [x, y] = abs(st.x, st.y); o.b = [x, y, st.width / s, st.height / s]; }
          if (st.text && st.text.boundingBox) {
            const bb = st.text.boundingBox; const [x, y] = abs(bb.x, bb.y);
            o.tb = [x, y, bb.width / s, bb.height / s];
          }
          res.push(o);
        }
        walk(c);
      }
    };
    walk(m.root);
    return res;
  });
  fs.writeFileSync(outFile, JSON.stringify(out, null, 1));
  console.log('dumped', out.length, 'cells');
} else {
  const res = await page.evaluate(async () => {
    const ui = window.theUi;
    const load = (src) => new Promise((ok, ko) => { const sc = document.createElement('script'); sc.src = src; sc.onload = ok; sc.onerror = ko; document.head.appendChild(sc); });
    if (typeof JSZip === 'undefined') await load('js/jszip/jszip.min.js');
    await load('js/vsdxold/mxVsdxCanvas2D.js');
    await load('js/vsdxold/VsdxExport.js');
    return await new Promise((resolve) => {
      ui.saveData = (name, fmt, data) => resolve({ data });
      ui.handleError = (e) => resolve({ err: String(e && (e.message || e)) });
      try { if (!new VsdxExport(ui).exportCurrentDiagrams(false)) resolve({ err: 'VsdxExport returned false' }); }
      catch (e) { resolve({ err: String(e.stack || e) }); }
      setTimeout(() => resolve({ err: 'timeout' }), 90000);
    });
  });
  if (res.err) { console.error('ERROR', res.err); process.exitCode = 1; }
  else { fs.writeFileSync(outFile, Buffer.from(res.data, 'base64')); console.log('saved', outFile); }
}
await browser.close();
server.close();
