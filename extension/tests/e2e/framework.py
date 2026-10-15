"""Scenario registry, the per-scenario environment (``Env``) and result bookkeeping for the extension e2e suite."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from playwright.async_api import Page, Playwright

import harness
import servers
from backend import RealBackend, make_dir, temp_root
from fake_llm import FakeLLM
from harness import Rig

DEFAULT_TIMEOUT = 90


# ---------------------------------------------------------------------------------------------- registry
@dataclass
class Scenario:
    name: str
    fn: Callable[["Env"], Awaitable[None]]
    needs: tuple[str, ...] = ()
    timeout: int = DEFAULT_TIMEOUT
    xfail: str | None = None  # known not to pass yet for a documented reason; flips to a plain pass when it does
    doc: str = ""


SCENARIOS: dict[str, Scenario] = {}


def scenario(*, needs: tuple[str, ...] = (), timeout: int = DEFAULT_TIMEOUT, xfail: str | None = None):
    """Register ``async def scenario_<name>(env)``. ``needs`` are capabilities (harness.capabilities) the checkout must
    provide; without them the scenario is reported BLOCKED (not failed) and names the workstream that owns the gap."""

    def register(fn):
        name = fn.__name__.removeprefix("scenario_")
        SCENARIOS[name] = Scenario(name, fn, needs, timeout, xfail, (fn.__doc__ or "").strip())
        return fn

    return register


FINDINGS: list[str] = []


def finding(label: str, detail: str = "") -> None:
    """A KNOWN product gap seen while a scenario ran (an xfail row of expectations.py). The scenario continues, the
    summary lists it, and when the product starts meeting the row the scenario says so."""
    text = f"{label}" + (f": {detail}" if detail else "")
    print(f"    KNOWN ISSUE {text}", flush=True)
    if text not in FINDINGS:
        FINDINGS.append(text)


class Skip(Exception):
    """Raised by a scenario that cannot run on this machine (reason in the message); reported, never a failure."""


def check(label: str, condition: object, detail: object = "") -> None:
    """A named assertion: prints PASS, raises AssertionError(label + detail) on failure."""
    ok = bool(condition)
    print(("    PASS " if ok else "    FAIL ") + label + (f"   [{detail}]" if not ok and detail != "" else ""), flush=True)
    if not ok:
        raise AssertionError(f"{label}" + (f" [{detail}]" if detail != "" else ""))


# ---------------------------------------------------------------------------------------------- environment
class Env:
    """Everything one scenario may use. Servers start lazily; ``close`` tears everything down in reverse order."""

    def __init__(self, p: Playwright, args, scenario_name: str) -> None:
        self.p, self.args, self.name = p, args, scenario_name
        self.fixtures = servers.FixtureServers()
        self._company: servers.CompanySite | None = None
        self._llm: FakeLLM | None = None
        self.backends: list[RealBackend] = []
        self.rigs: list[Rig] = []
        self.squatters: list[servers.Squatter] = []
        self.proxies: list[servers.DelayProxy] = []

    # ------------------------------------------------------------------ shared servers
    @property
    def company(self) -> servers.CompanySite:
        if self._company is None:
            self._company = servers.CompanySite()
            self.fixtures.tokens["COMPANY_SITE"] = self._company.origin
        return self._company

    @property
    def llm(self) -> FakeLLM:
        if self._llm is None:
            self._llm = FakeLLM().start()
        return self._llm

    @property
    def backend(self) -> RealBackend:
        assert self.backends, "no backend started in this scenario"
        return self.backends[-1]

    @property
    def rig(self) -> Rig:
        assert self.rigs, "no browser started in this scenario"
        return self.rigs[-1]

    # ------------------------------------------------------------------ backend
    async def start_backend(self, *, bridge: bool = True, seed_profile: bool = True, llm: bool = True,
                            research: bool = False, api_seed: bool = True, extra_env: dict | None = None,
                            data_dir=None) -> RealBackend:
        """The real backend on a temp data dir, seeded through the real API (and the fake loopback writing model)."""
        env = dict(extra_env or {})
        if research:
            env.update(self.company.research_hosts_env())
        backend = RealBackend(bridge=bridge, seed_profile=seed_profile, llm=self.llm if llm else None,
                              extra_env=env, data_dir=data_dir)
        self.backends.append(backend)
        await backend.start()
        if seed_profile and api_seed:
            await backend.seed_via_api()
        elif llm:
            await backend.configure_llm()
        if bridge:
            # The native code-entry dialog must never appear on the tester's Mac; the in-app approval is used instead.
            await backend.settings(extension_pairing_dialog=False)
        return backend

    # ------------------------------------------------------------------ browser
    async def start_rig(self, *, variant: str = "e2e", profile: Path | None = None,
                        ports: list[int] | None = None) -> Rig:
        rig = await harness.start_rig(self.p, variant=variant, recipe=self.args.recipe,
                                      headless=not self.args.headed, profile=profile)
        self.rigs.append(rig)
        if ports is None and self.backends and self.backends[-1].bridge_port:
            ports = [self.backends[-1].bridge_port]
        if ports and variant.startswith("e2e"):
            await rig.set_bridge_ports(ports)
        return rig

    async def connected(self, *, variant: str = "e2e", profile: Path | None = None, backend_args: dict | None = None,
                        ) -> Rig:
        """Backend + browser, already paired (through the real bridge protocol) so the scenario starts connected."""
        if not self.backends:
            await self.start_backend(**(backend_args or {}))
        rig = await self.start_rig(variant=variant, profile=profile)
        await harness.pair_directly(rig, self.backend)
        return rig

    async def open(self, name: str, query: str = "", *, page: Page | None = None, rig: Rig | None = None,
                   activate: bool = False) -> Page:
        rig = rig or self.rig
        page = page or rig.page
        await page.goto(self.fixtures.url(name, query), wait_until="domcontentloaded")
        if activate:
            await rig.activate(page)
        return page

    async def open_tab(self, name: str, query: str = "", *, rig: Rig | None = None, activate: bool = True) -> Page:
        """A FRESH tab on a fixture (activation is per tab: after a same-origin navigation the worker re-activates the
        tab by itself, and a second toolbar click would toggle the assistant off)."""
        rig = rig or self.rig
        page = await rig.new_page()
        await page.bring_to_front()
        await page.goto(self.fixtures.url(name, query), wait_until="domcontentloaded")
        if activate:
            await rig.activate(page)
        return page

    def squatter(self, port: int = 0, **kwargs) -> servers.Squatter:
        squatter = servers.Squatter(port, **kwargs)
        self.squatters.append(squatter)
        return squatter

    async def delay_proxy(self, upstream_port: int, delay: float = 3.0) -> servers.DelayProxy:
        proxy = await servers.DelayProxy(upstream_port, delay).start()
        self.proxies.append(proxy)
        return proxy

    # ------------------------------------------------------------------ teardown
    async def failure_artifacts(self, error: BaseException) -> list[str]:
        rig = self.rigs[-1] if self.rigs else None
        backend = self.backends[-1] if self.backends else None
        saved = await harness.save_failure(self.name, rig, backend, error)
        if self.squatters or self.proxies:  # what the impostors were asked (tokens redacted), for scenarios about them
            import json

            folder = harness.failure_dir() / re.sub(r"[^A-Za-z0-9_.-]", "_", self.name)
            folder.mkdir(parents=True, exist_ok=True)
            report = {"squatters": [{"port": sq.port, "proof": sq.proof, "requests": [
                {"method": r["method"], "path": r["path"], "auth": "authorization" in {k.lower() for k in r["headers"]}}
                for r in sq.requests]} for sq in self.squatters],
                "proxies": [{"port": px.port, "connections": px.connections, "credentialed": len(px.credentialed)} for px in self.proxies]}
            (folder / "impostors.json").write_text(json.dumps(report, indent=1))
        return saved

    async def close(self) -> None:
        for rig in reversed(self.rigs):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(rig.close(), 20)
        for backend in reversed(self.backends):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(backend.stop(), 25)
        for squatter in self.squatters:
            squatter.close()
        for proxy in self.proxies:
            proxy.close()
        self.fixtures.close()
        if self._company is not None:
            self._company.close()
        if self._llm is not None:
            self._llm.stop()
        self.rigs.clear()
        self.backends.clear()


# ---------------------------------------------------------------------------------------------- results
PASS, FAIL, BLOCKED, XFAIL, XPASS, SKIP = "pass", "fail", "blocked", "xfail", "xpass", "skip"


@dataclass
class Result:
    name: str
    status: str
    seconds: float = 0.0
    detail: str = ""
    artifacts: list[str] = field(default_factory=list)
    iteration: int = 1


def write_junit(path: Path, results: list[Result]) -> None:
    suite = ET.Element("testsuite", name="meridian-extension-e2e", tests=str(len(results)),
                       failures=str(sum(r.status in {FAIL, XPASS} for r in results)),
                       skipped=str(sum(r.status in {BLOCKED, XFAIL, SKIP} for r in results)),
                       time=f"{sum(r.seconds for r in results):.2f}")
    for result in results:
        case = ET.SubElement(suite, "testcase", classname="extension_e2e", name=result.name, time=f"{result.seconds:.2f}")
        if result.status == FAIL:
            ET.SubElement(case, "failure", message=result.detail[:300]).text = result.detail
        elif result.status == XPASS:
            ET.SubElement(case, "failure", message="expected to fail but passed: remove its xfail").text = result.detail
        elif result.status in {BLOCKED, XFAIL, SKIP}:
            ET.SubElement(case, "skipped", message=result.detail[:300])
        if result.artifacts:
            ET.SubElement(case, "system-out").text = "\n".join(result.artifacts)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


# ---------------------------------------------------------------------------------------------- leftovers
def reap_leftovers() -> list[str]:
    """Kill any process whose command line still points into this run's temp directory (browsers, backends, shims)
    and report them: the suite must leave nothing running."""
    root = str(temp_root())
    found: list[str] = []
    try:
        listing = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return found
    for line in listing.splitlines():
        match = re.match(r"\s*(\d+)\s+(.*)", line)
        if not match or root not in match.group(2) or str(os.getpid()) == match.group(1):
            continue
        found.append(f"{match.group(1)} {match.group(2)[:120]}")
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(int(match.group(1)), signal.SIGKILL)
    return found


def now() -> float:
    return time.monotonic()


__all__ = [
    "Scenario", "SCENARIOS", "scenario", "check", "Env", "Result", "PASS", "FAIL", "BLOCKED", "XFAIL", "XPASS", "SKIP",
    "Skip", "FINDINGS", "finding",
    "write_junit", "reap_leftovers", "make_dir", "now",
]
