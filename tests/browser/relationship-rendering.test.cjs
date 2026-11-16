const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const staticRoot = path.resolve(__dirname, '../../leakscope_app/static');

test('Chrome renders complete lane labels, aligned directed paths and a visible bounded matrix', async () => {
  const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser = await chromium.launch({ headless: true, ...(fs.existsSync(chrome) ? { executablePath: chrome } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.setContent('<main id="page-root" class="asset-page" style="width:360px"></main>');
    for (const css of ['leakscope', 'relationships', 'workspace', 'table_browser', 'relationship_map', 'experience']) {
      await page.addStyleTag({ path: path.join(staticRoot, 'css', css + '.css') });
    }
    for (const js of ['tables', 'relationship_map']) await page.addScriptTag({ path: path.join(staticRoot, 'js/leakscope', js + '.js') });
    await page.evaluate(() => {
      const nodes = Array.from({ length: 59 }, (_, i) => ({ id: 'n' + i, kind: 'domain', value: '*.boomerang-dsb.andurildev.com-' + i }));
      nodes.push({ id: 'cert', kind: 'certificate', value: '06:EE:9B:9E:36:35:B8:7A:F6:AF:C8:02:A7:5F:CC:C9:D3:A4:1B:0B:2D:B0:36:B8:93:FF:E9:EF:06:52:29:2F' });
      const map = LeakScopeRelationshipMap.create({ nodes, edges: [{ id: 'edge', source: 'n0', target: 'cert', relation: 'certificate_name', evidence_source: 'certificate' }] });
      document.querySelector('main').append(map.element);
    });
    // Lanes are redrawn to the space they get once attached; measure after that settles.
    await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
    assert.equal(await page.evaluate(() => { const scroll = document.querySelector('.ls-rel-scroll'); return scroll.scrollWidth <= scroll.clientWidth + 1; }), true, 'lanes fit their space without sideways scrolling');
    const geometry = await page.locator('.ls-rel-node').first().evaluate(node => {
      const label = node.querySelector('.ls-rel-node-name'), kind = node.querySelector('.ls-rel-node-kind');
      const n = node.getBoundingClientRect(), l = label.getBoundingClientRect(), k = kind.getBoundingClientRect();
      const drawing = document.querySelector('.ls-rel-links'), p = document.querySelector('.ls-rel-edge-path');
      const start = p.getPointAtLength(0), finish = p.getPointAtLength(p.getTotalLength());
      const matrix = drawing.getScreenCTM(), a = new DOMPoint(start.x, start.y).matrixTransform(matrix), b = new DOMPoint(finish.x, finish.y).matrixTransform(matrix);
      const target = document.querySelector('[data-rel-node="cert"]').getBoundingClientRect();
      return { labelHeight: l.height, requiredLabelHeight: parseFloat(getComputedStyle(label).lineHeight) * 2, labelBottom: l.bottom, kindTop: k.top, kindBottom: k.bottom, nodeBottom: n.bottom, start: { x: a.x, y: a.y }, source: { x: n.right, y: n.y + n.height / 2 }, end: { x: b.x, y: b.y }, target: { x: target.left, y: target.y + target.height / 2 } };
    });
    assert.ok(geometry.labelHeight >= geometry.requiredLabelHeight - .1, 'both label lines must fit without clipping half the second line');
    assert.ok(geometry.labelBottom <= geometry.kindTop && geometry.kindBottom < geometry.nodeBottom, 'name and metadata stay inside the card');
    for (const axis of ['x', 'y']) {
      assert.ok(Math.abs(geometry.start[axis] - geometry.source[axis]) < 1, 'directed path begins on the source card');
      assert.ok(Math.abs(geometry.end[axis] - geometry.target[axis]) < 1, 'directed path ends on the target card');
    }
    await page.locator('[data-rel-node="cert"]').focus();
    assert.equal(await page.locator('[data-rel-node="cert"]').evaluate(e => e.getBoundingClientRect().height), 80, 'keyboard focus preserves card geometry and does not overlap the next row');
    await page.locator('[data-rel-view="matrix"]').click();
    await page.evaluate(() => document.querySelectorAll('table').forEach(table => LeakScopeTables.collapse(table)));
    const matrix = page.locator('.ls-rel-matrix');
    assert.equal(await matrix.locator('tbody tr').count(), 60);
    assert.equal(await matrix.evaluate(e => !!e.closest('details:not([open])')), false, 'workspace must not collapse the matrix visualization');
    assert.equal(await page.locator('.ls-rel-matrix-cell').isVisible(), true);
    assert.equal(await page.locator('.ls-rel-matrix-cell').evaluate(e => e.getBoundingClientRect().height), 32, 'shared table styling preserves matrix geometry');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'matrix scroll stays inside the mobile page');
    const scroll = await page.locator('.ls-rel-matrix-scroll').evaluate(e => ({ client: e.clientWidth, content: e.scrollWidth }));
    assert.ok(scroll.content > scroll.client, 'the 60-column matrix scrolls horizontally');
  } finally { await browser.close(); }
});
