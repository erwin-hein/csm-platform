// Runs one Malloy query against the model and prints the rows, for exploring the model locally.
//
//   DATABASE_URL=postgresql://... npm run query -- "run: opportunities -> pipeline_by_stage"
//   DATABASE_URL=... npm run query -- "run: deliverables -> { group_by: dep_state; aggregate: deliverable_count }"
//   DATABASE_URL=... npm run query -- --sql "run: engagements -> portfolio"      # print the SQL instead

import { pathToFileURL, fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { connectionFromEnv, runtimeFor } from './compile.mjs';

const args = process.argv.slice(2);
const showSql = args[0] === '--sql';
const query = args.filter((a) => a !== '--sql').join(' ');
if (!query) {
  console.error('Usage: npm run query -- [--sql] "run: <source> -> <view or { query }>"');
  process.exit(2);
}
const connection = connectionFromEnv();
const model = runtimeFor(connection).loadModel(pathToFileURL(join(dirname(fileURLToPath(import.meta.url)), 'models', 'csm.malloy')));
try {
  const q = model.loadQuery(query);
  if (showSql) console.log(await q.getSQL());
  else console.table((await q.run({ rowLimit: 200 })).data.toObject());
} catch (e) {
  console.error(e.message ?? e);
  process.exitCode = 1;
} finally {
  await connection.close();
}
