const { test } = require('node:test');
const assert = require('node:assert/strict');
const { findInstalledAt } = require('../../apps/deskpet/desktop/installation.js');

const userDir = 'C:\\Users\\桌宠\\AppData\\Local\\Programs\\奶龙桌宠';
const machineDir = 'C:\\Program Files\\奶龙桌宠';

function readInstallTime(dir, entries, flags = {}) {
  return findInstalledAt({
    platform: 'win32',
    isPackaged: true,
    smokeTest: false,
    exe: `${dir}\\奶龙桌宠.exe`,
    readRegistry: () => Object.values(entries),
    ...flags
  });
}

test('a per-user executable uses its own install timestamp even when a machine install exists', () => {
  assert.equal(readInstallTime(userDir, {
    HKCU: { InstallLocation: `${userDir.toLowerCase()}\\`, FirstInstalledAt: '2026-11-22T16:00:00Z' },
    HKLM: { InstallLocation: machineDir, FirstInstalledAt: '2026-11-25T04:12:00Z' }
  }), '2026-11-22T16:00:00Z');
});

test('a machine executable uses the shared install timestamp from a different Windows account', () => {
  assert.equal(readInstallTime(machineDir, {
    HKCU: { InstallLocation: userDir, FirstInstalledAt: '2026-11-22T16:00:00Z' },
    HKLM: { InstallLocation: machineDir, FirstInstalledAt: '2026-11-25T04:12:00Z' }
  }), '2026-11-25T04:12:00Z');
});

test('unpacked binaries cannot borrow the first installation timestamp', () => {
  assert.equal(readInstallTime('C:\\dev\\win-unpacked', {
    HKCU: { InstallLocation: userDir, FirstInstalledAt: '2026-11-22T16:00:00Z' },
    HKLM: { InstallLocation: machineDir, FirstInstalledAt: '2026-11-25T04:12:00Z' }
  }), null);
});

test('legacy and malformed markers fall back without borrowing another scope', () => {
  for (const marker of ['legacy', '2026-02-30T00:00:00Z', '2026-11-23T00:00:00+08:00', '']) {
    assert.equal(readInstallTime(userDir, {
      HKCU: { InstallLocation: userDir, FirstInstalledAt: marker },
      HKLM: { InstallLocation: userDir, FirstInstalledAt: '2026-11-25T04:12:00Z' }
    }), null);
  }
});

test('source, self-test and non-Windows launches do not read the installation registry', () => {
  for (const flags of [{ isPackaged: false }, { smokeTest: true }, { platform: 'linux' }]) {
    assert.equal(readInstallTime(userDir, {}, {
      ...flags,
      readRegistry: () => assert.fail('unexpected registry query')
    }), null);
  }
});
