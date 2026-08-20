#!/usr/bin/env node

import { createHash } from 'node:crypto';
import {
  existsSync,
  lstatSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { dirname, isAbsolute, join, posix, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

export const SCHEMA_VERSION = 1;
export const STATE_FILENAME = '.openace-release-assets.json';
export const VITE_MANIFEST = '.vite/manifest.json';
export const MAX_ASSETS = 10_000;
export const MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024;

const HASHED_ASSET_RE = /(?:^|\/)[^/]+\.[A-Za-z0-9_-]{8}\.[^/]+$/;

function fail(message) {
  throw new Error(`frontend asset retention: ${message}`);
}

export function normalizeAssetPath(value) {
  if (typeof value !== 'string' || value.length === 0 || value.includes('\0')) {
    fail('asset path must be a non-empty string');
  }
  if (value.includes('\\') || isAbsolute(value)) fail(`unsafe asset path: ${value}`);
  const normalized = posix.normalize(value);
  if (normalized !== value || normalized === '..' || normalized.startsWith('../')) {
    fail(`unsafe asset path: ${value}`);
  }
  return normalized;
}

export function isStrictHashedAsset(value) {
  return HASHED_ASSET_RE.test(value);
}

function assetAbsolutePath(distDir, asset) {
  const distRoot = realpathSync(resolve(distDir));
  const absolute = resolve(distRoot, ...normalizeAssetPath(asset).split('/'));
  if (absolute !== distRoot && !absolute.startsWith(`${distRoot}${sep}`)) {
    fail(`asset escapes dist directory: ${asset}`);
  }
  let parent;
  try {
    parent = realpathSync(dirname(absolute));
  } catch {
    fail(`missing asset parent: ${asset}`);
  }
  if (parent !== distRoot && !parent.startsWith(`${distRoot}${sep}`)) {
    fail(`asset escapes dist directory through a symlink: ${asset}`);
  }
  return absolute;
}

function validateRegularAssets(distDir, assets) {
  if (!Array.isArray(assets) || assets.length > MAX_ASSETS) fail('asset count exceeds limit');
  let totalBytes = 0;
  for (const asset of assets) {
    if (!isStrictHashedAsset(normalizeAssetPath(asset))) fail(`not a hashed release asset: ${asset}`);
    const absolute = assetAbsolutePath(distDir, asset);
    if (!existsSync(absolute)) fail(`missing asset: ${asset}`);
    const info = lstatSync(absolute);
    if (info.isSymbolicLink() || !info.isFile()) fail(`asset is not a regular file: ${asset}`);
    totalBytes += info.size;
    if (totalBytes > MAX_TOTAL_BYTES) fail('asset bytes exceed limit');
  }
}

function uniqueSorted(values) {
  return [...new Set(values)].sort();
}

export function deriveCurrentRelease(distDir) {
  const manifestPath = join(resolve(distDir), VITE_MANIFEST);
  if (!existsSync(manifestPath)) fail(`missing Vite manifest: ${VITE_MANIFEST}`);
  const raw = readFileSync(manifestPath);
  let manifest;
  try {
    manifest = JSON.parse(raw.toString('utf8'));
  } catch {
    fail('invalid Vite manifest JSON');
  }
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
    fail('invalid Vite manifest schema');
  }

  const keys = new Set(Object.keys(manifest));
  const assets = [];
  for (const [key, entry] of Object.entries(manifest)) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) fail(`invalid manifest entry: ${key}`);
    for (const dependencyField of ['imports', 'dynamicImports']) {
      const dependencies = entry[dependencyField] ?? [];
      if (!Array.isArray(dependencies)) fail(`invalid ${dependencyField} for ${key}`);
      for (const dependency of dependencies) {
        if (typeof dependency !== 'string' || !keys.has(dependency)) {
          fail(`dangling ${dependencyField} key for ${key}: ${String(dependency)}`);
        }
      }
    }
    if (typeof entry.file !== 'string') fail(`missing file for manifest entry: ${key}`);
    assets.push(normalizeAssetPath(entry.file));
    for (const assetField of ['css', 'assets']) {
      const values = entry[assetField] ?? [];
      if (!Array.isArray(values)) fail(`invalid ${assetField} for ${key}`);
      for (const value of values) assets.push(normalizeAssetPath(value));
    }
  }

  const currentAssets = uniqueSorted(assets);
  validateRegularAssets(distDir, currentAssets);
  return {
    build_id: createHash('sha256').update(raw).digest('hex'),
    assets: currentAssets,
  };
}

