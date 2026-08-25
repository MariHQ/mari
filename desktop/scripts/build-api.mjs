import { spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const server = path.resolve("../server");
const componentPackages = path.resolve("../mari-components/packages");
const componentPaths = readdirSync(componentPackages, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => path.join(componentPackages, entry.name, "src"));
const combinedComponents = mkdtempSync(path.join(tmpdir(), "mari-components-"));
const combinedPackage = path.join(combinedComponents, "mari_components");
for (const source of componentPaths) {
  cpSync(path.join(source, "mari_components"), combinedPackage, {
    recursive: true,
    force: true,
  });
}
const componentModules = [...new Set(componentPaths.flatMap((source) => {
  const walk = (directory) => readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) return walk(target);
    if (!entry.name.endsWith(".py")) return [];

    const relative = path.relative(source, target).replace(/\.py$/, "");
    return [relative.endsWith(`${path.sep}__init__`)
      ? relative.slice(0, -`${path.sep}__init__`.length).split(path.sep).join(".")
      : relative.split(path.sep).join(".")];
  });
  return walk(source);
}))];
const args = [
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onedir",
  "--name", "mari-api",
  "--paths", server,
  "--paths", combinedComponents,
  ...componentModules.flatMap((module) => ["--hidden-import", module]),
  "--add-data", `${path.join(server, "init.sql")}:.`,
  "--add-data", `${path.join(server, "migrations")}:migrations`,
  "--collect-all", "strawberry",
  "--collect-all", "uvicorn",
  "--collect-all", "markdown",
  "--hidden-import", "psycopg_binary",
  "--distpath", "vendor/api",
  "--workpath", "build/pyinstaller",
  "--specpath", "build/pyinstaller",
  path.join(server, "mari_server", "scripts", "desktop_entry.py"),
];
try {
  const result = spawnSync(python, args, { stdio: "inherit", shell: false });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
} finally {
  rmSync(combinedComponents, { recursive: true, force: true });
}
