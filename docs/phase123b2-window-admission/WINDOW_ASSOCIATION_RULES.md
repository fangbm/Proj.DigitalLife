# Phase 1.2.3-B2 / Window, Surface, Input

The B2 API is a non-granting metadata review. B1 assess_native and its original
test assertions remain strict. No known OEM type map is fabricated. Exact
IS_WALLPAPER is recognized from AOSP InputConfig; it is not a global window grant.

WindowState.getName() uses the WindowState identity plus its tag; its input handle
is assigned this name. Match that identity with exact UID/PID/display, not title,
UID alone, process alone, token alone or array position. Every numeric input
handle ID must be present and unique. Same-UID windows and same-PID Surfaces are
distinct. Duplicate names, duplicate Surface objects, owner conflict or missing
Surface identity for an allocated Surface deny. Surface ownership is the bounded
WindowStateAnimator child record; no independent SurfaceFlinger dump was obtained.
Surface parent and compositor Z remain UNKNOWN. Input frame is OS-reported data,
not an atomic independent screenshot.

One or two complete Dispatcher sections are parsed independently. All relevant
fields must agree, including owner, ID, channel, region, flags, alpha, geometry
and focus. Extra/truncated or contradictory sections deny; do not choose one.
Two complete samples must then share the same safety projection within 8 seconds.
Raw timestamps/dump headers are audit hashes, not safety truth. Changed focus,
object identity, bounds, visibility, channel, touch region or layer state denies.
This is bounded agreement, never a system-level atomic observation guarantee.

An unknown-type instance is dormant only with no Surface, shown/onScreen/visible/
ready all false, nonvisible view, no matching input handle, and independently
noninteractive flags or no-focus plus no unattributed active channel in its owner
process. This is discarded on the next sample; requestedVisible=true is retained,
not silently changed. Unknown visible or input-channel-bearing instances deny.
Other unattributed active layers still block the whole graph even if a particular
unknown window is dormant. Hidden wallpapers with active channels are not assumed
harmless. A system bar needs its known assigned type, exact owner association,
edge bounds and matching shown/alpha/input state; SystemUI spelling alone grants
nothing. Transparent navigation is explicit alpha=0 in WMS and input, with empty
touch region. Any display/input conflict denies.

References (primary):
- https://android.googlesource.com/platform/frameworks/base/+/android16-release/services/core/java/com/android/server/wm/WindowState.java
- https://android.googlesource.com/platform/frameworks/base/+/android16-release/services/core/java/com/android/server/wm/WindowStateAnimator.java
- https://android.googlesource.com/platform/frameworks/native/+/android16-release/libs/input/android/os/InputConfig.aidl
- https://android.googlesource.com/platform/frameworks/native/+/refs/heads/master/libs/gui/include/gui/WindowInfo.h
