# Settings enrollment progress extension

This optional component adds one line to the native GNOME Settings fingerprint
dialog:

```text
已录入 4 次 / 共 26 次 · 请完全抬起手指
```

It is a narrow `LD_PRELOAD` extension for the distribution
`gnome-control-center`. It does not replace the Settings binary, does not change
authentication, and does not read private fingerprint data. The extension only
instruments `CcFingerprintDialog`, counts `enroll-stage-passed` signals, reads
the public `num-enroll-stages` property and updates a label inserted next to the
existing enrollment message.

## Why a launcher and a diversion

`LD_PRELOAD` has to be present when Settings loads the fingerprint dialog. The
distribution desktop entry starts `/usr/bin/gnome-control-center`, so the
installer keeps the original binary under
`/usr/local/lib/liuqin-fingerprint-ui/gnome-control-center`, diverts
`/usr/bin/gnome-control-center` locally and installs a small launcher that sets
`LD_PRELOAD` and executes the saved binary. The extension clears `LD_PRELOAD`
in its constructor so helper processes do not inherit it.

This is a local integration, not an upstream GNOME extension. A distribution
update that replaces `/usr/bin/gnome-control-center` may remove the diversion;
re-run `install.sh` and verify that the progress line appears.

## Build and install

```sh
./build.sh
sudo ./install.sh
```

Then close and reopen Settings, open **System → Users → Fingerprint Login** and
start an enrollment. The counter increases only for accepted stages; retries do
not increase it.

Remove it with:

```sh
sudo ./uninstall.sh
```

## Lock-screen release

The extension also listens for `org.gnome.ScreenSaver.ActiveChanged`. When the
session locks while the fingerprint dialog is open, it closes the dialog with
the native `adw_dialog_close()`, so Settings runs its own EnrollStop/Release
path and the lock screen can claim the reader. It does not touch PAM, GDM or
Polkit.

## Limitations

- The label is hard-coded in Chinese, matching the tested desktop locale.
- The extension depends on the widget names `enroll_message` and
  `enrollment_view` and on the `CcFingerprintDialog` type name. A Settings
  redesign can make it silently do nothing.
- The settings dialog still claims the reader while it is open; close it before
  using fingerprint `sudo` or the lock screen.
- The initial total is 26 stages; the extension replaces it with the device's
  `num-enroll-stages` property when available.
