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
  // Copy folder recursively only if destination doesn't exist (preserves user edits)
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
  // Writable per-user location
  // Example: C:\Users\<you>\AppData\Roaming\Skydiving Dashboard\runtime
  return path.join(app.getPath("userData"), "runtime");
}

/*
 * Ensure that the dashboard HTML, feed server script and configuration
 * files in the user's runtime directory are up to date with the version
 * bundled in this release.  Stores the current packaged version in
 * runtime/version.txt.  If the version differs, it deletes the old
 * dashboard, server script and config before copying the new ones.
 */
function ensureRuntimeFiles() {
  const runtimeDir = getRuntimeDir();
  ensureDir(runtimeDir);

  const versionFile = path.join(runtimeDir, "version.txt");
  const currentVersion = app.getVersion();
  let existingVersion = null;
  if (exists(versionFile)) {
    try {
      existingVersion = fs.readFileSync(versionFile, "utf8").trim();
    } catch {
      existingVersion = null;
    }
  }

  // Delete only the files managed by the installer when version changes
  function clearManagedFiles() {
    const filesToRemove = [
      path.join(runtimeDir, "dashboard.html"),
      path.join(runtimeDir, "dz_feed_server.py"),
    ];
    const dirsToRemove = [path.join(runtimeDir, "config")];
    for (const f of filesToRemove) {
      try {
        if (exists(f)) fs.unlinkSync(f);
      } catch {}
    }
    for (const d of dirsToRemove) {
      try {
        if (exists(d)) fs.rmSync(d, { recursive: true, force: true });
      } catch {}
    }
  }

  // Remove stale files if this packaged version is newer than the runtime copy
  if (existingVersion !== currentVersion) {
    clearManagedFiles();
  }

  // Source (bundled) paths
  const srcDashboard = getBundledPath("dashboard.html");
  const srcServer = getBundledPath("dz_feed_server.py");
  const srcConfigDir = getBundledPath("config");

  // Destination (runtime) paths
  const dstDashboard = path.join(runtimeDir, "dashboard.html");
  const dstServer = path.join(runtimeDir, "dz_feed_server.py");
  const dstConfigDir = path.join(runtimeDir, "config");

  // Copy files only if they aren’t present
  copyFileIfMissing(srcDashboard, dstDashboard);
  copyFileIfMissing(srcServer, dstServer);
  copyDirIfMissing(srcConfigDir, dstConfigDir);

  // Record the version used to populate runtime files
  try {
    fs.writeFileSync(versionFile, currentVersion);
  } catch {}

  return {
    runtimeDir,
    dashboardPath: dstDashboard,
    serverScriptPath: dstServer
  };
}

function pickPythonCommandCandidates() {
  // Try common names. Windows typically has "python" or "py".
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
      // Wait briefly to see if process exits quickly due to missing Python
      await new Promise((r) => setTimeout(r, 900));
      if (!proc.killed && proc.exitCode === null) return proc;
    } catch {
      // Try next
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
      // Enable <webview> tags to embed Burble manifests directly
      webviewTag: true
    }
  });

  // Load the dashboard
  mainWindow.loadFile(dashboardPath);

  // Intercept calls to window.open() from the renderer.  For HTTP/HTTPS links,
  // create a new BrowserWindow so that the page runs as a top‑level navigation.
  // This allows Burble's redirect and cookie handshake to complete properly.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://") || url.startsWith("https://")) {
      const popup = new BrowserWindow({
        fullscreen: true,
        autoHideMenuBar: true,
        frame: false,
        webPreferences: {
          nodeIntegration: false,
          contextIsolation: true,
          sandbox: true
        }
      });
      // Using the same URL for the referrer mimics a normal browser request
      popup.loadURL(url, { httpReferrer: url });
      return { action: "deny" };
    }
    return { action: "allow" };
  });

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

// Permit audio/video playback without user interaction
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
    // Copy bundled files to a writable runtime directory and start the server
    const { runtimeDir, dashboardPath, serverScriptPath } = ensureRuntimeFiles();
    serverProc = await startServerWithFallback(serverScriptPath, runtimeDir);
    createWindow(dashboardPath);
  } catch (err) {
    dialog.showErrorBox("Skydiving Dashboard", String(err?.message || err));
    app.quit();
  }

  app.on("activate", () => {
    // On macOS it's common to recreate a window when the dock icon is clicked and no windows are open.
    if (BrowserWindow.getAllWindows().length === 0 && mainWindow === null) {
      const { dashboardPath } = ensureRuntimeFiles();
      createWindow(dashboardPath);
    }
  });
});