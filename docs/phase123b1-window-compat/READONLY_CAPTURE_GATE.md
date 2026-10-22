# Capture gate, separate from execution

Only READY_FOR_READONLY_CAPTURE_APPROVAL can recommend asking for C0. It requires
profile consent and exact Find X6 identity, online USB, unlocked independently
confirmed policy/display state, coherent interactive/rotation/geometry/focus,
complete known window types, non-occluding classification of every foreign base,
attributable input layers, readable controls, no manual review/EmergencyStop and
free shared lease. It still sets raw_capture_authorized=false,
physical_input_allowed=false and worker_launch_allowed=false.

This B1 result is INSUFFICIENT_EVIDENCE, not a request to execute capture now.
The diagnostic CLI does not issue PNG/XML even if --capture-settings is passed.
Physical profile remains persisted read_only_authorized=false/input_enabled=false;
metadata-only identity comparison uses an ephemeral authorized read profile.
No App/Page/Control manifest is enabled and no execution DPAPI grant exists.
Status/Resume/worker, unknown dispatch and emergency contracts remain unchanged.
The original QEMU/physical input ActionPolicy restrictions remain unchanged.

No coordinator mutation is needed for metadata diagnostics. A failed full control
read is retained as a blocking fact; it is not repaired/migrated into success.
Future C0 must establish current controls and a protected, bounded observation
session; sequential dumps can race with user/system changes. This round does not
claim capture/observation atomicity or a newly granted lease.
