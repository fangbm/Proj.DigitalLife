# life_controls 缺失的准确根因

The B1 private diagnostic called readonly_db_health(root, root, device='real').
The first parameter means agent/life/audit DB; the second is shared coordinator.
data/phone-life.sqlite3 is core schema 12 with lease and legacy audit tables,
not an initialized life agent. Store._migrate creates core tables, not life_controls.
enable_life explicitly creates life schema 1 and life_controls in the agent DB.
The finite session control DB separately owns session/health_events/exported_cycles.
It also is not supposed to contain life_controls. Thus the B1 diagnostic error
was a role/path mistake, not proof that the coordinator needed a migration.

Existing production Status, Resume, Supervisor and Worker already use
cfg.agent_db for life controls/audits/budget and cfg.lease_path for shared leases.
SessionLedger/marker supply session stop state. physical_session_admission then
adds the physical contract and live checks. Those production modules need no
change in B2. None were changed. A correct-role temporary fixture proves that
a legacy coordinator without life_controls is acceptable only in coordinator
role. A real agent missing life_controls still fails; errors never become safe.

New readonly_sources.inspect_coordinator and read_bound_sources make roles
explicit in the review path. They use mode=ro/query_only, finite busy/progress
bounds, supported schemas, exact session/config/path/binding identities, marker
overlay and existing safety_admission. A physical control file copied elsewhere
cannot evade the original configured marker path. Unknown legacy action audits
are additionally inspected, never silently dropped by an inner join. This extra
legacy provenance check does not claim that existing Worker reads arbitrary
coordinator history: Worker reads its configured copied agent audit. All selected
history sources must be established before using the non-granting review result.

Current live limitation: the read-only inventory found 14 existing session
control records, zero physical bindings, zero matching this Find X6 identity.
There is no selected current physical agent/control record to read. The shared
coordinator is readable and has zero active real-device leases/unknown dispatches,
but that cannot manufacture missing current control/life facts. B2 returns
CURRENT_CONTROL_OR_AGENT_SOURCE_UNBOUND. No empty substitute, table creation,
source deletion, marker clearing, migration or implicit emulator-to-phone reuse.
This does not imply a need to start an Agent or grant execution for observation:
a future explicitly reviewed observation-source binding can be scoped separately.
B2 does not invent or create it under this round's constraints.
