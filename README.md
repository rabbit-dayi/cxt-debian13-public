# CXT Debian 13 Public — unofficial, SSH-key-only modification

**Thanks to CXT / CXTHHHHH and the original project:**
<https://www.cxthhhhh.com/network-reinstall-system-modify> ·
<https://github.com/MeowLove/Network-Reinstall-System-Modify>.
This is an **unofficial modification**, not an official CXT or Debian release.
It is independently assembled from official Debian components, not a redistribution
of the old CXT tools collection. Upstream default-login instructions do **not**
apply to this public image.

Public repository: <https://github.com/rabbit-dayi/cxt-debian13-public>.

## Download and install

Download `CXT-Debian13-Public.raw.gz`, `CXT-Debian13-Public.iso`,
`configure-auto.py`, and `SHA256SUMS` from the [GitHub Release](https://github.com/rabbit-dayi/cxt-debian13-public/releases/latest).
Release assets also include reviewed source, component license notices, build
provenance, and `VALIDATION.json` with the exact tested scope.

For DD, run the following in **bash from a rescue environment**, after backing
up the target disk. Replace both placeholders; this erases the entire target.

```bash
set -euo pipefail
sha256sum --check --ignore-missing SHA256SUMS
DISK=/dev/YOUR_TARGET_DISK
gzip -dc CXT-Debian13-Public.raw.gz | sudo dd of="$DISK" bs=4M status=progress conv=fsync
sudo python3 configure-auto.py --device "$DISK" --region cn \
  --github YOUR_GITHUB_USERNAME --confirm ERASE
sync
# Reboot from this disk only after successful write/readback confirmation.
```

Use `--region global` for Debian official mirrors or `--region auto` for country
selection. Omitting the configuration step leaves interactive key/disk approval;
the configured example explicitly authorizes automatic whole-disk installation
at next boot. **There is no password fallback: you must own a matching private
key for the selected GitHub account.**

For ISO, mount it as a CD/DVD or server virtual medium and boot it; do not gunzip
it or run the raw-disk configuration helper on the ISO. The default flow asks for
GitHub keys and erase authorization. The serial menu is COM1 at 115200 baud.
Disable unsigned Secure Boot and detach the ISO after installation.

## What this installs

- Debian 13 trixie amd64, SSH server and the standard utilities task, using the
  official Debian installer kernel `6.12.107+deb13-amd64` and installer build
  `20250803+deb13u7`.
- No ordinary user account. Root's password is locked with a lock marker, not a
  password hash. **There is no bootstrap/default password and no password login.**
- `PermitRootLogin prohibit-password`, `PasswordAuthentication no`,
  `KbdInteractiveAuthentication no`, `PubkeyAuthentication yes`, and `UsePAM yes`.
- Root SSH keys are fetched from `https://github.com/USERNAME.keys` over
  certificate-validated HTTPS, with HTTPS-only redirects. Every key is parsed by
  `ssh-keygen`; an empty account, invalid key, certificate, key option, failed
  request, or oversized response blocks authorization. Interactive installation
  shows fingerprints and asks you to trust them before selecting a disk.
- **You must supply your own GitHub username. None is embedded or preselected.**
  Key retrieval cannot be bypassed: the legacy `skip` value is refused by the
  host configuration helper, on-disk slot parser, kernel-argument handling, and
  key validation. Failed retrieval never becomes successful authorization.
- The raw image and ISO contain the same hardened initrd and Debian-only boot
  menu. Neither includes original CXT tools/archives, old ReadMe/password lists,
  a legacy menu, installed-system backups, private keys, or embedded credentials.

These are **network installers**, not ready-installed operating-system backups.
Internet connectivity and working DHCP (or interactive manual network setup) are
required. Firmware support depends on Debian's installer and hardware.

## Destructive-install safety

**Installation repartitions and erases the ENTIRE selected disk**, not just one
partition. Back up data separately and test only on disposable virtual disks
first. The downloaded image itself does not select a target disk or authorize
erasure. Writing the raw image onto a disk is already destructive to that disk;
booting it and authorizing installation replaces the whole disk again.

The interactive flow is: configure network → validate mirrors → enter GitHub
username → validate/trust SSH key fingerprints → select an eligible whole disk →
type `ERASE`. Mounted, read-only, and active-swap backing disks are excluded. The
final exact disk is checked again immediately before partitioning authorization.
The installer verifies that `/target` belongs to that disk before completing.
It is not a hardware write-protection mechanism; use isolated VMs for evaluation.

The raw format deliberately retains the existing slot protocol and geometry:

| Item | Value |
| --- | --- |
| Raw size | 1,073,745,920 bytes (1 GiB + 4096 bytes) |
| Partition table | MBR; one bootable type `0xef` partition only |
| FAT32 start / length | 2048 / 2,095,104 sectors, 512 bytes per sector |
| FAT label | `CXTDEB13` |
| Configuration slot | byte offset 1,073,741,824; exactly 4096 bytes |

The slot header remains `CXT-AUTO-V1`. A valid slot binds the install target to
**the whole disk containing that same verified MBR/FAT image**; it cannot choose
a different disk by name. Multiple configured disks, malformed CXT slots,
changed configuration, and mixed disk-slot plus `cxt.*` kernel authorization
sources block automatic installation. An all-zero slot requests interactive
installation. The slot contains only region, GitHub username, and explicit erase
authorization; it is never sourced as shell code. It is not encrypted, signed, or
a protection against somebody who already controls the disk or boot arguments.

To authorize a **disposable unmounted raw image copy** (not an ISO):

```sh
python3 configure-auto.py --image /path/to/disposable-copy.raw \
  --region cn --github YOUR_GITHUB_USERNAME --confirm ERASE
```

To authorize an unmounted whole disk containing this raw image, use `--device`
instead of `--image`, as root. The helper rejects symlinks, partitions, mounted
or in-use targets, wrong layouts, and old images lacking the full slot. It writes
only the slot, fsyncs and reads it back; it does not reboot or install anything.
**At the next boot, valid authorization allows the entire source disk to be erased
without another interactive confirmation.** Verify device identity externally.

To cancel automatic authorization before boot:

```sh
python3 configure-auto.py --image /path/to/disposable-copy.raw --clear --confirm ERASE
```

Clearing does not undo earlier image writes or installation. For block targets,
use `--device` and root privileges. A read-only ISO cannot carry this writable
raw-disk slot; it normally uses the interactive disk/key confirmation flow.

Advanced disposable-VM boot tests may supply `cxt.region=auto|cn|global`,
`cxt.github=YOUR_GITHUB_USERNAME`, `cxt.disk=/dev/VM_DISK`, and
`cxt.confirm=ERASE`. This explicitly authorizes the named disk and bypasses the
interactive fingerprint/disk confirmations, so only use a trusted account and a
known VM device. Do not mix these authorization arguments with a configured raw
disk slot. The production boot menu contains no account, target, or erase flag.

## Mirrors and trust

`cn` tries CERNET then USTC. `global` uses `deb.debian.org`. `auto` consults
`https://api.country.is/`; CN uses domestic candidates, other reported countries
use Debian, and an unavailable country hint tries CERNET → USTC → Debian.
The country request exposes the installer's ordinary public source IP to that
service but sends no account, token, or credentials; its response is discarded.

Mirror probes require verified HTTPS, exact Debian trixie/amd64 release metadata,
and matching SHA256/size plus valid installer package indexes. A failed probe is
not silently accepted. Security indexes are probed separately; unsupported mirror
security paths retain official HTTPS `security.debian.org` with no false claim
that it was reachable. Debian installer/APT archive-signature checks remain
mandatory. Mirrors or time/CA failures stop progress with diagnostic evidence;
there is no `--insecure`, `trusted=yes`, or unauthenticated archive fallback.

## Key-only recovery and operational limits

Before installation, confirm that the selected GitHub account publishes SSH keys
and that you possess a corresponding private key. Compare displayed SHA256
fingerprints through a trusted independent channel. Any owner of a private key
published on that account will gain root access. Automated authorization trusts
the named account without an interactive fingerprint question. No GitHub token,
GitHub password, TLS client certificate, or private key is required or accepted.

After reboot, connect as root using your matching private key. Updating keys on
GitHub later does **not** rotate the installed file automatically. Maintain
`/root/.ssh/authorized_keys` yourself and test a second working session before
removing a key. Restrict SSH at the provider firewall and keep Debian updated.
Never put a private SSH key into this repository, an image, or an installer log.

Keep provider console/rescue access. If GitHub/network/CA validation fails before
erasure, correct connectivity/time/trust or choose an account with valid keys;
do not disable verification. If the private key is lost after erasure, use the
provider rescue environment to mount the installed root, install a new public
key with root ownership and modes `0700` for `.ssh` / `0600` for
`authorized_keys`, and validate SSH configuration. There is no password fallback.
Do not unlock root or enable password authentication as an undocumented bypass.

A failed late hook means the disk may already be erased: completion/reboot is
blocked until the lock-only shadow state, no ordinary user, nonempty installed
keys, SSH syntax/effective key-only policy, and boot checks pass. Diagnose from
the installer console. Post-install records are under
`/var/log/installer/cxt/` and `/var/lib/cxt-install/complete`. Do not publish raw
installer logs without reviewing hostnames, network addresses, and public key
identities. Never publish shadow or debconf password databases.

Boot loaders are provided for BIOS, x64 UEFI and IA32 UEFI. **Secure Boot is not
supported** (unsigned GRUB); disable Secure Boot or use a properly signed
alternative. IA32 EFI inclusion is not a claim of an IA32 runtime test. The OS is
amd64, not i386 or ARM. The text boot menu supports screen and COM1 115200 serial
console and does not depend on graphics modules/themes.

## Rebuild (root, Debian 13 amd64)

All modification source is in this directory: `build-public.py`, `configure-auto.py`,
`grub.cfg`, `inputs.json`, and `overlay/`. No private customized initrd, password
file, original CXT archive, metadata pointer, or old FAT contents is read.

Host dependencies: Python 3.11+; `cpio`, `gzip`, `kmod`, `util-linux`, `qemu-utils`,
`udev`, `dosfstools`, `curl`, `openssh-client`, `coreutils`, `ca-certificates`,
`libc-bin`, `dpkg`, `xorriso`, `mtools`, `grub-common`, `grub2-common`,
`grub-pc-bin`, `grub-efi-amd64-bin`, `grub-efi-ia32-bin`, and the official
`linux-image-6.12.107+deb13-amd64=6.12.107-1` package for copyright/source metadata.
All five GRUB packages must have **exactly the same Debian version**. Configure
NBD partition support before building (for example load the NBD module with
`max_part=8` if it is not already configured). Never disconnect an occupied NBD.

Supply an existing cache of **official downloads only** using `--inputs DIR`.
The local development default is `/var/tmp/cxt-auto.T0ZIQVJ8/downloads`; only the
following whitelist is ever copied:

- Official `linux`, official **unmodified** `initrd.gz`, installer `SHA256SUMS`, and
  official `storage-Packages.gz`, all pinned by `inputs.json`.
- For kernel `6.12.107+deb13-amd64`, the eight
  `NAME-modules-6.12.107+deb13-amd64-di.udeb` files for `scsi-core`, `cdrom-core`,
  `scsi`, `ata`, `sata`, `pata`, `usb`, and `usb-storage`. Digests, size and exact
  package/version are verified against the pinned official Packages index.

Official installer/source URLs and integrity provenance are in `inputs.json` and
`COPYRIGHT`. The original cache was checked against HTTPS-fetched official
SHA256SUMS/Packages; detached Debian signatures were **not** independently checked
at acquisition. The pins preserve those inputs, not retrospective signature
verification. Debian's live `current` URLs and Packages indexes change: obtain
matching historical official inputs/snapshot copies rather than changing pins
or mixing a new kernel with old modules. The builder does not download or run
anything from the original CXT collection.

Choose a fresh empty work directory and a verified unused `/dev/nbdN`, then:

```sh
sudo python3 ./build-public.py --workdir /var/tmp/cxt-public-build \
  --device /dev/nbd3 --inputs /path/to/official-downloads
```

`/dev/nbd3` above is only an example; check that it is unused first. The script
refuses physical disks, connected/nonempty NBDs, and nonempty work directories.
It creates its own new raw file, attaches only that file, formats only its first
NBD partition, and unmounts/disconnects its attachment on exit. It never changes
NVRAM. Failure leaves the work directory for diagnosis; do not reuse it as fresh.

`--prepare-only` prepares and packages the hardened initrd, licenses and
provenance without connecting NBD or producing raw/ISO images. It still accepts
`--device` for a consistent CLI, but does not touch it. A subsequent complete
build requires another fresh work directory; there is no hidden resume mode.

Generated outputs in the chosen work directory:

- `CXT-Debian13-Public.raw` (1 GiB + 4096 bytes, initially zero authorization slot)
- `CXT-Debian13-Public.iso` (BIOS/EFI optical installer, same hardened initrd)
- `debian13-public-initrd.gz`, `PUBLIC-BUILD.json`, `SHA256SUMS-Public`
- `licenses/` with actual package copyrights, full common license texts,
  component/source manifests, and original installer package status.

Exact-kernel storage modules are extracted into temporary directories and **only
modules** are merged, preserving `/lib -> usr/lib`. `curl`, `ssh-keygen`, `od`,
their shared-library/loader closure, and CA trust are embedded. The raw FAT is
populated from scratch with Debian payload, same-version GRUB and notices only;
the ISO uses the identical kernel/initrd. Build output hashes are not boot tests.

## Verification status and evidence-ready release checklist

**Verified public build:** a fresh BIOS/domestic-mirror installation completed
without hook replay, rebooted, and accepted real root public-key SSH. Root was
locked; a real password login was rejected; effective SSH policy was key-only.
A temporary test public key was added only in installer RAM after actual GitHub
key validation, then removed over SSH; the original GitHub fingerprints matched.
It is absent from production images. The unmodified production ISO was booted
through both BIOS and x64 UEFI to the required GitHub prompt with unchanged blank
targets (serial and domestic-region boot arguments only). A real initrd boot
rejected legacy `github=skip` authorization. Image/config inspection passed;
raw/ISO initrd bytes are identical and the shipped slot is zero.

Full UEFI installation, overseas-mirror installation, IA32 EFI, physical hardware
and Secure Boot were **not** validated for this public build. No private-image
test is counted as public-image proof. `VALIDATION.json` in the Release records
the exact scope; raw installer logs and private test credentials are not shipped.

Before publishing either binary, retain an evidence record for each item:

1. Record pinned input hashes, exact kernel/module and GRUB versions, build output
   SHA256/size, build command, and tools. Confirm the initrd is unpacked from the
   official download, `/lib` is still a symlink, required exact-kernel storage
   modules/dependencies/CA are present, and raw/ISO initrd hashes are identical.
2. Inspect the final raw MBR/FAT geometry and the zeroed 4096-byte slot. Inventory
   the complete raw FAT and ISO trees. Confirm no original CXT tools/archives,
   old ReadMe/password lists, legacy menu, backup, credentials, password hash,
   private key, default GitHub identity, or unexpected payload is present. Retain
   copyrights, common licenses and corresponding-source manifests with releases.
3. Exercise the public helper on disposable raw copies only: valid configure /
   clear round trips; rejection of malformed names, the reserved legacy bypass
   value, wrong layouts, symlinks, partitions, mounted/in-use disks and images.
   Confirm all writes stay within the slot and never reboot.
4. Boot production BIOS/raw and x64 UEFI/ISO to the GitHub username prompt, without
   erase authorization. Capture the text/serial prompt and prove disks remain
   untouched. Check both screen/serial menu paths as applicable. IA32 and Secure
   Boot must retain their explicit limitations unless separately tested.
5. Negative tests before partitioning: a manually written legacy `github=skip`
   slot, `cxt.github=skip`, missing/empty/malformed keys, HTTPS failure, ambiguous
   configured disks, wrong slot layout, and mixed authorization sources must not
   authorize disk erasure. Never weaken the shipped policy to test it.
6. Perform a complete BIOS domestic-mirror install onto an isolated disposable
   disk with a real GitHub account whose public keys validate. For automation
   only, a separate ephemeral test public key may be injected into the VM's
   installer RAM after that real validation; never add it to shipped source or
   artifacts, and remove it through the authenticated SSH session afterward.
   Record that distinction; do not claim the tester owned the GitHub private key.
7. Reboot the installed system. Prove root public-key SSH works, password and
   keyboard-interactive SSH are denied, root's shadow field is a hash-free lock
   marker (report only the Boolean result, never the field), no ordinary user
   exists, installed keys are nonempty, effective SSH settings match, and the
   completion/mirror evidence exists. Remove any RAM-only injected test key and
   record successful removal. Never print credentials, shadow fields or hashes.
8. A prepartition UEFI boot is **not** a full UEFI install/reboot/SSH test; domestic
   success is **not** an overseas mirror install; a stopped run is **not** a pass.
   State exactly what was built, booted, installed, rebooted and authenticated.
9. Publish only a reviewed source allowlist plus final checked binary assets and
   sanitized evidence. Exclude work directories, original caches, private image
   trees, SSH fixture private keys, temporary authorization slots and raw logs.
   Both README and release notes must retain the CXT thanks and unofficial label.

## Licensing

This is an aggregation of separately licensed official Debian/GNU/Linux and other
components; **no single project license is claimed for the complete image**.
`COPYING` applies to the modification source only. `COPYRIGHT` explains upstream
credits, component copyright locations, versioned Debian source access, and
corresponding-source obligations. Preserve component notices and source access
when redistributing binaries; do not imply upstream endorsement.
