const { app, BrowserWindow, dialog, Menu, shell } = require("electron");
const { execFile, execFileSync, spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { promisify } = require("node:util");

const execFileAsync = promisify(execFile);
const INSTANCE_NAME = "mari-cloud-desktop";
const DB_USER = "mari";
const DB_PASSWORD = "mari-desktop";
const DB_NAME = "mari_cloud";

let mainWindow;
let apiProcess;
let stackStopping = false;
let appUrl;

// Keep development and packaged builds on the product's stable data path
// instead of Electron's package-name-derived default.
app.setPath(
  "userData",
  process.env.MARI_DESKTOP_USER_DATA_DIR || path.join(app.getPath("appData"), "Mari"),
);

function resourcePath(...parts) {
  const root = app.isPackaged ? process.resourcesPath : __dirname;
  return path.join(root, ...parts);
}

function pg0Path() {
  const parts = app.isPackaged ? ["bin"] : ["vendor", "bin"];
  return resourcePath(...parts, process.platform === "win32" ? "pg0.exe" : "pg0");
}

function apiPath() {
  const executable = process.platform === "win32" ? "mari-api.exe" : "mari-api";
  const parts = app.isPackaged ? ["api"] : ["vendor", "api"];
  return resourcePath(...parts, "mari-api", executable);
}

function webPath() {
  return app.isPackaged ? resourcePath("web") : path.resolve(__dirname, "../web/dist");
}

function getOpenPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

async function startDatabase(port) {
  const binary = pg0Path();
  const dataDir = path.join(app.getPath("userData"), "postgres");
  fs.mkdirSync(dataDir, { recursive: true });

  // A crash may leave the previous local instance running. Stop only Mari's
  // named instance, then restart it on this launch's private loopback port.
  try {
    execFileSync(binary, ["stop", "--name", INSTANCE_NAME], {
      stdio: "ignore",
      timeout: 15_000,
    });
  } catch {
    // A first launch has no instance to stop.
  }

  await execFileAsync(binary, [
    "start",
    "--name", INSTANCE_NAME,
    "--port", String(port),
    "--data-dir", dataDir,
    "--username", DB_USER,
    "--password", DB_PASSWORD,
    "--database", DB_NAME,
    "-c", "listen_addresses=127.0.0.1",
  ], { timeout: 60_000, maxBuffer: 4 * 1024 * 1024 });
}

function startApi(port, dbPort, setupToken) {
  const userDataDir = app.getPath("userData");
  const logsDir = path.join(app.getPath("userData"), "logs");
  const buildsDir = path.join(app.getPath("userData"), "builds");
  const configFile = path.join(userDataDir, "mari.toml");
  fs.mkdirSync(logsDir, { recursive: true });
  fs.mkdirSync(buildsDir, { recursive: true });
  if (!fs.existsSync(configFile)) fs.writeFileSync(configFile, "");
  const log = fs.openSync(path.join(logsDir, "mari-api.log"), "a");
  const origin = `http://127.0.0.1:${port}`;

  apiProcess = spawn(apiPath(), [], {
    env: {
      ...process.env,
      MARI_DB: `postgresql://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${dbPort}/${DB_NAME}`,
      MARI_STATIC_DIR: webPath(),
      MARI_BUILDS_DIR: buildsDir,
      MARI_CONFIG: configFile,
      MARI_CORS_ORIGINS: origin,
      MARI_OAUTH_REDIRECT_BASE: origin,
      MARI_DESKTOP_SETUP_TOKEN: setupToken,
      MARI_DESKTOP_API_PORT: String(port),
      MARI_DESKTOP: "1",
      SSL_CERT_FILE: path.join(path.dirname(apiPath()), "_internal", "botocore", "cacert.pem"),
    },
    stdio: ["ignore", log, log],
    cwd: userDataDir,
    windowsHide: true,
  });
  apiProcess.once("exit", (code) => {
    if (!stackStopping && code !== 0) {
      dialog.showErrorBox(
        "Mari stopped",
        `The local Mari service exited unexpectedly. Details are in ${path.join(logsDir, "mari-api.log")}.`,
      );
      app.quit();
    }
  });
}

async function waitForApi(origin) {
  const deadline = Date.now() + 60_000;
  let lastError = "";
  while (Date.now() < deadline) {
    if (apiProcess && apiProcess.exitCode !== null) {
      throw new Error(`The local API exited with code ${apiProcess.exitCode}.`);
    }
    try {
      const response = await fetch(`${origin}/healthz`);
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`The local API did not become ready: ${lastError}`);
}

async function startStack() {
  for (const required of [pg0Path(), apiPath(), webPath()]) {
    if (!fs.existsSync(required)) throw new Error(`Desktop resource is missing: ${required}`);
  }

  const [dbPort, apiPort] = await Promise.all([getOpenPort(), getOpenPort()]);
  const setupToken = crypto.randomBytes(24).toString("base64url");
  await startDatabase(dbPort);
  startApi(apiPort, dbPort, setupToken);
  const origin = `http://127.0.0.1:${apiPort}`;
  await waitForApi(origin);

  const auth = await fetch(`${origin}/auth/me`).then((response) => response.json());
  const initialUrl = auth.needsSetup
    ? `${origin}/setup?desktop_token=${encodeURIComponent(setupToken)}`
    : origin;
  appUrl = initialUrl;
  return appUrl;
}

function stopStack() {
  if (stackStopping) return;
  stackStopping = true;
  if (apiProcess && apiProcess.exitCode === null) apiProcess.kill();
  try {
    execFileSync(pg0Path(), ["stop", "--name", INSTANCE_NAME], {
      stdio: "ignore",
      timeout: 15_000,
    });
  } catch {
    // The database may already have stopped during shutdown.
  }
}

function createMenu() {
  const template = [
    ...(process.platform === "darwin"
      ? [{
          label: app.name,
          submenu: [
            { role: "about" },
            { type: "separator" },
            { role: "hide" },
            { role: "hideOthers" },
            { role: "unhide" },
            { type: "separator" },
            { role: "quit" },
          ],
        }]
      : []),
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "selectAll" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    { label: "Window", submenu: [{ role: "minimize" }, { role: "close" }] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 900,
    minHeight: 640,
    show: false,
    title: "Mari",
    backgroundColor: "#f5ead4",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    let allowed = false;
    try {
      allowed = Boolean(appUrl) && new URL(url).origin === new URL(appUrl).origin;
    } catch {
      allowed = false;
    }
    if (!allowed) {
      event.preventDefault();
      if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
    }
  });
  mainWindow.once("ready-to-show", () => mainWindow.show());
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    createMenu();
    createWindow();
    try {
      await mainWindow.loadURL(await startStack());
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      dialog.showErrorBox("Mari could not start", detail);
      app.quit();
    }
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
        if (appUrl) void mainWindow.loadURL(appUrl);
      }
    });
  });
}

app.on("before-quit", stopStack);
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
