// Runs the real draw.io app in headless Chromium.
//
//   node drawio_headless.mjs dump   <in.drawio> <out.json>   [--page N]
//   node drawio_headless.mjs export <in.drawio> <out.vsdx>
//   node drawio_headless.mjs raster <jobs.json> -      (SVG -> PNG sin cairo)
//   node drawio_headless.mjs png    <in.drawio> <out.png> [--page N]  (referencia: cómo lo pinta draw.io)
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
import crypto from 'crypto';
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
if (!['dump', 'export', 'raster', 'png'].includes(mode) || !inFile || !outFile) {
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

if (mode === 'png') {
  const svg = await page.evaluate(() => {
    const g = window.theUi.editor.graph;
    return 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(mxUtils.getXml(g.getSvg('#ffffff', 1, 10)))));
  });
  const shot = await browser.newPage();
  await shot.setContent('<html><body style="margin:0;background:#fff"><img id="i"></body></html>');
  const [w, h] = await shot.evaluate(async (src) => { const i = document.getElementById('i'); await new Promise(ok => { i.onload = ok; i.onerror = ok; i.src = src; }); return [i.naturalWidth, i.naturalHeight]; }, svg);
  await shot.setViewportSize({ width: Math.max(1, Math.min(w, 8000)), height: Math.max(1, Math.min(h, 8000)) });
  await shot.locator('#i').screenshot({ path: outFile });
  console.log('png', w, h);
  await browser.close(); server.close(); process.exit(0);
}
if (mode === 'dump') {
  const out = await page.evaluate(() => {
    const g = window.theUi.editor.graph, v = g.view, s = v.scale, t = v.translate, m = g.model;
    const abs = (x, y) => [x / s - t.x, y / s - t.y];
    const res = [];
    // Text exactly as draw.io paints it: lines of runs with their computed format
    const hex = (c) => { const m = (c || '').match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)/); return m ? '#' + [m[1], m[2], m[3]].map(x => (+x | 0).toString(16).padStart(2, '0')).join('').toUpperCase() : null; };
    const fmt = (el) => { const cs = getComputedStyle(el); const fw = cs.fontWeight;
      return { color: hex(cs.color) || hex(cs.fill) || '#000000', size: parseFloat(cs.fontSize) || 12,
               font: (cs.fontFamily || '').split(',')[0].replace(/["']/g, '').trim(),
               bold: fw === 'bold' || fw === 'bolder' || parseInt(fw) >= 600, italic: cs.fontStyle === 'italic',
               underline: (cs.textDecorationLine || cs.textDecoration || '').indexOf('underline') >= 0 }; };
    const BLOCK = /^(DIV|P|LI|UL|OL|H[1-6]|TABLE|TR|BLOCKQUOTE|PRE)$/;
    const textRuns = (st) => {
      const tn = st.text && st.text.node; if (!tn) return null;
      const lines = [[]];
      const nl = () => { if (lines[lines.length - 1].length) lines.push([]); };
      const fo = tn.querySelector('foreignObject');
      if (fo) {
        let root = fo.querySelector('div');
        while (root && root.childNodes.length === 1 && root.firstElementChild && root.firstElementChild.tagName === 'DIV') root = root.firstElementChild;
        if (!root) return null;
        const walkT = (n) => {
          for (const c of n.childNodes) {
            if (c.nodeType === 3) {
              const t = c.textContent.replace(/\s+/g, ' ');
              if (t.trim() || (t && lines[lines.length - 1].length)) lines[lines.length - 1].push(Object.assign({ text: t }, fmt(c.parentElement)));
            } else if (c.nodeType === 1) {
              if (c.tagName === 'BR') { lines.push([]); continue; }
              if (getComputedStyle(c).display === 'none') continue;
              const blk = BLOCK.test(c.tagName);
              if (blk) nl();
              walkT(c);
              if (blk) nl();
            }
          }
        };
        walkT(root);
      } else {
        for (const t of tn.querySelectorAll('text')) { lines[lines.length - 1].push(Object.assign({ text: t.textContent }, fmt(t))); lines.push([]); }
      }
      const out = lines.map(l => { if (l.length) { l[0].text = l[0].text.replace(/^ +/, ''); l[l.length - 1].text = l[l.length - 1].text.replace(/ +$/, ''); } return l.filter(r => r.text); })
                       .filter(l => l.length);
      return out.length ? out : null;
    };
    // HTML tables in labels: Visio text cannot hold a table, so the table is measured as
    // laid out by the browser (cell boxes, text, row/cell backgrounds, borders) and rebuilt
    // in Visio with one text box per cell plus lines and fills.
    const svgEl = g.view.getDrawPane().ownerSVGElement;
    const toModel = (r) => { const sr = svgEl.getBoundingClientRect(); const [x, y] = abs(r.left - sr.left, r.top - sr.top); return [x, y, r.width / s, r.height / s]; };
    // visual=true: also break where the browser wrapped the text, so the lines are
    // exactly draw.io's whatever font metrics the viewer uses
    const linesOf = (nodes, visual) => {
      const lines = [[]];
      let lastTop = null;
      const nl = () => { if (lines[lines.length - 1].length) lines.push([]); };
      const push = (t, el) => { t = t.replace(/\s+/g, ' '); if (t.trim() || (t && lines[lines.length - 1].length)) lines[lines.length - 1].push(Object.assign({ text: t }, fmt(el))); };
      const walkT = (n) => {
        if (n.nodeType === 3) {
          if (!visual) { push(n.textContent, n.parentElement); return; }
          const t = n.textContent, re = /\S+\s*/g; let m, buf = (t.match(/^\s*/) || [''])[0];
          while ((m = re.exec(t))) {
            const rg = document.createRange(); rg.setStart(n, m.index); rg.setEnd(n, m.index + Math.max(1, m[0].trimEnd().length));
            const rc = rg.getClientRects()[0];
            if (rc) {
              if (lastTop !== null && rc.top > lastTop + rc.height * 0.5) { if (buf) push(buf, n.parentElement); buf = ''; lines.push([]); }
              lastTop = rc.top;
            }
            buf += m[0];
          }
          if (buf) push(buf, n.parentElement);
          return;
        }
        if (n.nodeType !== 1) return;
        if (n.tagName === 'BR') { lines.push([]); return; }
        if (getComputedStyle(n).display === 'none') return;
        const blk = BLOCK.test(n.tagName);
        if (blk) nl();
        for (const c of n.childNodes) walkT(c);
        if (blk) nl();
      };
      for (const n of nodes) walkT(n);
      const out = lines.map(l => { if (l.length) { l[0].text = l[0].text.replace(/^ +/, ''); l[l.length - 1].text = l[l.length - 1].text.replace(/ +$/, ''); } return l.filter(r => r.text); }).filter(l => l.length);
      return out.length ? out : null;
    };
    const bgOf = (el) => { const c = getComputedStyle(el).backgroundColor; const m = (c || '').match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?/); return (m && (m[4] === undefined || +m[4] > 0.05)) ? hex(c) : null; };
    // CSS px -> screen px for an element (the label may be scaled by the view and by draw.io)
    const kOf = (el) => (el.offsetWidth ? el.getBoundingClientRect().width / el.offsetWidth : s);
    const contentBox = (el) => { const cs = getComputedStyle(el), r = el.getBoundingClientRect(), k = kOf(el);
      const L = k * (parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth)), R = k * (parseFloat(cs.paddingRight) + parseFloat(cs.borderRightWidth));
      const T = k * (parseFloat(cs.paddingTop) + parseFloat(cs.borderTopWidth)), B = k * (parseFloat(cs.paddingBottom) + parseFloat(cs.borderBottomWidth));
      return { left: r.left + L, top: r.top + T, width: Math.max(1, r.width - L - R), height: Math.max(1, r.height - T - B) }; };
    const tableLayout = (st) => {
      const tn = st.text && st.text.node; const fo = tn && tn.querySelector('foreignObject');
      if (!fo || !fo.querySelector('table')) return null;
      let root = fo.querySelector('div');
      while (root && root.childNodes.length === 1 && root.firstElementChild && root.firstElementChild.tagName === 'DIV') root = root.firstElementChild;
      const lay = { blocks: [], fills: [], rules: [] };
      const addBlock = (nodes, box, align) => { const ln = linesOf(nodes, true); if (ln) lay.blocks.push({ b: toModel(box), runs: ln, align, exact: true }); };
      const alignOf = (el) => { const a = getComputedStyle(el).textAlign; return a === 'center' ? 'center' : (a === 'right' || a === 'end') ? 'right' : 'left'; };
      const borders = (el) => {
        const cs = getComputedStyle(el), r = el.getBoundingClientRect();
        for (const [side, a, b] of [['Top', [r.left, r.top], [r.right, r.top]], ['Bottom', [r.left, r.bottom], [r.right, r.bottom]],
                                    ['Left', [r.left, r.top], [r.left, r.bottom]], ['Right', [r.right, r.top], [r.right, r.bottom]]]) {
          const w = parseFloat(cs['border' + side + 'Width']), sty = cs['border' + side + 'Style'];
          if (w > 0 && sty !== 'none' && sty !== 'hidden') {
            const p = toModel({ left: a[0], top: a[1], width: b[0] - a[0], height: b[1] - a[1] });
            lay.rules.push({ p: [[p[0], p[1]], [p[0] + p[2], p[1] + p[3]]], color: hex(cs['border' + side + 'Color']) || '#000000', w: w * kOf(el) / s, dashed: sty === 'dashed' || sty === 'dotted' });
          }
        }
      };
      const doTable = (tb) => {
        const tbg = bgOf(tb); if (tbg) lay.fills.push({ b: toModel(tb.getBoundingClientRect()), color: tbg });
        for (const tr of tb.querySelectorAll('tr')) {
          const rbg = bgOf(tr); if (rbg) lay.fills.push({ b: toModel(tr.getBoundingClientRect()), color: rbg });
          for (const td of tr.children) {
            const cbg = bgOf(td); if (cbg) lay.fills.push({ b: toModel(td.getBoundingClientRect()), color: cbg });
            borders(td);
            addBlock(Array.from(td.childNodes), contentBox(td), alignOf(td));
          }
        }
      };
      // content outside the tables: each run of consecutive non-table nodes is one block
      let pending = [];
      const flush = () => {
        if (!pending.length) return;
        const rg = document.createRange(); rg.setStartBefore(pending[0]); rg.setEndAfter(pending[pending.length - 1]);
        const r = rg.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) addBlock(pending, r, alignOf(pending[0].nodeType === 1 ? pending[0] : pending[0].parentElement));
        pending = [];
      };
      for (const c of Array.from(root.childNodes)) {
        if (c.nodeType === 1 && (c.tagName === 'TABLE' || c.querySelector('table'))) {
          flush();
          if (c.tagName === 'TABLE') doTable(c); else for (const t of c.querySelectorAll('table')) doTable(t);
        } else pending.push(c);
      }
      flush();
      return lay;
    };
    const walk = (cell) => {
      for (let i = 0; i < m.getChildCount(cell); i++) {
        const c = m.getChildAt(cell, i);
        const st = v.getState(c);
        if (st && g.isCellVisible(c) && c.parent !== m.root) {
          const o = { id: c.id, parent: c.parent ? c.parent.id : null, edge: c.edge ? 1 : 0, style: c.style || '', value: (typeof c.value === 'string') ? c.value : (c.value && c.value.getAttribute ? (c.value.getAttribute('label') || '') : '') };
          // sombra de todo el diagrama (casilla «Sombra» del panel Diagrama): se pasa a
          // cada forma, salvo las que la desactivan con shadow=0
          if (g.shadowVisible && !/(^|;)shadow=/.test(o.style)) o.style += ';shadow=1';
          if (c.edge) {
            o.pts = st.absolutePoints.filter(Boolean).map(q => abs(q.x, q.y));
            const vs = st.getVisibleTerminal(true), vt = st.getVisibleTerminal(false);
            if (vs) o.source = vs.id;
            if (vt) o.target = vt.id;
          }
          else { const [x, y] = abs(st.x, st.y); o.b = [x, y, st.width / s, st.height / s]; }
          try { const lay = tableLayout(st); if (lay) o.layout = lay; } catch (e) { o.rerr = 'tabla: ' + String(e).slice(0, 100); }
          try { const r = textRuns(st); if (r) o.runs = r; } catch (e) {}
          if (st.text && st.text.boundingBox) {
            const bb = st.text.boundingBox; const [x, y] = abs(bb.x, bb.y);
            o.tb = [x, y, bb.width / s, bb.height / s];
          }
          // Elements the Visio exporter cannot draw: draw.io renders them and they go in as images
          if (!c.edge) {
            const shp = (st.style && st.style.shape) || '';
            const html = (typeof o.value === 'string') ? o.value : '';
            const im = html.match(/<img\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/i);
            if (im && html.replace(/<img\b[^>]*>/gi, '').replace(/<[^>]+>/g, '').trim() === '') {
              const wa = html.match(/\bwidth=["']?(\d+)/i), ha = html.match(/\bheight=["']?(\d+)/i);
              o.rkind = 'img'; o.rsrc = im[1];
              o.rsize = [wa ? +wa[1] : o.b[2], ha ? +ha[1] : o.b[3]];
            } else if (shp.indexOf('mxgraph.') === 0 && o.b[2] <= 400 && o.b[3] <= 400) {
              try {
                const div = document.createElement('div');
                div.style.cssText = 'position:absolute;left:-9999px;top:0;width:1px;height:1px;overflow:hidden';
                document.body.appendChild(div);
                const tg = new Graph(div);
                tg.setStylesheet(g.getStylesheet());
                const cl = c.clone(); cl.value = ''; cl.geometry = new mxGeometry(0, 0, o.b[2], o.b[3]);
                cl.style = (cl.style || '').replace(/(^|;)rotation=[^;]*/g, '$1');
                tg.model.beginUpdate(); try { tg.addCell(cl); } finally { tg.model.endUpdate(); }
                const svg = tg.getSvg(null, 1, 1);
                o.rkind = 'stencil'; o.rsrc = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(mxUtils.getXml(svg))));
                o.rsize = [o.b[2] + 2, o.b[3] + 2];
                tg.destroy(); div.remove();
              } catch (e) { o.rerr = String(e).slice(0, 120); }
            }
          }
          res.push(o);
        }
        walk(c);
      }
    };
    walk(m.root);
    return res;
  });
  // capture each element with the browser (screenshot: no canvas tainting, foreignObject text works)
  const RDIR = path.join(WEB, '_hires', 'raster');
  fs.mkdirSync(RDIR, { recursive: true });
  const shot = await browser.newPage();
  let nr = 0;
  for (const o of out) {
    if (o.rerr) console.log('AVISO: no se pudo dibujar la forma', o.id, o.rerr);
    if (!o.rkind) continue;
    const src = o.rsrc; delete o.rsrc;
    const [nw, nh] = o.rsize;
    const k = o.rkind === 'img' ? Math.min(2, 4000 / Math.max(nw, nh)) : Math.max(4, 256 / Math.max(1, Math.min(nw, nh)));
    const W = Math.max(1, Math.round(nw * k)), H = Math.max(1, Math.round(nh * k));
    if (!/^data:/i.test(src) && !src.startsWith('/') && !/^https?:\/\/127\.0\.0\.1/.test(src)) {
      console.log('AVISO: imagen externa no accesible sin conexión:', src.slice(0, 100));
      delete o.rkind; continue;
    }
    try {
      await shot.setViewportSize({ width: Math.min(W, 8000), height: Math.min(H, 8000) });
      await shot.setContent(`<html><body style="margin:0;background:transparent"><img id="i" style="display:block;width:${W}px;height:${H}px"></body></html>`);
      await shot.evaluate(async (src) => { const i = document.getElementById('i'); await new Promise((ok) => { i.onload = ok; i.onerror = ok; i.src = src; }); }, src);
      const name = 'r_' + crypto.createHash('sha1').update(src + W).digest('hex').slice(0, 16) + '.png';
      await shot.locator('#i').screenshot({ path: path.join(RDIR, name), omitBackground: true });
      o.raster = '__WEBROOT__/_hires/raster/' + name;
      if (o.rkind === 'img') {   // the picture overflows the text cell: use its real extent
        const bx = o.tb || o.b;
        o.b = [bx[0] + (bx[2] - nw) / 2, bx[1] + (bx[3] - nh) / 2, nw, nh];
        delete o.tb;
      }
      nr++;
    } catch (e) { console.log('AVISO: no se pudo rasterizar', o.id, String(e).slice(0, 100)); delete o.rkind; }
  }
  await shot.close();
  fs.writeFileSync(outFile, JSON.stringify(out, null, 1));
  console.log('dumped', out.length, 'cells,', nr, 'rendered as image');
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
