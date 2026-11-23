'use strict';
const { execFileSync } = require('node:child_process');
const { win32 } = require('node:path');

// electron-builder derives this GUID from the existing appId com.nailong.deskpet.
const installKey = 'Software\\43e0bcfe-b903-5e76-846d-30de1fae56b5';
const historyKey = 'Software\\com.nailong.deskpet\\Relationship';

// Use the packaged Python interpreter to read Unicode paths through winreg.
// This runs once, without a shell, and writes an ASCII JSON result.
const registryReader = `
import json
import winreg

def read_string(hive, view, key, name):
    try:
        with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as opened:
            value, kind = winreg.QueryValueEx(opened, name)
            return value if kind == winreg.REG_SZ else None
    except OSError:
        return None

entries = []
for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        location = read_string(hive, view, ${JSON.stringify(installKey)}, "InstallLocation")
        if location:
            entries.append({
                "InstallLocation": location,
                "FirstInstalledAt": read_string(hive, view, ${JSON.stringify(historyKey)}, "FirstInstalledAt"),
            })
print(json.dumps(entries))
`;

function readInstallationRegistry(python) {
  try {
    const raw = execFileSync(python, ['-I', '-B', '-S', '-c', registryReader], {
      encoding: 'utf8', windowsHide: true, timeout: 3000, stdio: ['ignore', 'pipe', 'ignore']
    });
    const entries = JSON.parse(raw);
    return Array.isArray(entries) ? entries : [];
  } catch {
    return [];
  }
}

function findInstalledAt({ platform = process.platform, isPackaged, smokeTest,
  exe = process.execPath, python, readRegistry = () => readInstallationRegistry(python) }) {
  if (platform !== 'win32' || !isPackaged || smokeTest) return null;
  const executableDir = win32.dirname(exe).toLowerCase();
  const match = readRegistry().find(({ InstallLocation }) =>
    typeof InstallLocation === 'string' && win32.isAbsolute(InstallLocation) &&
    win32.resolve(InstallLocation).toLowerCase() === executableDir
  );
  const stamp = match?.FirstInstalledAt;
  if (typeof stamp !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(stamp)) return null;
  const parsed = new Date(stamp);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString() === stamp.replace('Z', '.000Z')
    ? stamp : null;
}

module.exports = { findInstalledAt };
