# Explicit lock-state contract

`native_windows.lock_state(policy, displays)` reads exact mShowingLockscreen,
mKeyguardShowing, isKeyguardShowing, keyguardShowing declarations. Generic
showing/mIsShowing are accepted only inside the indented KeyguardServiceDelegate
section and stop at a peer section. Accepted values are exactly true and false;
prefixes, strings, numbers, invalid tokens and missing state never mean unlocked.
Any positive state wins. Conflicts/invalid declarations remain explicit.

mKeyguardDrawComplete is render completion, not lock state. true and false both
leave lock state unchanged. A visible KEYGUARD/KEYGUARD_DIALOG window is evaluated
independently and denied. Interactive=true never proves unlocked or overlay-free.
Interactive/wakefulness conflicts produce unknown. Complete native admission
requires trusted service headers plus independent unlocked policy/display state,
complete windows, focus, geometry, Surface and Input Dispatcher evidence.

Legacy parse_safety supports the existing AOSP minimal summary and ColorOS
delegate syntax, but overlay_absence_proven stays false. Complete native safety
cannot fall back to that summary on malformed or unknown native content.
Source binding is an explicit versioned dumpsys grammar, not device attestation.
