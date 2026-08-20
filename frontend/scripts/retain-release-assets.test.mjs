import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { STATE_FILENAME, readState, retainReleaseAssets } from './retain-release-assets.mjs';

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'openace-assets-node-'));
  mkdirSync(join(root, '.vite'), { recursive: true });
  return root;
}

function writeRelease(root, name, hash, extra = {}) {
  const file = `${name}.${hash}.js`;
  writeFileSync(join(root, file), `// ${file}`);
  const manifest = { [`src/${name}.tsx`]: { file, isEntry: true, ...extra } };
  writeFileSync(join(root, '.vite', 'manifest.json'), JSON.stringify(manifest));
  return file;
}

test('retains current and one previous release across A -> B -> C', () => {
  const root = fixture();
  try {
    const a = writeRelease(root, 'AutonomousDev', 'aaaaaaaa');
    retainReleaseAssets(root);
    const b = writeRelease(root, 'AutonomousDev', 'bbbbbbbb');
    retainReleaseAssets(root);
    assert.equal(readFileSync(join(root, a), 'utf8'), `// ${a}`);
    const c = writeRelease(root, 'AutonomousDev', 'cccccccc');
    const state = retainReleaseAssets(root);
    assert.throws(() => readFileSync(join(root, a)), /ENOENT/);
    assert.equal(readFileSync(join(root, b), 'utf8'), `// ${b}`);
    assert.equal(readFileSync(join(root, c), 'utf8'), `// ${c}`);
    assert.deepEqual(state.current.assets, [c]);
    assert.deepEqual(state.previous.assets, [b]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('deduplicates content hashes shared by consecutive releases', () => {
  const root = fixture();
  try {
    const shared = 'react-vendor.11111111.js';
    const a = 'AutonomousDev.aaaaaaaa.js';
    const b = 'AutonomousDev.bbbbbbbb.js';
    for (const asset of [shared, a]) writeFileSync(join(root, asset), asset);
    writeFileSync(
      join(root, '.vite', 'manifest.json'),
      JSON.stringify({ 'src/main.tsx': { file: shared, assets: [a], isEntry: true } })
    );
    retainReleaseAssets(root);

    writeFileSync(join(root, b), b);
    writeFileSync(
      join(root, '.vite', 'manifest.json'),
      JSON.stringify({ 'src/main.tsx': { file: shared, assets: [b], isEntry: true } })
    );
    const state = retainReleaseAssets(root);

    assert.deepEqual(state.current.assets, [b, shared]);
    assert.deepEqual(state.previous.assets, [a]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('does not rotate away the previous release when the current build repeats', () => {
  const root = fixture();
  try {
    const a = writeRelease(root, 'AutonomousDev', 'aaaaaaaa');
    retainReleaseAssets(root);
    const b = writeRelease(root, 'AutonomousDev', 'bbbbbbbb');
    retainReleaseAssets(root);

    const state = retainReleaseAssets(root);

    assert.deepEqual(state.current.assets, [b]);
    assert.deepEqual(state.previous.assets, [a]);
    assert.equal(readFileSync(join(root, a), 'utf8'), `// ${a}`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('keeps public files and never follows or removes symlinks', (t) => {
  const root = fixture();
  try {
    writeFileSync(join(root, 'manifest.json'), '{"public":true}');
    const current = writeRelease(root, 'index', 'dddddddd');
    const outside = join(root, '..', `outside-${process.pid}.eeeeeeee.js`);
    writeFileSync(outside, 'outside');
    try {
      symlinkSync(outside, join(root, 'linked.ffffffff.js'));
    } catch (error) {
      if (error?.code !== 'EPERM') throw error;
      rmSync(outside, { force: true });
      t.skip('symlinks are unavailable on this platform');
      return;
    }
    retainReleaseAssets(root);
    assert.equal(readFileSync(join(root, 'manifest.json'), 'utf8'), '{"public":true}');
    assert.equal(readFileSync(join(root, current), 'utf8'), `// ${current}`);
    assert.equal(readFileSync(outside, 'utf8'), 'outside');
    rmSync(outside, { force: true });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('fails closed for dangling manifest imports and missing assets', () => {
  const root = fixture();
  try {
    writeFileSync(
      join(root, '.vite', 'manifest.json'),
      JSON.stringify({ 'src/main.tsx': { file: 'index.12345678.js', dynamicImports: ['missing'] } })
    );
    assert.throws(() => retainReleaseAssets(root), /dangling dynamicImports/);
    assert.equal(readFileSync(join(root, '.vite', 'manifest.json'), 'utf8').length > 0, true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('rejects manifest assets that traverse a symlinked directory', (t) => {
  const root = fixture();
  const outside = mkdtempSync(join(tmpdir(), 'openace-assets-outside-'));
  try {
    writeFileSync(join(outside, 'escaped.12345678.js'), 'outside');
    try {
      symlinkSync(outside, join(root, 'linked'), 'dir');
    } catch (error) {
      if (error?.code !== 'EPERM') throw error;
      t.skip('directory symlinks are unavailable on this platform');
      return;
    }
    writeFileSync(
      join(root, '.vite', 'manifest.json'),
      JSON.stringify({ 'src/main.tsx': { file: 'linked/escaped.12345678.js', isEntry: true } })
    );
    assert.throws(() => retainReleaseAssets(root), /escapes dist directory through a symlink/);
    assert.equal(readFileSync(join(outside, 'escaped.12345678.js'), 'utf8'), 'outside');
  } finally {
    rmSync(root, { recursive: true, force: true });
    rmSync(outside, { recursive: true, force: true });
  }
});

test('writes a versioned state atomically after validation', () => {
  const root = fixture();
  try {
    const current = writeRelease(root, 'index', '12345678');
    retainReleaseAssets(root);
    const state = JSON.parse(readFileSync(join(root, STATE_FILENAME), 'utf8'));
    assert.equal(state.schema_version, 1);
    assert.deepEqual(state.current.assets, [current]);
    assert.equal(state.previous, null);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('accepts the shared cross-runtime state fixture', () => {
  const root = fixture();
  try {
    const fixturePath = join(
      dirname(fileURLToPath(import.meta.url)),
      '..',
      '..',
      'tests',
      'fixtures',
      'frontend_asset_state_v1.json'
    );
    const state = JSON.parse(readFileSync(fixturePath, 'utf8'));
    for (const generation of [state.current, state.previous]) {
      for (const asset of generation.assets) writeFileSync(join(root, asset), asset);
    }
    writeFileSync(join(root, STATE_FILENAME), JSON.stringify(state));
    assert.deepEqual(readState(root), state);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
