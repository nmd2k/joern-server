import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { describe, it, beforeEach, afterEach } from 'node:test';
import {
  collectCpgSamples,
  filterAndPaginateSamples,
  isSha256Hex,
  lookupArchiveEntry,
  repairSidMapFromArchive,
  saveSidMap,
  resolveCpgPaths,
  scanArchiveEntries,
  buildArchiveIndex,
} from '../cpg-lookup.js';

const HASH_A = 'a'.repeat(64);
const HASH_B = 'b'.repeat(64);

describe('cpg-lookup', () => {
  let tmp;

  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cpg-lookup-'));
  });

  afterEach(() => {
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it('isSha256Hex accepts 64-char hex', () => {
    assert.equal(isSha256Hex(HASH_A), true);
    assert.equal(isSha256Hex('not-a-hash'), false);
  });

  it('lookupArchiveEntry finds directory archives with meta', () => {
    const archiveDir = path.join(tmp, 'archive');
    const entryDir = path.join(archiveDir, HASH_A);
    fs.mkdirSync(entryDir, { recursive: true });
    fs.writeFileSync(
      path.join(entryDir, '.meta.json'),
      JSON.stringify({ sample_id: 'repo__vuln', archived_at: '2026-01-01' }),
    );
    const entry = lookupArchiveEntry(archiveDir, HASH_A);
    assert.ok(entry);
    assert.equal(entry.sample_id, 'repo__vuln');
  });

  it('lookupArchiveEntry ignores directory without meta', () => {
    const archiveDir = path.join(tmp, 'archive');
    fs.mkdirSync(path.join(archiveDir, HASH_A), { recursive: true });
    assert.equal(lookupArchiveEntry(archiveDir, HASH_A), null);
  });

  it('scanArchiveEntries discovers flat-file legacy hash archives', () => {
    const archiveDir = path.join(tmp, 'archive');
    fs.mkdirSync(archiveDir, { recursive: true });
    fs.writeFileSync(path.join(archiveDir, HASH_B), 'fake-cpg-bytes');
    const entries = scanArchiveEntries(archiveDir);
    assert.equal(entries.length, 1);
    assert.equal(entries[0].source_hash, HASH_B);
  });

  it('collectCpgSamples marks archived prebuilt CPGs without cpg/out', () => {
    const archiveDir = path.join(tmp, 'archive');
    const outDir = path.join(tmp, 'out');
    const sidMapPath = path.join(archiveDir, '.sid-map.json');
    fs.mkdirSync(outDir, { recursive: true });
    const entryDir = path.join(archiveDir, HASH_A);
    fs.mkdirSync(entryDir, { recursive: true });
    fs.writeFileSync(
      path.join(entryDir, '.meta.json'),
      JSON.stringify({ sample_id: 'myrepo__fixed' }),
    );

    const { samples } = collectCpgSamples({
      cpgOutDir: outDir,
      cpgArchiveDir: archiveDir,
      sidMapPath,
      repairSidMap: false,
    });

    const row = samples.find((s) => s.sample_id === 'myrepo__fixed');
    assert.ok(row);
    assert.equal(row.in_out, false);
    assert.equal(row.archived, true);
    assert.equal(row.source_hash, HASH_A);

    const repaired = collectCpgSamples({
      cpgOutDir: outDir,
      cpgArchiveDir: archiveDir,
      sidMapPath,
      repairSidMap: true,
    });
    assert.ok(repaired.samples.find((s) => s.sample_id === 'myrepo__fixed'));
    const sidMap = JSON.parse(fs.readFileSync(sidMapPath, 'utf8'));
    assert.equal(sidMap['myrepo__fixed'], HASH_A);
  });

  it('resolveCpgPaths uses CPG_BASE_DIR when archive has entries', () => {
    const base = path.join(tmp, 'my-cpg');
    const archiveDir = path.join(base, 'archive');
    fs.mkdirSync(archiveDir, { recursive: true });
    fs.writeFileSync(path.join(archiveDir, HASH_A), 'cpg');
    const prev = process.env.CPG_BASE_DIR;
    process.env.CPG_BASE_DIR = base;
    try {
      const resolved = resolveCpgPaths({ repoRoot: path.join(tmp, 'nonexistent-repo') });
      assert.equal(resolved.cpgBaseDir, base);
      assert.equal(resolved.cpgArchiveDir, archiveDir);
    } finally {
      if (prev === undefined) delete process.env.CPG_BASE_DIR;
      else process.env.CPG_BASE_DIR = prev;
    }
  });

  it('repairSidMapFromArchive is a no-op when sid-map already complete', () => {
    const archiveDir = path.join(tmp, 'archive');
    const sidMapPath = path.join(archiveDir, '.sid-map.json');
    fs.mkdirSync(path.join(archiveDir, HASH_A), { recursive: true });
    fs.writeFileSync(
      path.join(archiveDir, HASH_A, '.meta.json'),
      JSON.stringify({ sample_id: 'known__repo__buggy' }),
    );
    fs.writeFileSync(sidMapPath, JSON.stringify({ 'known__repo__buggy': HASH_A }));
    const before = fs.readFileSync(sidMapPath, 'utf8');
    repairSidMapFromArchive(archiveDir, sidMapPath, { 'known__repo__buggy': HASH_A });
    assert.equal(fs.readFileSync(sidMapPath, 'utf8'), before);
  });

  it('saveSidMap returns false when archive dir is not writable', () => {
    const archiveDir = path.join(tmp, 'ro-archive');
    fs.mkdirSync(archiveDir, { recursive: true });
    fs.chmodSync(archiveDir, 0o555);
    const sidMapPath = path.join(archiveDir, '.sid-map.json');
    try {
      assert.equal(saveSidMap(sidMapPath, { foo: HASH_A }), false);
      assert.equal(fs.existsSync(sidMapPath), false);
    } finally {
      fs.chmodSync(archiveDir, 0o755);
    }
  });

  it('filterAndPaginateSamples supports search and paging', () => {
    const samples = [
      { sample_id: 'alpha__v1' },
      { sample_id: 'beta__v1' },
      { sample_id: 'alpha__v2' },
    ];
    const page1 = filterAndPaginateSamples(samples, { q: 'alpha', limit: 1, offset: 0 });
    assert.equal(page1.total, 2);
    assert.equal(page1.samples.length, 1);
    assert.equal(page1.samples[0].sample_id, 'alpha__v1');
    assert.equal(page1.has_more, true);

    const page2 = filterAndPaginateSamples(samples, { q: 'alpha', limit: 1, offset: 1 });
    assert.equal(page2.samples[0].sample_id, 'alpha__v2');
    assert.equal(page2.has_more, false);
  });

  it('buildArchiveIndex deduplicates archive hashes', () => {
    const idx = buildArchiveIndex([
      { source_hash: HASH_A, sample_id: 'repo__a' },
      { source_hash: HASH_A, sample_id: 'repo__b' },
    ]);
    assert.equal(idx.hashes.size, 1);
    assert.equal(idx.bySampleId.get('repo__a').source_hash, HASH_A);
  });
});
