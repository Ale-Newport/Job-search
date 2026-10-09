"""Recovery controls and API reconnection on synthetic browser fixtures only.

Requires the local Vite server. API routes for this test are mocked; no job site is opened.
"""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright, expect


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page(viewport={"width": 1360, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        state = {"run": "needs_review", "status": "NEEDS_REVIEW", "error": None}
        actions = []

        async def application_route(route):
            action = route.request.url.rsplit("/", 1)[-1]
            if route.request.method == "POST":
                actions.append(action)
                state.update(run="running" if action == "apply" else "error", error=None)
                await route.fulfill(json={"status": state["run"]})
                return
            if state.get("slow"):
                await asyncio.sleep(3.2)
            await route.fulfill(json={
                "id": "recovery-fixture", "job_id": "fixture-job", "mode": "review",
                "status": state["status"], "created_at": "2026-10-04T12:00:00Z",
                "job": {"company": "Local Fixture", "title": "Recovery Test"},
                "events": [], "answers": [], "tasks": [], "documents": [], "emails": [],
                "runs": [{"id": "run-fixture", "status": state["run"], "error": state["error"]}],
            })

        await page.route("**/api/applications/recovery-fixture**", application_route)
        await page.goto("http://127.0.0.1:1420")
        await page.evaluate("""async () => {
            const {default: React} = await import('/node_modules/.vite/deps/react.js');
            const {default: ReactDOM} = await import('/node_modules/.vite/deps/react-dom_client.js');
            const {ApplicationDetail} = await import('/src/details.tsx');
            document.getElementById('root').style.display = 'none';
            const mount = document.createElement('main'); document.body.append(mount);
            mount.id = 'recovery-fixture'; mount.style.padding = '32px';
            function Fixture() {
                const [refresh, setRefresh] = React.useState(0);
                const [busy, setBusy] = React.useState(null);
                const ctx = {refresh, busy, select: () => {}, navigate: () => {}, toast: () => {},
                    act: async (name, action) => {setBusy(name); try {return await action();}
                        finally {setBusy(null); setRefresh(v => v + 1);}}};
                window.refreshFixture = () => setRefresh(v => v + 1);
                return React.createElement(ApplicationDetail, {id:'recovery-fixture', ctx});
            }
            ReactDOM.createRoot(mount).render(React.createElement(Fixture));
        }""")
        fixture = page.locator("#recovery-fixture")
        await expect(fixture.get_by_role("button", name="Review & approve submission")).to_be_visible()
        # A slow poll must complete, rather than being aborted by the next poll.
        state.update(run="error", status="ERROR", error="Chrome closed. Retry Auto Apply to reopen the form.", slow=True)
        await expect(fixture.get_by_role("button", name="Retry Auto Apply")).to_be_visible(timeout=10000)
        state["slow"] = False
        await expect(fixture.get_by_text(state["error"], exact=True)).to_be_visible()
        await fixture.get_by_role("button", name="Retry Auto Apply").click()
        await expect(fixture.get_by_role("button", name="Stop attempt")).to_be_visible()
        await expect(fixture.get_by_role("button", name="Resume agent")).to_be_disabled()
        await fixture.get_by_role("button", name="Stop attempt").click()
        await expect(fixture.get_by_role("button", name="Retry Auto Apply")).to_be_visible()
        assert actions == ["apply", "cancel"], actions
        state.update(run="unconfirmed", status="NEEDS_REVIEW", error="Submission outcome is uncertain.")
        await page.evaluate("window.refreshFixture()")
        await expect(fixture.get_by_role("button", name="Reconcile submission")).to_be_visible()
        await expect(fixture.get_by_role("button", name="Resume agent")).to_be_disabled()
        await expect(fixture.get_by_role("button", name="Retry Auto Apply")).to_have_count(0)
        state.update(run="confirmed", status="CONFIRMED", error=None)
        await page.evaluate("window.refreshFixture()")
        await expect(fixture.get_by_role("button", name="Resume agent")).to_have_count(0)

        result = await page.evaluate("""async () => {
            const {api} = await import('/src/api.ts?recovery-test');
            const originalFetch = window.fetch, originalTimer = window.setTimeout;
            let invokes = 0, requests = 0;
            window.__TAURI_INTERNALS__ = {invoke: async () => {
                invokes++; return {base_url:'/fixture', token:'synthetic-token'};
            }};
            try {
                window.fetch = async () => {requests++; throw new TypeError('connection lost');};
                await api('/mutation', 'POST', {}).then(() => {throw new Error('Expected failure');}, () => {});
                if (requests !== 1 || invokes !== 1) throw new Error('Mutation was replayed');
                window.fetch = async () => new Response(JSON.stringify({ok:true}));
                if (!(await api('/health')).ok || invokes !== 2) throw new Error('Connection was not refreshed');
                window.setTimeout = (fn, ms, ...args) => originalTimer(fn, [15000,90000].includes(ms) ? 5 : ms, ...args);
                window.fetch = () => new Promise(() => {});
                const message = await api('/hung').then(() => '', e => e.message);
                if (!message.includes('took too long')) throw new Error('Read deadline did not report recovery');
                const mutationMessage = await api('/hung', 'POST', {}).then(() => '', e => e.message);
                if (!mutationMessage.includes('Check the latest status')) throw new Error('Unknown mutation outcome lost');
                const abort = new AbortController(); abort.abort();
                const name = await api('/aborted', 'GET', undefined, abort.signal).then(() => '', e => e.name);
                if (name !== 'AbortError') throw new Error('Caller cancellation was not preserved');
                const {api: nativeApi} = await import('/src/api.ts?native-error-test');
                window.__TAURI_INTERNALS__.invoke = async () => {throw 'Wait a minute and retry';};
                const nativeMessage = await nativeApi('/health').then(() => '', e => e.message);
                if (nativeMessage !== 'Wait a minute and retry') throw new Error('Native error was hidden');
                return {reconnect:true, noMutationReplay:true, boundedRequests:true};
            } finally {
                window.fetch = originalFetch; window.setTimeout = originalTimer;
                delete window.__TAURI_INTERNALS__;
            }
        }""")
        output = Path("/tmp/meridian-ui-qa")
        output.mkdir(exist_ok=True)
        await page.screenshot(path=str(output / "recovery-controls.png"), full_page=True)
        assert not errors, errors
        print(json.dumps({"result": "passed", "controls": actions, **result}))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
