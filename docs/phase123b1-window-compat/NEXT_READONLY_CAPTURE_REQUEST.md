# Deferred C0 scope; DO NOT EXECUTE

Current status INSUFFICIENT_EVIDENCE. Resolve unknown OEM types, current Settings
focus and complete read-only control evidence first. No immediate capture is
recommended and this document does not ask to bypass those blockers.

After a fresh trusted gate and separate user approval, the exact candidate raw
commands would be (the full identity-bound USB serial is private):

```
adb -s <bound-private-serial> exec-out screencap -p
adb -s <bound-private-serial> exec-out uiautomator dump /dev/tty
```

Registered pre/post metadata commands: get-state; shell getprop for the eight
IDENTITY_PROPERTIES; shell wm size; shell wm density; shell dumpsys power;
shell dumpsys window policy; shell dumpsys window windows; shell dumpsys window
displays; shell dumpsys input. No shell input, am start, keyevent, swipes, settings
write, install, broadcasts or model API. User manually maintains Settings.

Data would include private raw PNG screen pixels and UI XML text/labels/bounds/
structure, possibly device/account/notification content. Store in new owner-only
local directory; no upload/publication. PNG/XML can expose secrets even on a
Settings page. uiautomator may start a native instrumentation process and fail;
that is not proven side-effect-free. No custom helper deployment or permission
grant is proposed. XML failure must be explicit and must not trigger an install.
Shared lease, identity, safety and freshness must be rechecked; cancel on any
change. Approval must specify capture scope; a readiness label alone is no grant.
