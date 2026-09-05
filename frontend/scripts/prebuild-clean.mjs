#!/usr/bin/env node

/**
 * Pre-build cleanup script
 *
 * Cleans old build artifacts before a new build.
 * Issue #3277: Prevent old version residue from causing 404 errors.
 *
 * This script:
 * 1. Removes old index.html
 * 2. Removes old JS/CSS chunks
 * 3. Preserves .openace-release-assets.json for version detection
 * 4. Preserves .vite/manifest.json until new build completes
 *
 * The vite config has emptyOutDir: false to prevent issues during deployment,
 * so we need to explicitly clean old files before building.
 */

import { existsSync, readdirSync, rmSync, statSync } from 'node:fs';
import { resolve } from 'node:path';

const DIST_DIR = resolve(import.meta.dirname, '..', 'static', 'js', 'dist');
const PRESERVE_FILES = [
  '.openace-release-assets.json', // Version detection file
];
const PRESERVE_DIRS = [
  '.vite', // Vite manifest directory (preserve during build to prevent chunk 404)
];

function isPreserved(name) {
  // Preserve specific files
  if (PRESERVE_FILES.includes(name)) return true;
  // Preserve specific directories
  if (PRESERVE_DIRS.includes(name)) return true;
  return false;
}

function cleanDistDirectory() {
  if (!existsSync(DIST_DIR)) {
    console.log('Dist directory does not exist, nothing to clean');
    return;
  }

  console.log('Cleaning old build artifacts from dist directory...');

  const entries = readdirSync(DIST_DIR);
  let cleanedCount = 0;

  for (const entry of entries) {
    if (isPreserved(entry)) {
      console.log(`  Preserving: ${entry}`);
      continue;
    }

    const entryPath = resolve(DIST_DIR, entry);
    const stat = statSync(entryPath);

    try {
      if (stat.isDirectory()) {
        // Remove entire directory
        rmSync(entryPath, { recursive: true, force: true });
        console.log(`  Removed directory: ${entry}`);
        cleanedCount++;
      } else {
        // Remove file
        rmSync(entryPath, { force: true });
        console.log(`  Removed file: ${entry}`);
        cleanedCount++;
      }
    } catch (error) {
      console.error(`  Failed to remove ${entry}:`, error);
    }
  }

  console.log(`Cleaned ${cleanedCount} items from dist directory`);
}

// Run cleanup
cleanDistDirectory();