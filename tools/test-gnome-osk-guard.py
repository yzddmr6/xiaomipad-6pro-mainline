#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the GNOME Shell OSK guard extension against the real maybeHandleEvent() body.

The method is copied at run time from the js/ui/keyboard.js of a verified
gnome-shell 50 source tree (--keyboard-js); Clutter and Main are replaced by
small stand-ins whose contains() rejects null the way the Clutter binding does.
The stand-in modules are served at resource:///org/gnome/shell through GLib's
G_RESOURCE_OVERLAYS, so the extension file is imported unchanged. This checks
the JavaScript logic only, not a running GNOME Shell.
"""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

EXTENSION = (Path(__file__).resolve().parents[1] / 'device/gnome-overlay/usr/share/gnome-shell/'
             'extensions/liuqin-osk-guard@io.github.liuqin/extension.js')

HARNESS = r'''
const [extensionPath] = ARGV;

const NULL_ERROR = 'Argument descendant may not be null';
const keyboardBox = {
    contains(actor) {
        if (actor === null)
            throw new Error(NULL_ERROR);
        return actor.inKeyboard === true;
    },
};
globalThis.__oskTest = {Main: {layoutManager: {keyboardBox}}};
let current = null;
globalThis.global = {stage: {get_event_actor: () => current}};

function check(condition, what) {
    if (!condition)
        throw new Error(`FAILED: ${what}`);
    print(`ok: ${what}`);
}

function thrown(fn) {
    try {
        fn();
    } catch (e) {
        return e.message;
    }
    return null;
}

const {KeyboardManager} = await import('resource:///org/gnome/shell/ui/keyboard.js');
const {default: OskGuard} = await import(`file://${extensionPath}`);
const original = KeyboardManager.prototype.maybeHandleEvent;
const manager = new KeyboardManager();
const event = {};

check(thrown(() => manager.maybeHandleEvent(event)) === NULL_ERROR,
    'unpatched method throws on a null event actor');

const extension = new OskGuard({uuid: 'liuqin-osk-guard@io.github.liuqin'});
extension.enable();
current = null;
check(manager.maybeHandleEvent(event) === false, 'guarded method returns false for a null actor');

const key = {inKeyboard: true, received: [], event(e, capture) {
    this.received.push([e, capture]);
}};
current = key;
check(manager.maybeHandleEvent(event) === true &&
      key.received.length === 2 && key.received[0][1] === true && key.received[1][1] === false,
    'an OSK key event is still forwarded (capture, then bubble)');

current = {inKeyboard: false};
check(manager.maybeHandleEvent(event) === false, 'an event outside the keyboard is not handled');

manager._keyboard = null;
current = null;
check(manager.maybeHandleEvent(event) === false, 'no keyboard object still returns false');
manager._keyboard = {};

extension.disable();
check(KeyboardManager.prototype.maybeHandleEvent === original, 'disable() restores the original method');
check(thrown(() => manager.maybeHandleEvent(event)) === NULL_ERROR, 'original behaviour returns after disable()');
print('PASS');
'''


def method_source(keyboard_js):
    lines = keyboard_js.read_text().splitlines()
    start = lines.index('    maybeHandleEvent(event) {')
    end = lines.index('    }', start)
    body = lines[start:end + 1]
    if not any('keyboardBox.contains(actor)' in line for line in body):
        raise SystemExit('maybeHandleEvent() does not have the expected 50.x shape')
    return '\n'.join(body)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--keyboard-js', type=Path, required=True, help='js/ui/keyboard.js of the verified source')
    ap.add_argument('--gjs', default='gjs', help='gjs command, for example via qemu-aarch64 -L <root>')
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stubs = root / 'stubs'
        (stubs / 'extensions').mkdir(parents=True)
        (stubs / 'ui').mkdir()
        (stubs / 'extensions/extension.js').write_text(
            'export class Extension {\n    constructor(metadata) {\n        this.metadata = metadata;\n    }\n}\n')
        (stubs / 'ui/keyboard.js').write_text(
            'const Main = globalThis.__oskTest.Main;\n\nexport class KeyboardManager {\n'
            '    constructor() {\n        this._keyboard = {};\n    }\n\n' + method_source(a.keyboard_js) + '\n}\n')
        harness = root / 'harness.js'
        harness.write_text(HARNESS)
        env = dict(os.environ, G_RESOURCE_OVERLAYS=f'/org/gnome/shell={stubs}')
        run = subprocess.run(shlex.split(a.gjs) + ['-m', str(harness), str(EXTENSION)],
                             capture_output=True, text=True, env=env)
        sys.stdout.write(run.stdout)
        sys.stderr.write(run.stderr)
        if run.returncode or not run.stdout.rstrip().endswith('PASS'):
            raise SystemExit(f'gjs harness failed ({run.returncode})')


if __name__ == '__main__':
    main()
