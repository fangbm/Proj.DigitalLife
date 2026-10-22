# Extra BASE_APPLICATION / historical metadata review

Overall classification: **INSUFFICIENT_WINDOW_EVIDENCE**. The extra retained
application window itself is classified NONDISPLAYED_WITH_INPUT_CORROBORATION
in the deidentified field-whitelist derivative; that does not admit the screen.

Historical legally retained metadata: Settings indexed window 15 is focused by
display and input, shown/ready/on-screen/requested=true, obscured=false, frame
[0,0][1240,2772]. Another Settings base (16) is inactive. Foreign base 17 has the
same full bounds/baseLayer=21000, follows Settings in the indexed WMS list, and
isVisible=true/mHasSurface=true/viewVisibility=0. However shown=false,
ready=false/onScreen=false/requested=false/lastHidden=true, with input handle
NOT_VISIBLE/NOT_FOCUSABLE/NOT_TOUCHABLE/NO_INPUT_CHANNEL and an empty touch region.
Private UID/PID association was checked without publishing identifiers. This is
a retained noncomposed surface, not proof of actual touches or a deleted history
record. A second foreign base (18) is also inactive; old short summaries omitted
some inactive windows. The complete indexed list has 21 entries.

Equal base layers cannot alone prove precise compositor Z order. We record list
relation only and rely on direct Surface plus input corroboration for the local
non-display conclusion. Ownership is WMS/Input association, not an attestation of
the application publisher. No package inventory or real process/device IDs are
published. Status/navigation/wallpaper are separately classified, never exempt
just because of SystemUI package spelling.

The original full input dump contains two Dispatcher sections. The reviewed
derivative explicitly excerpts one, reconstructs formatting and replaces private
IDs/packages consistently. It is marked real_device_metadata_deidentified, not
synthetic or a complete live admission. Unknown OEM types 2314/2315 and
unattributed input layers still prevent whole-screen trust. In the current-round
live metadata sample, Settings focus is not verified, so the historical local
finding is not reused as current permission. No multiwindow/overlay absence is
claimed beyond the stated metadata snapshot.
