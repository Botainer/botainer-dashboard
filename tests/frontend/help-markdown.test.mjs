import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { renderHelpMarkdown } from '../../frontend/connections.js';

// Structural/event checks against the production renderer. This deliberately
// does not emulate HTML parsing, CSS layout, a browser, or network navigation.
class Element {
  constructor(document, tagName, text = '') {
    this.ownerDocument = document;
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this._text = text;
    this.attributes = new Map();
    this.listeners = new Map();
    this.className = '';
    this.hidden = false;
    this.scrolls = [];
  }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { assert.fail('Help rendering must not parse HTML.'); }
  set outerHTML(_) { assert.fail('Help rendering must not parse HTML.'); }
  append(...children) {
    for (let child of children) {
      if (typeof child === 'string') child = this.ownerDocument.createTextNode(child);
      if (child.parentNode) child.parentNode.children.splice(child.parentNode.children.indexOf(child), 1);
      child.parentNode = this;
      this.children.push(child);
    }
  }
  appendChild(child) { this.append(child); return child; }
  replaceChildren(...children) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this.append(...children);
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  addEventListener(name, handler) {
    const handlers = this.listeners.get(name) || [];
    handlers.push(handler);
    this.listeners.set(name, handlers);
  }
  focus(options) { this.ownerDocument.activeElement = this; this.focusOptions = options; }
  scrollIntoView(options) { this.scrolls.push(options); }
}

function makeDocument() {
  return {
    activeElement: null,
    createElement(tag) { return new Element(this, tag); },
    createTextNode(text) { return new Element(this, '#text', String(text)); },
  };
}
const descendants = node => [node, ...node.children.flatMap(descendants)];
const tags = (node, tag) => descendants(node).filter(item => item.tagName === tag.toUpperCase());
const attribute = (node, name) => node.getAttribute(name) ?? node[name];
const heading = (node, text) => descendants(node).find(item => /^H[1-6]$/.test(item.tagName) && item.textContent === text);
const childOf = (node, parent) => node === parent || Boolean(node.parentNode && childOf(node.parentNode, parent));
function render(text, options = {}) {
  const document = makeDocument();
  return { document, root: renderHelpMarkdown(text, { document, ...options }) };
}
async function click(node) {
  assert.ok(node, 'Expected a clickable document control.');
  let prevented = false;
  for (const handler of node.listeners.get('click') || []) {
    await handler({ preventDefault() { prevented = true; } });
  }
  return prevented;
}

test('help renders readable headings, paragraphs, emphasis and literal inline code', () => {
  const { root } = render('# Guide\n\nA **clear** first line\ncontinues with *emphasis* and `--check`.\n\n## Next step\n\nAnother paragraph.');
  assert.equal(root.tagName, 'DIV');
  assert.ok(root.className.split(/\s+/).includes('help-document'));
  assert.ok(heading(root, 'Guide'));
  assert.ok(heading(root, 'Next step'));
  assert.equal(tags(root, 'P').length, 2);
  assert.equal(tags(root, 'STRONG')[0].textContent, 'clear');
  assert.equal(tags(root, 'EM')[0].textContent, 'emphasis');
  assert.equal(tags(root, 'CODE')[0].textContent, '--check');
  assert.match(tags(root, 'P')[0].textContent, /first line\s+continues/);
});

test('view can omit only the document first heading without losing later headings', () => {
  const { root } = render('# Guide\n\nIntroduction.\n\n## Continue\n\nDetails.', { omitFirstHeading: true });
  assert.equal(heading(root, 'Guide'), undefined);
  assert.ok(heading(root, 'Continue'));
  assert.match(root.textContent, /Introduction/);
});

test('fenced shell text preserves line breaks, whitespace and literal markup', () => {
  const commands = 'ssh example \\\n    -o BatchMode=yes true\n<script>alert("inert")</script>\n**not emphasis**';
  const { root } = render('```sh\n' + commands + '\n```');
  const pre = tags(root, 'PRE');
  assert.equal(pre.length, 1);
  assert.equal(pre[0].textContent.replace(/\n$/, ''), commands);
  assert.equal(tags(pre[0], 'CODE').length, 1);
  assert.equal(tags(pre[0], 'STRONG').length, 0);
  assert.equal(tags(root, 'SCRIPT').length, 0);
});

