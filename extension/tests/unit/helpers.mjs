import { FakeBridge, closedPort } from "./fake-bridge.mjs";
import { connectionIdle, setOwnDigest } from "../../src/shared/connection.ts";
import { installChrome } from "./chrome-mock.mjs";

/** A fresh mock browser + any number of fake bridges; candidate ports are pinned so a real Meridian is never contacted. */
export async function world({ bridges = [{}], paired = null, chromeOptions = {} } = {}) {
  globalThis.__E2E__ = true;
  setOwnDigest(""); // this build carries no stamp in the tests: the digest comparison is off unless a test turns it on
  const chrome = installChrome(chromeOptions);
  const servers = bridges.map((options) => new FakeBridge(options));
  for (const server of servers) await server.start();
  const ports = servers.map((server) => server.port);
  await chrome.storage.local.set({ "meridian.e2e.ports": ports });
  let issued = null;
  if (paired !== null) {
    const owner = servers[paired.on ?? 0];
    issued = owner.issueClient();
    await chrome.storage.local.set({
      "meridian.connection": {
        port: paired.port ?? owner.port, clientId: issued.clientId, token: issued.token, clientName: "Google Chrome", pairedAt: "2026-01-01T00:00:00.000Z",
      },
    });
  }
  return {
    chrome, servers, bridge: servers[0], ports, issued,
    async close() {
      await connectionIdle(); // a check still running would otherwise commit its result into the NEXT test's storage
      for (const server of servers) await server.stop().catch(() => undefined);
      globalThis.__E2E__ = false;
    },
    async closedPort() {
      return closedPort();
    },
  };
}

export const authedRequests = (server) => server.log.filter((entry) => entry.auth !== null);
