import { chmod, mkdir, stat } from "node:fs/promises";
import { createWriteStream } from "node:fs";
import { pipeline } from "node:stream/promises";
import path from "node:path";

const PG0_VERSION = "0.14.2";
const targets = {
  "darwin-arm64": "pg0-darwin-aarch64",
  "darwin-x64": "pg0-darwin-x86_64",
  "linux-arm64": "pg0-linux-aarch64-gnu",
  "linux-x64": "pg0-linux-x86_64-gnu",
  "win32-x64": "pg0-windows-x86_64.exe",
};
const requestedPlatform = process.env.MARI_TARGET_PLATFORM || process.platform;
const requestedArch = process.env.MARI_TARGET_ARCH || process.arch;
const asset = targets[`${requestedPlatform}-${requestedArch}`];
if (!asset) throw new Error(`pg0 has no desktop build for ${requestedPlatform}-${requestedArch}`);

const binDir = path.resolve("vendor/bin");
const destination = path.join(binDir, requestedPlatform === "win32" ? "pg0.exe" : "pg0");
await mkdir(binDir, { recursive: true });

let present = false;
try {
  present = (await stat(destination)).size > 1_000_000;
} catch {
  // Download below.
}
if (!present) {
  const url = `https://github.com/vectorize-io/pg0/releases/download/v${PG0_VERSION}/${asset}`;
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok || !response.body) throw new Error(`Could not download ${url}: HTTP ${response.status}`);
  await pipeline(response.body, createWriteStream(destination));
}
if (requestedPlatform !== "win32") await chmod(destination, 0o755);