test('list continuations and fenced commands stay in their item, followed by step four', () => {
  const { root } = render([
    '1. Restore the connection.',
    '2. Read the prompt.',
    '3. Run checks from the project:',
    '',
    '   ```sh',
    '   botainer status',
    '   botainer doctor',
    '   ```',
    '',
    '   `status` reports the sessions.',
    '   Follow the relevant finding.',
    '4. Reconnect to the original owner.',
  ].join('\n'));
  const ordered = tags(root, 'OL');
  assert.equal(ordered.length, 1);
  const items = ordered[0].children.filter(child => child.tagName === 'LI');
  assert.equal(items.length, 4);
  assert.match(items[2].textContent, /status reports the sessions\.\s+Follow the relevant finding/);
  assert.equal(tags(items[2], 'PRE').length, 1);
  assert.equal(tags(items[2], 'PRE')[0].textContent.replace(/\n$/, ''), 'botainer status\nbotainer doctor');
  assert.match(items[3].textContent, /Reconnect to the original owner/);
  assert.equal(tags(items[3], 'PRE').length, 0);
});

test('nested lists remain associated with their parent item', () => {
  const { root } = render('1. Pick a route:\n   - Local\n   - Remote\n2. Continue.');
  const ordered = tags(root, 'OL')[0];
  assert.ok(ordered);
  const items = ordered.children.filter(child => child.tagName === 'LI');
  assert.equal(items.length, 2);
  const nested = tags(items[0], 'UL');
  assert.equal(nested.length, 1);
  assert.deepEqual(nested[0].children.filter(child => child.tagName === 'LI').map(item => item.textContent), ['Local', 'Remote']);
});

test('pipe tables have semantic headers and preserve escaped pipes and inline-code pipes', () => {
  const { root } = render('| Route | Instructions |\n| --- | --- |\n| local \\| remote | Use `left|right` and **review**. |\n| Host | Account access |');
  const tables = tags(root, 'TABLE');
  assert.equal(tables.length, 1);
  assert.equal(tags(tables[0], 'THEAD').length, 1);
  assert.equal(tags(tables[0], 'TBODY').length, 1);
  assert.deepEqual(tags(tables[0], 'TH').map(item => item.textContent), ['Route', 'Instructions']);
  assert.ok(tags(tables[0], 'TH').every(item => attribute(item, 'scope') === 'col'));
  assert.equal(tags(tables[0], 'TR').length, 3);
  const cells = tags(tables[0], 'TD');
  assert.equal(cells.length, 4);
  assert.equal(cells[0].textContent, 'local | remote');
  assert.equal(tags(cells[1], 'CODE')[0].textContent, 'left|right');
  assert.equal(tags(cells[1], 'STRONG')[0].textContent, 'review');
  assert.notEqual(tables[0].parentNode, root, 'Table needs its own bounded scroll wrapper.');
});

test('external HTTPS references are explicit links with isolated new-tab navigation', () => {
  const url = 'https://docs.example.invalid/reference#topic';
  const { root } = render(`[Reference](${url})`);
  const anchors = tags(root, 'A');
  assert.equal(anchors.length, 1);
  assert.equal(attribute(anchors[0], 'href'), url);
  assert.equal(attribute(anchors[0], 'target'), '_blank');
  const relationship = String(attribute(anchors[0], 'rel')).split(/\s+/);
  assert.ok(relationship.includes('noopener'));
  assert.ok(relationship.includes('noreferrer'));
});

test('existing guide references navigate through the help controller without opening application URLs', async () => {
  for (const [file, guideId, anchor] of [
    ['ssh-setup.md', 'ssh', 'first-login-then-dashboard-test'],
    ['installation.md', 'installation', ''],
    ['start-dashboard.md', 'start', 'pair-the-browser'],
    ['sessions.md', 'sessions', 'leaving-versus-stopping'],
    ['configuration.md', 'configuration', 'project-config-and-save-state'],
    ['maintenance.md', 'maintenance', 'a-session-will-not-start-or-connect'],
    ['host-agents.md', 'host', 'register-an-existing-host-folder'],
  ]) {
    const navigations = [];
    const { root } = render(`[Guide instructions](${file}${anchor ? '#' + anchor : ''})`, {
      onNavigate: location => navigations.push(location),
    });
    assert.equal(tags(root, 'A').length, 0);
    const control = tags(root, 'BUTTON').find(item => /Guide instructions/.test(item.textContent));
    await click(control);
    assert.deepEqual(navigations, [{ guideId, anchor }]);
  }
});

test('same-guide links focus and scroll to the matching heading in the current article', async () => {
  const navigations = [];
  const { root, document } = render('[Jump to checks](#first-login-then-dashboard-test)\n\n## First login, then dashboard test\n\nCheck connection.', {
    guideId: 'ssh', onNavigate: location => navigations.push(location),
  });
  const destination = heading(root, 'First login, then dashboard test');
  const control = tags(root, 'BUTTON').find(item => /Jump to checks/.test(item.textContent));
  assert.ok(destination);
  await click(control);
  assert.equal(document.activeElement, destination);
  assert.ok(destination.scrolls.length > 0);
  assert.deepEqual(navigations, []);
});

