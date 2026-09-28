// Compiles the Malloy model against the live database and writes build/csm.model.json, which the
// app's admin page reads (CLAUDE.md §3 Semantic layer). Node is only needed here, not at runtime.
//
//   DATABASE_URL=postgresql://... npm run compile            # compile, check every view runs, write JSON
//   DATABASE_URL=... npm run compile -- --check              # fail if build/ is out of date (CI)
//
// Compiling reads each table's columns from Postgres, so a model that names a missing table or
// column fails here. Every named view is also run once (LIMIT 1) to prove its SQL executes.
// The output is deterministic (no timestamps) so it can be committed and diffed.

import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import malloy from '@malloydata/malloy';
import postgres from '@malloydata/db-postgres';

const { SingleConnectionRuntime } = malloy;
const { PostgresConnection } = postgres;
const MALLOY_VERSION = createRequire(import.meta.url)('@malloydata/malloy/package.json').version;

const here = dirname(fileURLToPath(import.meta.url));
const MODEL = join(here, 'models', 'csm.malloy');
const OUT = join(here, 'build', 'csm.model.json');
const check = process.argv.includes('--check');

export function connectionFromEnv() {
  const raw = process.env.DATABASE_URL || 'postgresql://postgres@127.0.0.1:5432/csm';
  const connectionString = raw.replace(/^postgres(ql)?\+\w+:/, 'postgresql:').replace(/^postgres:/, 'postgresql:');
  return new PostgresConnection({ name: 'postgres', connectionString });
}

export function runtimeFor(connection) {
  const urlReader = { readURL: async (url) => readFile(fileURLToPath(url), 'utf8') };
  return new SingleConnectionRuntime({ urlReader, connection });
}

const doc = (entity) => entity.annotations.texts('"').map((t) => t.replace(/^#"\s?/, '').trim()).join(' ').trim();

// The Malloy text of `view: name is { ... }` inside the given source's block, found by brace matching.
function viewText(text, sourceName, viewName) {
  const start = text.indexOf(`source: ${sourceName} is `);
  const at = text.indexOf(`view: ${viewName} is {`, start);
  if (start < 0 || at < 0) return null;
  let depth = 0;
  for (let i = text.indexOf('{', at); i < text.length; i++) {
    if (text[i] === '{') depth++;
    if (text[i] === '}' && --depth === 0) return text.slice(at, i + 1);
  }
  return null;
}

// join alias -> {source, kind} for one source's block, read from the model text (the compiled API only
// exposes internal ids for the joined source).
function joinsIn(text, sourceName) {
  const start = text.indexOf(`source: ${sourceName} is `);
  const next = text.indexOf('\nsource: ', start + 1);
  const block = text.slice(start, next < 0 ? text.length : next);
  const out = {};
  for (const m of block.matchAll(/join_(one|many|cross):\s+(\w+)(?:\s+is\s+(\w+))?/g)) {
    out[m[2]] = { source: m[3] ?? m[2], join: m[1] };
  }
  return out;
}

// A nested field is a join if the model declares it with join_one/join_many; otherwise it's an
// array column (e.g. clients.domains), which Malloy also represents as a nested structure.
function fieldKind(f, joins) {
  if (f.isExploreField()) return f.name in joins ? 'join' : 'dimension';
  if (f.isQueryField()) return 'view';
  return f.isCalculation() ? 'measure' : 'dimension';
}

async function main() {
  const text = await readFile(MODEL, 'utf8');
  const connection = connectionFromEnv();
  const runtime = runtimeFor(connection);
  const url = pathToFileURL(MODEL);
  const model = await runtime.getModel(url);

  const sources = [];
  const views = [];
  for (const explore of model.explores) {
    const fields = [];
    const joins = joinsIn(text, explore.name);
    for (const f of explore.allFields) {
      const kind = fieldKind(f, joins);
      fields.push({
        name: f.name,
        kind,
        type: kind === 'join' ? 'source' : kind === 'view' ? 'view' : f.isExploreField() ? 'array' : f.type,
        doc: doc(f),
        ...(kind === 'join' ? joins[f.name] : {}),
      });
      if (kind === 'view') {
        const query = `run: ${explore.name} -> ${f.name}`;
        const sql = await runtime.loadModel(url).loadQuery(query).getSQL();
        // Prove it executes; LIMIT 1 keeps it cheap.
        await connection.runSQL(`SELECT * FROM (${sql}) AS compile_check LIMIT 1`);
        views.push({ source: explore.name, name: f.name, doc: doc(f), query, malloy: viewText(text, explore.name, f.name), sql });
      }
    }
    sources.push({ name: explore.name, doc: doc(explore), fields });
  }

  const out = {
    model: 'analytics/models/csm.malloy',   // repo-relative, fixed so the output doesn't depend on where it's built
    model_sha256: createHash('sha256').update(text).digest('hex'),
    model_doc: model.annotations.texts('"').map((t) => t.replace(/^##"\s?/, '').trim()).join(' ').trim(),
    malloy_version: MALLOY_VERSION,
    sources,
    views,
    malloy_text: text,
  };
  const json = JSON.stringify(out, null, 2) + '\n';

  if (check) {
    const current = await readFile(OUT, 'utf8').catch(() => '');
    if (current !== json) {
      console.error(`${relative(process.cwd(), OUT)} is out of date: run \`npm run compile\` in analytics/ and commit it.`);
      process.exitCode = 1;
    } else {
      console.log('Semantic model build is up to date.');
    }
  } else {
    await mkdir(dirname(OUT), { recursive: true });
    await writeFile(OUT, json);
    console.log(`Wrote ${relative(process.cwd(), OUT)}: ${sources.length} sources, ${views.length} views.`);
  }
  await connection.close();
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((e) => { console.error(e.message ?? e); process.exit(1); });
}
