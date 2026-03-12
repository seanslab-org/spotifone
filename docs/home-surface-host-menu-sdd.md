# Spotifone SDD — Home Surface Host Menu

Date: 2026-03-12

## Goal

Turn the idle Spotifone splash into the primary home surface for host selection, while preserving the existing settings/host-management overlay toggled by the mute button.

## Requirements

- Keep the Spotifone logo/branding at the top of the screen.
- Show the sentence: `Bluetooth mic + keyboard. no setup, just talk.`
- Render a purple divider line beneath the header.
- Below the divider, render a menu of remembered Bluetooth hosts.
- Bright rows indicate hosts that are currently live or already connected.
- Grey rows indicate remembered hosts that are not currently live.
- Tapping a row attempts to switch/connect Spotifone to that host.

## Source Of Truth

- "Ever connected" will use remembered BlueZ hosts from `/var/lib/bluetooth/<adapter>/<device>/info`.
- This intentionally tracks durable host memory already owned by BlueZ.
- We will not introduce a second persistent history store unless the product later needs hosts retained after explicit removal/unpair.

## Liveness Model

- Connected hosts are always considered live.
- Non-connected hosts are considered live when they appear during a short Bluetooth discovery pass.
- The UI will periodically refresh remembered hosts and probe liveness in the background.
- If discovery is unavailable or fails, the system falls back to connected-only liveness rather than blocking the UI.

## Screen Model

- Default state: home surface visible.
- Header:
  - brand title
  - requested sentence
  - purple divider line
- Body:
  - remembered hosts in a 2-column icon grid
  - each tile shows a device-style icon plus host name
  - MAC addresses are not rendered on the home surface
  - bright/live tiles use normal text color
  - offline tiles use muted text color
- Footer/status:
  - transient connection/status messages
- Existing mute-button overlay:
  - remains available as a separate settings surface
  - keeps scan/delete/about functions

## Interaction

- Tap bright or muted host tile on the home surface:
  - try `Device1.Connect`
  - fall back to the existing `bluetoothctl`-based connect/pair/trust/connect flow when needed
  - show transient status text such as `connecting...`, `connected`, or `connect failed`
- Mute button:
  - still toggles the existing settings overlay
- Touch while settings overlay is open:
  - continues to use the existing overlay interaction model

## Constraints

- Do not change existing PTT/HID button behavior.
- Keep changes localized to `src/menu_ui.py` unless a helper test requires a new unit test file.
- Avoid adding a new runtime dependency.

## Failure Modes

- No adapter address:
  - show empty host list with status fallback
- Discovery fails:
  - remembered hosts still render
  - liveness falls back to connected-only
- Connection attempt fails:
  - show a short error/status message and keep the user on the home surface

## Rollback

- Revert the home-surface renderer to the previous `logo.fb` splash-only idle screen.
- Leave the existing settings overlay code path intact so rollback is low-risk and localized.

## Verification

- Unit tests:
  - remembered host state mapping
  - liveness merge behavior
  - connected hosts remain bright even if discovery does not report them
- Manual on-device checks:
  - boot lands on home host menu
  - live host tile is bright
  - offline host tile is muted
  - tapping a tile attempts switch/connect
  - mute button still opens the settings overlay
