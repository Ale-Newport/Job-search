from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..browser import PROGRESS, is_submit


@dataclass
class ATSAdapter:
    name: str = "generic"
    domains: tuple[str, ...] = ()
    manual_only: bool = False
    aliases: dict[str, str] = field(default_factory=dict)
    capability: str = "Semantic HTML forms; unsupported widgets require human takeover."

    def matches(self, url: str, snapshot: dict | None = None) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in self.domains)

    def question(self, element: dict) -> str:
        if element.get("role") == "checkbox" and element.get("context") and element["context"] != element["label"]:
            return element["context"] + " — " + element["label"]
        if element.get("role") == "radio":
            return element.get("context") or element.get("group") or element["label"]
        return self.aliases.get(element.get("name", ""), element["label"])

    def next_button(self, snapshot: dict) -> dict | None:
        buttons = [e for e in snapshot["elements"] if "CLICK" in e["operations"] and
                   e.get("role") == "button" and PROGRESS.match(e["label"].strip()) and not is_submit(e)]
        return buttons[0] if len(buttons) == 1 else None

    def submit_button(self, snapshot: dict) -> dict | None:
        buttons = [e for e in snapshot["elements"] if "CLICK" in e["operations"] and is_submit(e)]
        return buttons[0] if len(buttons) == 1 else None

    def describe(self) -> dict:
        return {"name": self.name, "domains": list(self.domains), "manual_only": self.manual_only,
                "capability": self.capability}
