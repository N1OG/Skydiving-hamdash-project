const { app, BrowserWindow, dialog } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const fs = require("fs");

let mainWindow = null;
let serverProc = null;

const SERVER_HOST = "127.0.0.1";
const SERVER_PORT = "8765";
const SERVER_ARGS = ["--host", SERVER_HOST, "--port", SERVER_PORT];

function isWindows() {
  return process.platform === "win32";
}

function exists(p) {
  try {
    fs.accessSync(p, fs.constants.F_OK);
    return true;
  } catch {
    return false;
  }
}

function ensureDir(p) {
  if (!exists(p)) fs.mkdirSync(p, { recursive: true });
}

function copyFileIfMissing(src, dst) {
  if (!exists(dst)) {
    ensureDir(path.dirname(dst));
    fs.copyFileSync(src, dst);
  }
}

function copyDirIfMissing(srcDir, dstDir) {
  if (exists(dstDir)) return;
  ensureDir(dstDir);
  fs.cpSync(srcDir, dstDir, { recursive: true, force: false, errorOnExist: false });
}

function getBundledPath(rel) {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, rel);
  }
  return path.join(app.getAppPath(), "..", rel);
}

function getRuntimeDir() {
  return path.join(app.getPath("userData"), "runtime");
}

function getAppVersionString() {
  try {
    return String(app.getVersion() || "0.0.0");
  } catch {
    return "0.0.0";
  }
}

function writeRuntimeVersionJson(runtimeDir) {
  const versionPath = path.join(runtimeDir, "version.json");
  const payload = {
    version: getAppVersionString(),
    productName: app.getName(),
    generatedAt: new Date().toISOString()
  };

  try {
    fs.writeFileSync(versionPath, JSON.stringify(payload, null, 2), "utf8");
  } catch {
    // not fatal
  }
}

/*
 * Ensure that the dashboard HTML, feed server script and configuration
 * files in the user's runtime directory are up to date with the version
 * bundled in this release. Stores the current packaged version in
 * runtime/version.txt. If the version differs, it deletes the old
 * dashboard, server script and config before copying the new ones.
 */
function ensureRuntimeFiles() {
  const runtimeDir = getRuntimeDir();
  ensureDir(runtimeDir);

  const versionFile = path.join(runtimeDir, "version.txt");
  const currentVersion = getAppVersionString();
  let existingVersion = null;

  if (exists(versionFile)) {
    try {
      existingVersion = fs.readFileSync(versionFile, "utf8").trim();
    } catch {
      existingVersion = null;
    }
  }

  function clearManagedFiles() {
    const filesToRemove = [
      path.join(runtimeDir, "dashboard.html"),
      path.join(runtimeDir, "dz_feed_server.py"),
      path.join(runtimeDir, "version.json")
    ];
    const dirsToRemove = [path.join(runtimeDir, "config")];

    for (const f of filesToRemove) {
      try {
        if (exists(f)) fs.unlinkSync(f);
      } catch {
        // ignore
      }
    }

    for (const d of dirsToRemove) {
      try {
        if (exists(d)) fs.rmSync(d, { recursive: true, force: true });
      } catch {
        // ignore
      }
    }
  }

  if (existingVersion !== currentVersion) {
    clearManagedFiles();
  }

  const srcDashboard = getBundledPath("dashboard.html");
  const srcServer = getBundledPath("dz_feed_server.py");
  const srcConfigDir = getBundledPath("config");

  const dstDashboard = path.join(runtimeDir, "dashboard.html");
  const dstServer = path.join(runtimeDir, "dz_feed_server.py");
  const dstConfigDir = path.join(runtimeDir, "config");

  copyFileIfMissing(srcDashboard, dstDashboard);
  copyFileIfMissing(srcServer, dstServer);
  copyDirIfMissing(srcConfigDir, dstConfigDir);

  try {
    fs.writeFileSync(versionFile, currentVersion, "utf8");
  } catch {
    // not fatal
  }

  writeRuntimeVersionJson(runtimeDir);

  return {
    runtimeDir,
    dashboardPath: dstDashboard,
    serverScriptPath: dstServer
  };
}

function pickPythonCommandCandidates() {
  return isWindows() ? ["python", "py", "python3"] : ["python3", "python"];
}

function spawnServer(pythonCmd, serverScriptPath, cwd) {
  const args = [serverScriptPath, ...SERVER_ARGS];

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

async function startServerWithFallback(serverScriptPath, cwd) {
  const candidates = pickPythonCommandCandidates();

  for (const cmd of candidates) {
    try {
      const proc = spawnServer(cmd, serverScriptPath, cwd);
      await new Promise((r) => setTimeout(r, 900));

      if (!proc.killed && proc.exitCode === null) {
        return proc;
      }
    } catch {
      // try next
    }
  }

  throw new Error(
    "Could not start Python feed server.\n\nInstall Python 3 and ensure 'python' (or 'py') is available in PATH."
  );
}

function createWindow(dashboardPath) {
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
      sandbox: true,
      backgroundThrottling: false
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
          try {
            serverProc.kill("SIGKILL");
          } catch {
            // ignore
          }
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
    const { runtimeDir, dashboardPath, serverScriptPath } = ensureRuntimeFiles();

    serverProc = await startServerWithFallback(serverScriptPath, runtimeDir);

    createWindow(dashboardPath);
  } catch (err) {
    dialog.showErrorBox("Skydiving Dashboard", String(err?.message || err));
    app.quit();
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0 && mainWindow === null) {
      const { dashboardPath } = ensureRuntimeFiles();
      createWindow(dashboardPath);
    }
  });
});