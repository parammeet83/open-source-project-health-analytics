// Fails the build if the production bundle contains secrets, database URLs or localhost API calls.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const DIST = new URL("../dist/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const FORBIDDEN = [
  [/postgres(?:ql)?(?:\+psycopg)?:\/\//i, "database connection string"],
  [/\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}/, "GitHub token"],
  [/github_pat_[A-Za-z0-9_]{20,}/, "GitHub fine-grained token"],
  [/neon\.tech/i, "Neon database host"],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, "private key"],
  [/"type"\s*:\s*"service_account"/, "Google service-account key"],
  [/(PIPELINE|MIGRATION|API)_DATABASE_URL/, "server-side environment variable name"],
  // A local API address always carries a port (e.g. the dev fallback http://localhost:8000).
  // React Router legitimately contains the bare string "http://localhost" as a URL-parsing base.
  [/https?:\/\/(?:localhost|127\.0\.0\.1):\d+/i, "localhost API URL"],
];
const expectedApi = (process.env.VITE_API_BASE_URL ?? "").trim().replace(/\/+$/, "");
let foundApi = false;

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

let problems = 0;
let scanned = 0;
for (const file of files(DIST)) {
  if (!/\.(js|css|html|json|map|txt|svg)$/.test(file)) continue;
  scanned += 1;
  const text = readFileSync(file, "utf8");
  if (expectedApi && text.includes(expectedApi)) foundApi = true;
  for (const [pattern, label] of FORBIDDEN) {
    if (pattern.test(text)) {
      console.error(`FORBIDDEN (${label}) in ${file}`);
      problems += 1;
    }
  }
}
if (scanned === 0) {
  console.error("No build output found in dist/ — run `npm run build` first.");
  process.exit(1);
}
if (expectedApi && !foundApi) {
  console.error(`The configured API URL (${expectedApi}) was not found in the bundle.`);
  problems += 1;
}
if (problems) process.exit(1);
console.log(
  `dist/ is clean: scanned ${scanned} files${expectedApi ? `; API base URL ${expectedApi} is embedded` : ""}.`,
);
