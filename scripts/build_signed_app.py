"""Build with a stable local signing identity so Keychain grants survive updates.

Only certificate identities are inspected; private keys stay in macOS Keychain.
Set MERIDIAN_SIGNING_IDENTITY explicitly when multiple development identities exist.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    identities = subprocess.check_output(["security", "find-identity", "-v", "-p", "codesigning"], text=True)
    valid = re.findall(r'\b([A-F0-9]{40}) "([^"]+)"', identities)
    saved = ROOT / ".local-data/signing-identity.json"
    identity = os.environ.get("MERIDIAN_SIGNING_IDENTITY")
    if not identity and saved.exists():
        identity = json.loads(saved.read_text())["identity"]
    if not identity:
        development = [name for _, name in valid if name.startswith("Apple Development:")]
        if len(development) == 1:
            identity = development[0]
    if not identity or not any(identity in (fingerprint, name) for fingerprint, name in valid):
        sys.exit("A stable Apple code-signing identity is required. Set MERIDIAN_SIGNING_IDENTITY to an installed valid certificate; ad-hoc signing would reset Keychain trust.")
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps({"identity": identity}) + "\n")
    saved.chmod(0o600)
    config = json.dumps({"bundle": {"macOS": {"signingIdentity": identity}}})
    subprocess.run([str(ROOT / "node_modules/.bin/tauri"), "build", "--config", config, *sys.argv[1:]],
                   cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
