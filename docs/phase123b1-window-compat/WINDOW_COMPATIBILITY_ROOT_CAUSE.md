# Phase 1.2.3-B1 / v0.2.3

Final admission: **INSUFFICIENT_EVIDENCE**. No C0 raw capture permission and no
physical execution grant. Baseline commit 709f5db0da05d03e73e4aba1bbc860048974e199.
Both baseline artifact hashes were recalculated and matched before production edits.

The old broad `Keyguard` substring treated render-completion field names as
overlay evidence. Its boolean regex accepted `false_suffix`, and it failed to
combine display lock declarations with policy declarations. Four deterministic
tests failed on the frozen production code before modification. Original failure
logs are retained; this is offline_fixture, not a naturally observed intrusion.

The execution helper's numeric-only `ty` reader rejects native named constants.
It remains unchanged to preserve the reviewed input contract. A separate strict
native metadata reader now accepts exact known names/decimal values for read-only
assessment. It does not expand physical input. `PhysicalDeviceProbe.safety`
intersects complete native output with that assessment; it cannot accept native
output just because the legacy summary parser recognizes a few fields.

The only modified original runtime file is device_profiles.py. New runtime file:
native_windows.py. No physical_execution.py, LifeSubject, budget, lease, action
protocol, GUI model, semantic helper or supervisor changes. The old diagnostic
CLI capture flag remains recognized but returns RAW_CAPTURE_REQUIRES_C0_APPROVAL
without sending PNG/XML commands. The programmatic legacy mock summary protocol
is retained, never claims native overlay absence, and is not a C0 permit.

Live metadata diagnostics (this round) verified identity but could not verify
Settings focus. Unknown OEM window types remain present. The shared legacy SQLite
has no life_controls table; the existing full read-only health reader cannot prove
the life-control barrier. We did not migrate or reset it. No raw capture occurred.
