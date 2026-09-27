import test from 'node:test';
import assert from 'node:assert/strict';
import { fileBreadcrumbs, fileLocation, fileListingPresentation, fileBrowserError } from '../../frontend/app.js';

const entry = (name, kind = 'file', base = '') => ({ name, kind, path: `${base ? `${base}/` : ''}${name}`, size: kind === 'file' ? 42 : null });

test('breadcrumbs stay relative to the project and preserve literal display names', () => {
  assert.deepEqual(fileBreadcrumbs(''), [{ label: 'Project root', path: '' }]);
  assert.deepEqual(fileBreadcrumbs('src/space folder/λ'), [
    { label: 'Project root', path: '' }, { label: 'src', path: 'src' },
    { label: 'space folder', path: 'src/space folder' }, { label: 'λ', path: 'src/space folder/λ' },
  ]);
  assert.equal(fileLocation('/work/project', 'src/notes.txt'), '/work/project/src/notes.txt');
  assert.equal(fileLocation('/work/project/', 'src'), '/work/project/src');
  assert.equal(fileLocation('/', 'src'), '/src');
  assert.equal(fileLocation('/work/project', ''), '/work/project');
  assert.equal(fileLocation('C:\\work\\project', 'src/notes.txt'), 'C:\\work\\project\\src\\notes.txt');
});

test('relative paths reject traversal, controls, excessive depth and invalid Unicode', () => {
  for (const path of [null, [], '../outside', 'src/../outside', './src', '/etc', 'a//b', 'a/',
    'a\\b', '.hidden', 'a/.hidden', 'line\nname', 'a\u0000b', 'a\u007fb', '\ud800',
    Array(25).fill('a').join('/'), 'a'.repeat(241), 'λ'.repeat(121), Array(10).fill('a'.repeat(110)).join('/')]) {
    assert.throws(() => fileBreadcrumbs(path), /invalid-relative-path/);
    assert.throws(() => fileLocation('/work/project', path), /invalid-relative-path/);
  }
  for (const path of ['', 'relative', 'https://example.invalid/path', '/work/\nname', '/work/\ud800', null]) {
    assert.throws(() => fileLocation(path, 'src'), /invalid-project-location/);
  }
});

test('list presentation sorts directories first, filters names and never mutates source', () => {
  const result = { path: 'src', entries: [entry('zebra.txt', 'file', 'src'), entry('beta', 'directory', 'src'),
    entry('Alpha.txt', 'file', 'src'), entry('alpha', 'directory', 'src')], excluded: 2, truncated: true };
  const before = structuredClone(result);
  const all = fileListingPresentation(result);
  assert.deepEqual(all.entries.map(row => row.name), ['alpha', 'beta', 'Alpha.txt', 'zebra.txt']);
  assert.equal(all.total, 4); assert.equal(all.shown, 4); assert.equal(all.excluded, 2);
  assert.equal(all.emptyMessage, ''); assert.match(all.summary, /2 entries are excluded/);
  assert.match(all.summary, /listing is incomplete/);
  const filtered = fileListingPresentation(result, '  ALPHA ');
  assert.deepEqual(filtered.entries.map(row => row.name), ['alpha', 'Alpha.txt']);
  assert.match(filtered.summary, /2 of 4 listed entries match/);
  assert.equal(filtered.entries[0] === result.entries[3], false);
  assert.deepEqual(result, before);
});

test('empty and filtered listings do not claim the physical folder is empty', () => {
  const empty = fileListingPresentation({ path: '', entries: [], excluded: 3 });
  assert.match(empty.emptyMessage, /excluded items may still exist/);
  assert.match(empty.summary, /3 entries are excluded/);
  const unknown = fileListingPresentation({ path: '', entries: [] });
  assert.equal(unknown.excluded, null); assert.equal(unknown.truncated, false);
  assert.match(unknown.emptyMessage, /Hidden and excluded/);
  const filtered = fileListingPresentation({ path: '', entries: [entry('notes')] }, 'absent');
  assert.match(filtered.emptyMessage, /Clear the filter/);
  assert.match(filtered.summary, /0 of 1/);
});

