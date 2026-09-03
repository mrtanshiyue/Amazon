import { readFileSync, writeFileSync } from "node:fs";

const databaseId = process.argv[2];
if (!databaseId) throw new Error("D1 database id is required");

const source = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8");
if (!source.includes("__D1_DATABASE_ID__")) throw new Error("D1 placeholder missing");

writeFileSync(
  new URL("../wrangler.deploy.jsonc", import.meta.url),
  source.replace("__D1_DATABASE_ID__", databaseId)
);
console.log("Generated wrangler.deploy.jsonc");
