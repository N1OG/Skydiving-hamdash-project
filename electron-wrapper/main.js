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

  // Node 16+ supports fs.cpSync; Node 24 definitely does.
  fs.cpSync(srcDir, dstDir, { recursive: true, force: false, errorOnExist: false });
}

function getBundledPath(rel) {
  // In packaged builds, extraResources are placed under process.resourcesPath
  // In dev, our repo root is one directory up from electron-wrapper
  if (app.isPackaged) {
    return path.join(process.resourcesPath, rel);
  }
  return path.join(app.getAppPath(), "..", rel);
}

function getRuntimeDir() {
  // Writable per-user location
  // Example: C:\Users\<you>\AppData\Roaming\Skydiving Dashboard\
  return path.join(app.getPath("userData"), "runtime");
}

/*
 * Ensure that the dashboard HTML, feed server script and configuration
 * files in the user's runtime directory are up to date with the version
 * bundled in this release.  The application stores its writable state
 * under getRuntimeDir() so that DZ selections and user profiles persist
 * between runs.  Previous versions of the app never overwrote these
 * files on upgrade, which prevented newer releases from taking effect.
 *
 * To fix this, we store the current packaged version in a file
 * (version.txt) inside the runtime directory.  When launching the app
 * we compare this recorded version against app.getVersion().  If the
 * versions differ, all runtime files managed by the installer are
 * deleted so that fresh copies from the new package are written.  Any
 * user-modified files (such as custom profiles) remain in other files
 * inside runtimeDir.
 */
function ensureRuntimeFiles() {
  const runtimeDir = getRuntimeDir();
  ensureDir(runtimeDir);

  // Path to a small file that records which version populated the runtime
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

  // Helper to remove stale runtime contents while leaving unrelated
  // user data intact.  Only remove the files that the installer manages.
  function clearManagedFiles() {
    const filesToRemove = [
      path.join(runtimeDir, "dashboard.html"),
      path.join(runtimeDir, "dz_feed_server.py"),
    ];
    const dirsToRemove = [path.join(runtimeDir, "config")];

    for (const f of filesToRemove) {
      try {
        if (exists(f)) fs.unlinkSync(f);
      } catch {
        // ignore errors
      }
    }
    for (const d of dirsToRemove) {
      try {
        if (exists(d)) fs.rmSync(d, { recursive: true, force: true });
      } catch {
        // ignore errors
      }
    }
  }

  // If this is the first run, or if the version has changed since the
  // runtime directory was populated, clear the managed files so that
  // updated versions from this release are installed.
  if (existingVersion !== currentVersion) {
    clearManagedFiles();
  }

  // Source (bundled/read-only) paths
  const srcDashboard = getBundledPath("dashboard.html");
  const srcServer = getBundledPath("dz_feed_server.py");
  const srcConfigDir = getBundledPath("config");

  // Destination (writable) paths
  const dstDashboard = path.join(runtimeDir, "dashboard.html");
  const dstServer = path.join(runtimeDir, "dz_feed_server.py");
  const dstConfigDir = path.join(runtimeDir, "config");

  // Copy only if missing so we don't blow away user changes; clearing
  // stale files above means new versions will be copied.
  copyFileIfMissing(srcDashboard, dstDashboard);
  copyFileIfMissing(srcServer, dstServer);
  copyDirIfMissing(srcConfigDir, dstConfigDir);

  // Record the version that populated these files so we can detect
  // upgrades on next run.
  try {
    fs.writeFileSync(versionFile, currentVersion);
  } catch {
    // not fatal
  }

  return {
    runtimeDir,
    dashboardPath: dstDashboard,
    serverScriptPath: dstServer,
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

      // Give it a moment to fail fast if python isn't found.
      await new Promise((r) => setTimeout(r, 900));

      if (!proc.killed && proc.exitCode === null) return proc;
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
      sandbox: true
    }
  });

  mainWindow.loadFile(dashboardPath);

  // Open the DevTools for debugging. This will show the browser console and
  // network panel which can help diagnose issues such as manifest loading
  // failures. Comment out this line to disable the DevTools in production.
  try {
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } catch (e) {
    // If devtools cannot be opened (e.g. in kiosk mode), ignore the error.
  }

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
    // Copy files to writable runtime directory (fixes DZ selection persistence)
    const { runtimeDir, dashboardPath, serverScriptPath } = ensureRuntimeFiles();

    // Start server from runtime directory so ./config/* is writable
    serverProc = await startServerWithFallback(serverScriptPath, runtimeDir);

    // Load dashboard from the same writable runtime directory
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