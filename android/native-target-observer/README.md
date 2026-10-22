# Fixed-target observer: offline prototype only

Not integrated with PhysicalRegistry/PhysicalPhone, not a permission issuer,
and never deployed by the build. Build with SDK 36/JDK 17 using `build.ps1
-OutputDirectory <new artifact directory>`; it does not invoke ADB.

One nonce, one compiled Settings selector, two native samples, then disconnect.
The historical child indices bind selection, not future tap coordinates.
Any path/title/owner mismatch fails without search or retry. Output contains
native flags/bounds/window metadata only, no complete tree or UI strings.
The exact expected stock Settings title is compared only after package,
resource/class and non-password/non-editor checks.

The shell UiAutomation connection uses hidden framework APIs. SDK compilation
does not establish ColorOS compatibility or shell permission availability.
Requesting FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES avoids the ordinary
wrapper's default suppression; incompatibility fails, never falls back.
UiAutomation itself temporarily registers a content-reading test service and
can affect accessibility/window state. AOSP disconnect also restores rotation
policy. Those lifecycle effects require explicit operator review/approval.
No APK install, permanent service, settings toggle or permission grant is coded.

4.5-second process watchdog and a 300ms handler-thread join bound cleanup. Host
request/response bounds, exact device binding, shared lease, output limits and
fixed transport allowlist must be added to the human diagnostic launcher before
any approved execution. The prototype alone cannot prove SPY policy or provide
a two-second whole-executor guarantee. It grants no GUI capability.
