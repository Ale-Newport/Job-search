"""Loopback bridge between the Meridian sidecar and the Chrome/Safari assistant extension.

See docs/EXTENSION_DESIGN.md. The bridge is a second, fixed-port listener with its own pairing
credential; it never reuses the per-launch bearer token of the desktop API.
"""

PROTOCOL = 1
# Ordered candidate ports the extension probes. A conflict falls through to the next free one.
BRIDGE_PORTS = tuple(range(47615, 47625))
# The Chrome Web Store build, if one is ever published, gets its own id; extend through
# MERIDIAN_EXTENSION_IDS (comma separated) instead of editing this tuple.
CHROME_EXTENSION_IDS = ("ophngbnbiekjhemmabbpildjedfphlbc",)
