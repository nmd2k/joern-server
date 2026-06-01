import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** Match FileCPGRegistry archive layout and lookup rules. */
export function isSha256Hex(name) {
  return typeof name === 'string' && name.length === 64 && /^[0-9a-f]+$/i.test(name);
}

function readJsonFile(filePath) {
  try {
    const raw = fs.readFileSync(filePath, 'utf8');
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function pathExists(p) {
  try {
    fs.accessSync(p);
    return true;
  } catch {
    return false;
  }
}

/**
 * Return archive metadata when a registry lookup would succeed, else null.
 * Mirrors joern_server.cpg.file_registry.FileCPGRegistry.lookup.
 */
export function lookupArchiveEntry(archiveDir, sourceHash) {
  const archivePath = path.join(archiveDir, sourceHash);
  let stat;
  try {
    stat = fs.statSync(archivePath);
  } catch {
    return null;
  }

  if (stat.isDirectory()) {
    if (pathExists(path.join(archivePath, '.evicting'))) return null;
    const metaPath = path.join(archivePath, '.meta.json');
    if (!pathExists(metaPath)) return null;
    const meta = readJsonFile(metaPath);
    if (!meta) return null;
    return {
      source_hash: sourceHash,
      archive_path: archivePath,
      sample_id: typeof meta.sample_id === 'string' ? meta.sample_id : '',
      ...meta,
    };
  }

  if (stat.isFile()) {
    const evicting = path.join(archiveDir, `${sourceHash}.evicting`);
    if (pathExists(evicting)) return null;
    const metaPath = path.join(archiveDir, `${sourceHash}.meta.json`);
    if (pathExists(metaPath)) {
      const meta = readJsonFile(metaPath);
      if (!meta) return null;
      return {
        source_hash: sourceHash,
        archive_path: archivePath,
        sample_id: typeof meta.sample_id === 'string' ? meta.sample_id : '',
        ...meta,
      };
    }
    if (isSha256Hex(sourceHash)) {
      return {
        source_hash: sourceHash,
        archive_path: archivePath,
        sample_id: '',
      };
    }
  }

  return null;
}

/** Scan archive directory for valid CPG entries (deduped by source_hash). */
export function scanArchiveEntries(archiveDir) {
  const entries = [];
  const seen = new Set();
  if (!pathExists(archiveDir)) return entries;

  let names;
  try {
    names = fs.readdirSync(archiveDir);
  } catch {
    return entries;
  }

  for (const name of names.sort()) {
    if (name.startsWith('.')) continue;
    const itemPath = path.join(archiveDir, name);
    let stat;
    try {
      stat = fs.statSync(itemPath);
    } catch {
      continue;
    }
    if (stat.isDirectory()) {
      const entry = lookupArchiveEntry(archiveDir, name);
      if (entry) {
        entries.push(entry);
        seen.add(name);
      }
    }
  }

  for (const name of names.sort()) {
    if (name.startsWith('.') || name.endsWith('.meta.json') || name.endsWith('.evicting')) continue;
    const itemPath = path.join(archiveDir, name);
    let stat;
    try {
      stat = fs.statSync(itemPath);
    } catch {
      continue;
    }
    if (stat.isFile() && isSha256Hex(name) && !seen.has(name)) {
      const entry = lookupArchiveEntry(archiveDir, name);
      if (entry) {
        entries.push(entry);
        seen.add(name);
      }
    }
  }

  return entries;
}

export function loadSidMap(sidMapPath) {
  if (!pathExists(sidMapPath)) return {};
  const parsed = readJsonFile(sidMapPath);
  if (!parsed) return {};
  const out = {};
  for (const [sampleId, sourceHash] of Object.entries(parsed)) {
    if (typeof sampleId === 'string' && sampleId && typeof sourceHash === 'string' && sourceHash) {
      out[sampleId] = sourceHash;
    }
  }
  return out;
}

function isPermissionError(err) {
  return err && (err.code === 'EACCES' || err.code === 'EPERM');
}

/** True when sid-map can be updated (playground is often read-only on shared CPG storage). */
export function canWriteSidMap(sidMapPath) {
  const dir = path.dirname(sidMapPath);
  try {
    fs.accessSync(dir, fs.constants.W_OK);
    if (pathExists(sidMapPath)) {
      fs.accessSync(sidMapPath, fs.constants.W_OK);
    }
    return true;
  } catch {
    return false;
  }
}

/**
 * Merge repo sample_ids from archive .meta.json into an in-memory sid-map (no disk I/O).
 * Pass pre-scanned `archiveEntries` to avoid scanning the archive directory twice.
 */
export function mergeSidMapFromArchive(archiveDir, sidMap, archiveEntries = null) {
  const next = { ...sidMap };
  const entries = archiveEntries ?? scanArchiveEntries(archiveDir);
  for (const entry of entries) {
    const sampleId = entry.sample_id;
    const sourceHash = entry.source_hash;
    if (!sampleId || !sourceHash) continue;
    if (!sampleId.includes('__')) continue;
    if (!next[sampleId]) next[sampleId] = sourceHash;
  }
  return next;
}

/** Persist sid-map; returns false on permission errors instead of throwing. */
export function saveSidMap(sidMapPath, mapping) {
  if (!canWriteSidMap(sidMapPath)) {
    return false;
  }
  try {
    const dir = path.dirname(sidMapPath);
    fs.mkdirSync(dir, { recursive: true });
    const tmp = `${sidMapPath}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(mapping), 'utf8');
    fs.renameSync(tmp, sidMapPath);
    return true;
  } catch (err) {
    if (isPermissionError(err)) return false;
    throw err;
  }
}

/**
 * Optionally persist sample_id → source_hash pairs from archive metadata.
 * Listing uses mergeSidMapFromArchive in memory; disk write is opt-in only.
 */
export function repairSidMapFromArchive(archiveDir, sidMapPath, sidMap) {
  const next = mergeSidMapFromArchive(archiveDir, sidMap);
  const changed = Object.keys(next).length !== Object.keys(sidMap).length;
  if (!changed) {
    return { changed: false, sidMap: next, persisted: false };
  }
  const persisted = saveSidMap(sidMapPath, next);
  return { changed: true, sidMap: next, persisted };
}

export function cpgOutExists(cpgOutDir, sampleId) {
  const outPath = path.join(cpgOutDir, sampleId);
  if (!pathExists(outPath)) return false;
  try {
    const stat = fs.statSync(outPath);
    return stat.isDirectory() || stat.isFile();
  } catch {
    return false;
  }
}

/** Index archive scan results for O(1) hash / sample_id lookups. */
export function buildArchiveIndex(archiveEntries) {
  const hashes = new Set();
  const bySampleId = new Map();
  for (const entry of archiveEntries || []) {
    if (entry?.source_hash) hashes.add(entry.source_hash);
    const sampleId = entry?.sample_id;
    if (sampleId) bySampleId.set(sampleId, entry);
  }
  return { hashes, bySampleId };
}

/**
 * Filter and paginate a sample list (client/server shared helper).
 * When limit is omitted, returns the full filtered list.
 */
export function filterAndPaginateSamples(samples, options = {}) {
  const q = String(options.q || '').trim().toLowerCase();
  let filtered = samples || [];
  if (q) {
    filtered = filtered.filter((s) => String(s.sample_id || '').toLowerCase().includes(q));
  }
  const total = filtered.length;
  const offset = Math.max(0, parseInt(options.offset, 10) || 0);
  const rawLimit = options.limit;
  if (rawLimit === undefined || rawLimit === null || rawLimit === '') {
    return { samples: filtered, total, limit: null, offset: 0, has_more: false };
  }
  const limit = Math.max(1, Math.min(parseInt(rawLimit, 10) || 100, 5000));
  const page = filtered.slice(offset, offset + limit);
  return {
    samples: page,
    total,
    limit,
    offset,
    has_more: offset + page.length < total,
  };
}

/**
 * Build merged sample list from sid-map, cpg/out, and archive scan.
 */
export function collectCpgSamples({
  cpgOutDir,
  cpgArchiveDir,
  sidMapPath,
  containerOutPrefix = '/workspace/cpg/out',
  repairSidMap = false,
}) {
  let sidMap = loadSidMap(sidMapPath);
  if (repairSidMap) {
    const repaired = repairSidMapFromArchive(cpgArchiveDir, sidMapPath, sidMap);
    sidMap = repaired.sidMap;
  }
  const archiveEntries = pathExists(cpgArchiveDir) ? scanArchiveEntries(cpgArchiveDir) : [];
  sidMap = mergeSidMapFromArchive(cpgArchiveDir, sidMap, archiveEntries);
  const archiveIndex = buildArchiveIndex(archiveEntries);

  const sampleMap = new Map();

  const upsert = (sampleId, patch) => {
    const current = sampleMap.get(sampleId) || {
      sample_id: sampleId,
      source_hash: null,
      in_out: false,
      archived: false,
      cpg_path: `${containerOutPrefix}/${sampleId}`,
    };
    sampleMap.set(sampleId, { ...current, ...patch, sample_id: sampleId });
  };

  for (const [sampleId, sourceHash] of Object.entries(sidMap)) {
    const archived = archiveIndex.hashes.has(sourceHash);
    upsert(sampleId, {
      source_hash: sourceHash,
      in_out: cpgOutExists(cpgOutDir, sampleId),
      archived,
    });
  }

  if (pathExists(cpgOutDir)) {
    let outEntries;
    try {
      outEntries = fs.readdirSync(cpgOutDir, { withFileTypes: true });
    } catch {
      outEntries = [];
    }
    for (const entry of outEntries) {
      if (entry.name.startsWith('.') || entry.name.endsWith('.joern_hash')) continue;
      if (!entry.isDirectory() && !entry.isFile()) continue;
      const current = sampleMap.get(entry.name);
      upsert(entry.name, {
        source_hash: current?.source_hash ?? sidMap[entry.name] ?? null,
        in_out: true,
        archived: current?.archived ?? false,
      });
    }
  }

  for (const entry of archiveEntries) {
    const sourceHash = entry.source_hash;
    const sampleId = entry.sample_id;
    const archived = true;
    if (sampleId) {
      upsert(sampleId, {
        source_hash: sourceHash,
        in_out: cpgOutExists(cpgOutDir, sampleId),
        archived,
      });
      continue;
    }
    // Legacy archive with hash only: expose by hash if referenced in sid-map values.
    for (const [sid, hash] of Object.entries(sidMap)) {
      if (hash === sourceHash) {
        upsert(sid, {
          source_hash: sourceHash,
          in_out: cpgOutExists(cpgOutDir, sid),
          archived,
        });
      }
    }
  }

  const samples = Array.from(sampleMap.values()).sort((a, b) =>
    a.sample_id.localeCompare(b.sample_id),
  );
  return { samples, sidMap };
}

function dirHasArchiveEntries(archiveDir) {
  if (!pathExists(archiveDir)) return false;
  try {
    const names = fs.readdirSync(archiveDir);
    return names.some((name) => {
      if (name.startsWith('.')) return false;
      if (name.endsWith('.meta.json') || name.endsWith('.evicting')) return false;
      const itemPath = path.join(archiveDir, name);
      try {
        const stat = fs.statSync(itemPath);
        return stat.isFile() || stat.isDirectory();
      } catch {
        return false;
      }
    });
  } catch {
    return false;
  }
}

/**
 * Resolve host CPG paths. Prefers CPG_BASE_DIR when set; otherwise first base dir
 * whose archive/ contains CPG data (e.g. /datadrive/cpg on production hosts).
 */
export function resolveCpgPaths(options = {}) {
  const repoRoot = options.repoRoot || path.resolve(__dirname, '..');
  const candidates = [];
  const add = (base) => {
    if (base && !candidates.includes(base)) candidates.push(path.resolve(base));
  };

  add(process.env.CPG_BASE_DIR);
  add('/workspace/cpg');
  add('/datadrive/cpg');
  add(path.join(repoRoot, 'cpg'));

  const outOverride = process.env.CPG_OUT_DIR;
  const archiveOverride = process.env.CPG_ARCHIVE_DIR;
  const sidMapOverride = process.env.CPG_SID_MAP_PATH;

  if (outOverride && archiveOverride) {
    return {
      cpgBaseDir: process.env.CPG_BASE_DIR || path.dirname(path.dirname(outOverride)),
      cpgOutDir: path.resolve(outOverride),
      cpgArchiveDir: path.resolve(archiveOverride),
      cpgSidMapPath: sidMapOverride
        ? path.resolve(sidMapOverride)
        : path.join(path.resolve(archiveOverride), '.sid-map.json'),
      resolvedFrom: 'env-overrides',
    };
  }

  for (const base of candidates) {
    const archiveDir = path.join(base, 'archive');
    if (dirHasArchiveEntries(archiveDir)) {
      return {
        cpgBaseDir: base,
        cpgOutDir: outOverride ? path.resolve(outOverride) : path.join(base, 'out'),
        cpgArchiveDir: archiveOverride ? path.resolve(archiveOverride) : archiveDir,
        cpgSidMapPath: sidMapOverride
          ? path.resolve(sidMapOverride)
          : path.join(archiveOverride ? path.resolve(archiveOverride) : archiveDir, '.sid-map.json'),
        resolvedFrom: base,
      };
    }
  }

  const fallbackBase = process.env.CPG_BASE_DIR
    ? path.resolve(process.env.CPG_BASE_DIR)
    : path.resolve('/workspace/cpg');
  const archiveDir = archiveOverride ? path.resolve(archiveOverride) : path.join(fallbackBase, 'archive');
  return {
    cpgBaseDir: fallbackBase,
    cpgOutDir: outOverride ? path.resolve(outOverride) : path.join(fallbackBase, 'out'),
    cpgArchiveDir: archiveDir,
    cpgSidMapPath: sidMapOverride ? path.resolve(sidMapOverride) : path.join(archiveDir, '.sid-map.json'),
    resolvedFrom: 'fallback',
  };
}

export function shouldScanLocalArchive(archiveDir) {
  if (process.env.CPG_SAMPLES_SOURCE === 'joern') return false;
  if (process.env.CPG_SAMPLES_SOURCE === 'local') return true;
  return dirHasArchiveEntries(archiveDir);
}
