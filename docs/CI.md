# Builds and Automation

## Kernel Changes

The kernel repository's `Liuqin kernel` workflow builds pushes and pull requests
to `liuqin-6.17`. It checks out the integration repository's `main` and uses its
shared `.github/actions/kernel` action. No cross-repository write token is needed.
Both repositories must exist under the same GitHub owner before enabling this flow.

The integration repository's `Kernel build` workflow uses `kernel/source.json`.
Accept a kernel update by changing that commit: the configuration and packaging
remain in the integration repository. Kernel development artifacts record their
tested SHA separately and cannot enter image assembly as product inputs. The one
exception is a development build of the very commit that `kernel/source.json`
pins now, with the pinned configuration hash and byte-identical fragments: it was
built from the same inputs as a product build, and image assembly accepts it
and says so.

Kernel jobs use GitHub-hosted Ubuntu runners and ccache. They upload the Image,
DTB, configuration, symbols, matching module package inputs and checksums as
Actions artifacts. Compilation is not a device test or an automatic release.

## System Images

`tools/build-liuqin-image.py` is the local and CI assembly entry point. It consumes
a completed product kernel build plus prepared inputs; it does not rebuild every
third-party dependency. `--stage` resumes an interrupted assembly without repeating
successful stages. Do not modify sources or inputs while an assembly is running.

The `System image` workflow is manually dispatched on `main` and uploads the
matching boot image, installer RAM image, rootfs, installer and checksums.
It requires a dedicated Linux x86-64 runner labeled `liuqin-images`, sufficient
disk space for kernel outputs and two desktop trees, and the host dependencies
listed in BUILD. Python also needs pyelftools; image assembly needs root, GNU tar,
ACL/xattr support, squashfs-tools, cpio, ARM64 binfmt and the cross compiler.
The runner must allow noninteractive execution of the trusted assembly command
with sudo. Do not run external pull requests on this privileged runner.
After configuring the runner, set repository variable `LIUQIN_IMAGE_CI=enabled`
to also assemble images automatically when relevant code changes reach `main`.

Set `LIUQIN_IMAGE_INPUTS` to a runner-local JSON file (default
`/opt/liuqin/inputs.json`). It contains the prepared-input variables listed by
`tools/build-liuqin-image.py`: Ubuntu root and manifest, firmware pool and prepared
tree, audio topology, WLAN set, stock DTB/DTBO inputs, sensor archive, Settings
binary and its build manifest, BusyBox and mkbootimg. Hash values remain pinned;
the local paths and raw input file are not uploaded with artifacts. Upstream
downloads and prebuilt components may be cached outside the checkout.

Example on a prepared host:

```sh
python3 tools/build-liuqin-kernel.py
sudo python3 tools/build-liuqin-image.py \
  --inputs /opt/liuqin/inputs.json --kernel-out "$PWD/out/kernel"
python3 tools/install-liuqin.py --bundle out/image/bundle --check
```

The workflow does not create a public Release or mark a bundle device-tested.
Full-image CI requires a configured dedicated runner. Test installation on the
final candidate before marking a bundle device-tested; report Android recovery
separately and do not claim it is validated by a successful Ubuntu installation.

## Public Delivery

Installation versions belong in tagged Releases of the integration repository,
not another repository, the source Git tree or Git LFS. Actions artifacts are
temporary build results, not supported installation releases.

GitHub [limits each Release asset to under 2 GiB](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).
The release export stage splits the desktop archive into parts below that limit
without rebuilding the images. Upload every file in `out/image/release-assets/`.
Users join the parts as described in the bundled installation instructions;
the installer verifies the joined archive before installation. See
[Release assets](BUILD.en.md#release-assets) for the export command.

Required board firmware is included in the assembled system and boot images.
A matching firmware package may also be attached for builders, without another
firmware repository. Small, stable, generic firmware binaries may also be tracked
under `device/firmware/` with their provenance, hashes and applicable notices;
this is an allowed layout, not a claim that all firmware is already imported.
Keep one authoritative copy and avoid repeatedly committing large binary sets.
Upstream Ubuntu images, unmodified upstream components and
full stock ROMs are not mirrored here. Per-device calibration and addresses are
always obtained from the user's own tablet and are never Release assets.
