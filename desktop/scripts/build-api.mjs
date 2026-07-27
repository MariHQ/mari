import { spawnSync } from "node:child_process";
import path from "node:path";

const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const server = path.resolve("../server");
const args = [
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onedir",
  "--name", "mari-api",
  "--paths", server,
  "--add-data", `${path.join(server, "init.sql")}:.`,
  "--collect-all", "strawberry",
  "--collect-all", "uvicorn",
  "--collect-all", "markdown",
  "--hidden-import", "psycopg_binary",
  "--distpath", "vendor/api",
  "--workpath", "build/pyinstaller",
  "--specpath", "build/pyinstaller",
  path.join(server, "desktop_entry.py"),
];
const result = spawnSync(python, args, { stdio: "inherit", shell: false });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
