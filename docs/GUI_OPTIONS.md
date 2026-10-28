# GUI phase 2 — comparison only, no native implementation

| Option | Fits | Extra work / constraints |
|---|---|---|
| Theos Preference Bundle | Compact settings, on/off, status in Settings | Native build/ABI and Settings integration; multi-step onboarding is less flexible |
| Dedicated rootless app | Guided setup, diagnostics, queue/status screens | App packaging/signing, native toolchain and tested IPC/permission model |

Keep Python collection/queue/transport as the tested backend. A GUI must expose
fixed init/configure/doctor/status/enable/disable verbs, never a generic shell or
arbitrary root command. Secrets must not be argv, URLs, console logs or exported
diagnostics. A native UI does not itself grant access to LINE's sandboxed data.

The rootless native build and ABI requirements need the selected platform/toolchain
to be verified; see [Theos rootless](https://theos.dev/docs/rootless). No Preferences,
Cephei, tweak injection, signing material or third-party native library is added
to the current .deb. Decide the GUI framework after independent beta installation,
receiver idempotency and data-loss gates pass. A settings/IPC foundation would be
premature while these gates and redistribution rights remain unresolved.