test('malformed, foreign-folder or duplicate listing entries reject the entire response', () => {
  const good = { path: 'src', entries: [entry('notes', 'file', 'src')] };
  const invalid = [null, [], {}, { ...good, path: '../elsewhere' }, { ...good, entries: null },
    { ...good, entries: Array(2001).fill(entry('notes', 'file', 'src')) },
    ...[-1, '2', Infinity, Number.MAX_SAFE_INTEGER + 1].map(excluded => ({ ...good, excluded })),
    { ...good, truncated: 'yes' }, { ...good, entries: [entry('notes', 'file', 'elsewhere')] },
    { ...good, entries: [entry('notes', 'file', 'src'), entry('notes', 'file', 'src')] },
    ...[null, {}, { name: '../notes', path: 'src/../notes', kind: 'file' },
      { name: 'notes', path: 'src/notes/more', kind: 'file' },
      { name: 'notes', path: 'src/notes', kind: 'symlink' },
      { ...entry('notes', 'file', 'src'), size: -1 },
      { ...entry('notes', 'file', 'src'), size: '42' }].map(value => ({ ...good, entries: [value] })),
  ];
  for (const result of invalid) assert.throws(() => fileListingPresentation(result), /unsupported-file-listing/);
  assert.throws(() => fileListingPresentation(good, null), /unsupported-file-listing/);
  assert.throws(() => fileListingPresentation(good, 'x'.repeat(1025)), /unsupported-file-listing/);
});

test('listing upper bound and literal markup names remain inert presentation data', () => {
  const result = fileListingPresentation({ path: '', entries: Array.from({ length: 2000 }, (_, i) => entry(`file-${i}`)) });
  assert.equal(result.entries.length, 2000);
  const markup = fileListingPresentation({ path: '', entries: [entry('<script>literal')] });
  assert.equal(markup.entries[0].name, '<script>literal');
  assert.equal(fileBreadcrumbs('<script>literal')[1].label, '<script>literal');
});

test('unsupported filesystem names do not prevent browsing other files', () => {
  const oddNames = ['x'.repeat(241), 'sub\\name', 'line\nbreak', '.hidden', '字'.repeat(81)];
  const result = fileListingPresentation({ path: '', entries: [entry('notes'), ...oddNames.map(name => entry(name))] });
  assert.deepEqual(result.entries.map(item => item.name), ['notes']);
  assert.equal(result.unsupported, oddNames.length);
  assert.match(result.summary, /names or paths this browser cannot open/);
  assert.equal(result.excluded, null);
  assert.equal(result.truncated, false);
  const duplicate = entry('x'.repeat(241));
  assert.throws(() => fileListingPresentation({ path: '', entries: [duplicate, duplicate] }), /unsupported-file-listing/);
  assert.throws(() => fileListingPresentation({ path: '', entries: [{ ...duplicate, path: 'elsewhere' }] }), /unsupported-file-listing/);
});

test('file errors explain preview, identity, connection and excluded-file limits', () => {
  assert.match(fileBrowserError(new Error('file-access-excluded')), /Hidden files/);
  assert.match(fileBrowserError('host-file-access-excluded'), /Project config/);
  for (const code of ['text-size-limit', 'utf8-text-required', 'Text preview bound exceeded']) {
    assert.match(fileBrowserError(new Error(code)), /64 KiB/);
  }
  assert.match(fileBrowserError('project-identity-changed'), /moved, been replaced/);
  assert.match(fileBrowserError('root-identity-changed'), /Project details/);
  assert.match(fileBrowserError('remote-connection-unavailable'), /Settings → Machines/);
  assert.match(fileBrowserError(Object.assign(new Error(), { name: 'AbortError' })), /connection is unavailable/);
  assert.match(fileBrowserError(new TypeError('Failed to fetch')), /connection is unavailable/);
  assert.match(fileBrowserError('regular-unlinked-file-required'), /filesystem link/);
  assert.match(fileBrowserError('combined-project-operation-unavailable'), /not currently available/);
  assert.match(fileBrowserError('unsupported-file-listing'), /No file from this response was opened/);
  assert.match(fileBrowserError('unsupported-file-preview'), /No file from this response was opened/);
  assert.match(fileBrowserError('host-project-unregistered'), /Refresh the project list/);
});

test('remote failures remain uncertain, explain large-folder limit and expose no raw diagnostics', () => {
  assert.match(fileBrowserError('Directory entry bound exceeded'), /cannot page/);
  assert.match(fileBrowserError('remote-files-unavailable'), /could also|can also/);
  assert.match(fileBrowserError('cluster-files-unavailable-inspect-private-receipt'), /500 scanned entries/);
  assert.match(fileBrowserError('remote-file-unavailable'), /diagnostic receipt/);
  assert.match(fileBrowserError('remote-helper-changed-restart-required'), /deliberately restart/);
  const raw = '<script>untrusted diagnostic</script> /private/location';
  for (const value of [new Error(raw), raw, undefined, {}]) {
    const message = fileBrowserError(value);
    assert.match(message, /diagnostic receipt/);
    assert.equal(message.includes(raw), false);
  }
});
