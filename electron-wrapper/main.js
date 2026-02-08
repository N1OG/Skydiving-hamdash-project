const { app, BrowserWindow, dialog } = require("electron");
const path = require("path");
const { spawn } = require("child_process");

let mainWindow = null;
let serverProc = null;

const SERVER_HOST = "127.0.0.1";
const SERVER_PORT = "8765";
const SERVER_ARGS = ["--host", SERVER_HOST, "--port", SERVER_PORT];

function isWindows() {
  return process.platform === "win32";
}

function getResourcePath(rel) {
  // When packaged, extraResources land in process.resourcesPath.
  // In dev, we reference files from repo root (one level up).
  if (app.isPackaged) return path.join(process.resourcesPath, rel);
  return path.join(app.getAppPath(), "..", rel);
}

function pickPythonCommandCandidates() {
  // Try common names. Windows typically has "python".
  return isWindows() ? ["python", "py", "python3"] : ["python3", "python"];
}

function spawnServer(pythonCmd) {
  const serverScript = getResourcePath("dz_feed_server.py");
  const cwd = path.dirname(serverScript);
  const args = [serverScript, ...SERVER_ARGS];

  const proc = spawn(pythonCmd, args, {
    cwd,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true
  });

  proc.stdout.on("data", (d) => console.log(`[server] ${d.toString().trimEnd()}`));
  proc.stderr.on("data", (d) => console.error(`[server:err] ${d.toString().trimEnd()}`));
  proc.on("exit", (code, signal) => console.log(`[server] exited code=${code} signal=${signal}`));

  return proc;
}

async function startServerWithFallback() {
  const candidates = pickPythonCommandCandidates();

  for (const cmd of candidates) {
    try {
      const proc = spawnServer(cmd);

      // Give it a moment to fail fast if python isn't found.
      await new Promise((r) => setTimeout(r, 900));

      if (!proc.killed && proc.exitCode === null) return proc;
    } catch (e) {
      // try next
    }
  }

  throw new Error(
    "Could not start Python feed server.\n\nInstall Python 3 and ensure 'python' (or 'py') is available in PATH."
  );
}

function createWindow() {
  const dashboardPath = getResourcePath("dashboard.html");

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 720,
    backgroundColor: "#000000",
    fullscreen: true,
    autoHideMenuBar: true,
    frame: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true
    }
  });

  mainWindow.loadFile(dashboardPath);

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

async function shutdownServer() {
  if (!serverProc) return;

  return new Promise((resolve) => {
    try {
      serverProc.once("exit", () => resolve());
      serverProc.kill("SIGTERM");

      setTimeout(() => {
        if (serverProc && serverProc.exitCode === null) {
          try { serverProc.kill("SIGKILL"); } catch {}
        }
        resolve();
      }, 1500);
    } catch {
      resolve();
    }
  });
}

app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");

app.on("window-all-closed", async () => {
  await shutdownServer();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", async (e) => {
  e.preventDefault();
  await shutdownServer();
  app.exit(0);
});

app.whenReady().then(async () => {
  try {
    serverProc = await startServerWithFallback();
    createWindow();
  } catch (err) {
    dialog.showErrorBox("Skydiving Dashboard", String(err?.message || err));
    app.quit();
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});