test('unsupported source references retain the document path without a dead application link', () => {
  const { root } = render('[Design](architecture.md) and [File plan](research/file-browsing-options.md).');
  assert.equal(tags(root, 'A').length, 0);
  assert.equal(tags(root, 'BUTTON').length, 0);
  assert.match(root.textContent, /Design/);
  assert.match(root.textContent, /architecture\.md/);
  assert.match(root.textContent, /research\/file-browsing-options\.md/);
});

test('raw HTML, image markup and hostile destinations never create active content', () => {
  const hostile = [
    '<img src="https://images.example.invalid/tracker" onerror="alert(1)">',
    '<script>alert(1)</script>',
    '<iframe src="https://frames.example.invalid/"></iframe>',
    '![remote image](https://images.example.invalid/tracker)',
    '[script](javascript:alert)',
    '[mixed script](JaVaScRiPt:alert)',
    '[data](data:text/html,malicious)',
    '[file](file:///private/example)',
    '[protocol relative](//example.invalid/track)',
    '[application endpoint](/api/connections)',
    '[encoded scheme](javascript%3Aalert)',
  ].join('\n\n');
  const { root } = render(hostile);
  for (const tag of ['IMG', 'SCRIPT', 'IFRAME', 'OBJECT', 'EMBED', 'SVG', 'A', 'BUTTON']) {
    assert.equal(tags(root, tag).length, 0, `Unexpected active element: ${tag}`);
  }
  assert.match(root.textContent, /<script>alert\(1\)<\/script>/);
  assert.match(root.textContent, /remote image/);
  assert.ok(descendants(root).every(item => [...item.attributes.keys()].every(name => !name.startsWith('on'))));
});

test('unclosed code fences keep following link-like syntax literal', () => {
  const { root } = render('```text\n[link](https://docs.example.invalid/)\n# literal heading');
  assert.equal(tags(root, 'PRE').length, 1);
  assert.equal(tags(root, 'A').length, 0);
  assert.equal(heading(root, 'literal heading'), undefined);
});

test('renderer bounds input by UTF-8 bytes before building the document', () => {
  for (const text of ['a'.repeat(256 * 1024 + 1), 'é'.repeat(128 * 1024 + 1)]) {
    assert.throws(() => render(text), /(?:256|large|limit|bound|size)/i);
  }
});

test('the shipped help guides render their actual tables, code and numbered procedures', () => {
  let totalTables = 0;
  for (const [id, filename, expectedTables] of [
    ['getting-started', 'getting-started.md', 1],
    ['installation', 'installation.md', 3],
    ['start', 'start-dashboard.md', 0],
    ['ssh', 'ssh-setup.md', 3],
    ['sessions', 'sessions.md', 2],
    ['configuration', 'configuration.md', 1],
    ['maintenance', 'maintenance.md', 0],
    ['host', 'host-agents.md', 0],
    ['agent', 'agent-setup-guide.md', 0],
    ['support', 'setup-support-matrix.md', 1],
    ['status', 'status.md', 0],
    ['alpha-testing', 'alpha-testing.md', 0],
  ]) {
    const source = readFileSync(new URL(`../../docs/${filename}`, import.meta.url), 'utf8');
    const { root } = render(source, { guideId: id });
    assert.equal(tags(root, 'TABLE').length, expectedTables, filename);
    totalTables += tags(root, 'TABLE').length;
    assert.ok(descendants(root).some(item => /^H[1-6]$/.test(item.tagName)), filename);
    assert.equal(tags(root, 'SCRIPT').length, 0);
    for (const table of tags(root, 'TABLE')) {
      assert.ok(tags(table, 'TH').length >= 2, filename);
      assert.ok(tags(table, 'TD').length > 0, filename);
    }
    if (id === 'maintenance') {
      const checks = tags(root, 'PRE').find(item => /botainer status\nbotainer doctor/.test(item.textContent));
      assert.ok(checks, 'Botainer maintenance needs its two-command check block.');
      const owner = tags(root, 'LI').find(item => childOf(checks, item));
      assert.ok(owner, 'Check commands remain part of their ordered procedure.');
      assert.match(owner.textContent, /Follow the finding/);
      assert.equal(owner.parentNode.tagName, 'OL');
      assert.equal(owner.parentNode.children.filter(item => item.tagName === 'LI').length, 4);
    }
  }
  assert.equal(totalTables, 11);
});
