# Native window trust rules

Window order is indexed and complete; repeated IDs, malformed headers, duplicate
primary attributes, missing type, invalid booleans/bounds or missing footer reject.
Primary mAttrs and nested rotation mAttrs are distinct contexts. Known type is
necessary, never sufficient. Owner package alone does not grant trust.

Display focus, any repeated WMS focus, obscuring window and Input Dispatcher focus
must agree with a full-screen, actually shown, unobscured Settings application.
Input owner UID, display, frame and alpha must agree. Separate fields are retained:
view visibility, hasSurface, shown, ready, requested, onScreen, isVisible, lastHidden.
In particular isVisible=true does not prove composition when shown=false.

A foreign retained application surface requires shown=false, onScreen=false,
ready=false, requested=false, lastHidden=true plus a matching NOT_VISIBLE,
NOT_TOUCHABLE, NOT_FOCUSABLE input handle. Alternatively, no surface and all
non-display fields false plus no handle in a complete list proves inactivity.
Missing/conflicting evidence rejects. Overlap and same base layer are recorded;
baseLayer alone cannot resolve equal-layer Z order. Indexed relation is reported
as list order, not falsely promoted to a measured compositor Z coordinate.

Only tightly bounded status/navigation bars from SystemUI with matching trusted,
nonfocusable system-owned handles can pass. Full-screen bars, visible foreign
apps, keyguard, IME, permission dialogs, toast and overlays reject. Unknown OEM
types reject even without a surface. Unattributed visible input layers, including
OEM spy monitors, remain unproven. Wallpaper is not a blanket exception: it must
be demonstrably non-displayed/non-input by the same evidence rules.

Two Input Dispatcher sections are ambiguous in this grammar and reject; we do
not silently choose the convenient section. No parser result authorizes input.