function validateGeneration(distDir, value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`invalid ${label} generation`);
  if (typeof value.build_id !== 'string' || !/^[a-f0-9]{64}$/.test(value.build_id)) {
    fail(`invalid ${label} build id`);
  }
  if (!Array.isArray(value.assets) || value.assets.some((asset) => typeof asset !== 'string')) {
    fail(`invalid ${label} assets`);
  }
  const assets = uniqueSorted(value.assets);
  if (assets.length !== value.assets.length || assets.some((asset, index) => asset !== value.assets[index])) {
    fail(`duplicate or unsorted ${label} assets`);
  }
  validateRegularAssets(distDir, assets);
  return { build_id: value.build_id, assets };
}

export function readState(distDir) {
  const statePath = join(resolve(distDir), STATE_FILENAME);
  if (!existsSync(statePath)) return null;
  let state;
  try {
    state = JSON.parse(readFileSync(statePath, 'utf8'));
  } catch {
    fail('invalid release state JSON');
  }
  if (!state || state.schema_version !== SCHEMA_VERSION) fail('unsupported release state schema');
  return {
    schema_version: SCHEMA_VERSION,
    current: validateGeneration(distDir, state.current, 'current'),
    previous: state.previous == null ? null : validateGeneration(distDir, state.previous, 'previous'),
  };
}

function walkHashedAssets(distDir, directory = resolve(distDir)) {
  const found = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const absolute = join(directory, entry.name);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) {
      found.push(...walkHashedAssets(distDir, absolute));
    } else if (entry.isFile()) {
      const asset = relative(resolve(distDir), absolute).split(sep).join('/');
      if (isStrictHashedAsset(asset)) found.push(asset);
    }
  }
  return uniqueSorted(found);
}

function legacyPrevious(distDir, current) {
  const assets = walkHashedAssets(distDir).filter((asset) => !current.assets.includes(asset));
  if (assets.length === 0) return null;
  validateRegularAssets(distDir, assets);
  return {
    build_id: createHash('sha256').update(assets.join('\n')).digest('hex'),
    assets,
  };
}

function atomicWriteState(distDir, state) {
  const statePath = join(resolve(distDir), STATE_FILENAME);
  const tempPath = `${statePath}.tmp-${process.pid}`;
  writeFileSync(tempPath, `${JSON.stringify(state, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
  renameSync(tempPath, statePath);
}

export function retainReleaseAssets(distDir) {
  const current = deriveCurrentRelease(distDir);
  const oldState = readState(distDir);
  const repeatedCurrent =
    oldState?.current.build_id === current.build_id &&
    oldState.current.assets.length === current.assets.length &&
    oldState.current.assets.every((asset, index) => asset === current.assets[index]);
  const previousCandidate = repeatedCurrent
    ? oldState.previous
    : (oldState?.current ?? legacyPrevious(distDir, current));
  const previousAssets = (previousCandidate?.assets ?? []).filter(
    (asset) => !current.assets.includes(asset)
  );
  const previous =
    previousCandidate && previousAssets.length > 0
      ? { build_id: previousCandidate.build_id, assets: previousAssets }
      : null;
  const keep = new Set([...current.assets, ...(previous?.assets ?? [])]);

  for (const asset of walkHashedAssets(distDir)) {
    if (!keep.has(asset)) rmSync(assetAbsolutePath(distDir, asset));
  }

  const state = { schema_version: SCHEMA_VERSION, current, previous };
  atomicWriteState(distDir, state);
  return state;
}

function main() {
  const args = process.argv.slice(2);
  const distIndex = args.indexOf('--dist');
  if (distIndex < 0 || !args[distIndex + 1]) fail('usage: retain-release-assets.mjs --dist <path>');
  retainReleaseAssets(args[distIndex + 1]);
}

if (resolve(process.argv[1] ?? '') === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
