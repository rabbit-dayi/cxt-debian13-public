#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 rabbit-dayi; this license covers this script, not bundled Debian.
"""Build Debian-only, key-only public raw/ISO installers from pinned official inputs."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from urllib.parse import quote
import tempfile
import time

ROOT = Path(__file__).resolve().parent
GRUB_PACKAGES = ('grub-common', 'grub2-common', 'grub-pc-bin', 'grub-efi-amd64-bin', 'grub-efi-ia32-bin')
TOOLS = ('cpio', 'gzip', 'ldd', 'dpkg-deb', 'dpkg-query', 'depmod', 'sfdisk', 'qemu-nbd',
         'udevadm', 'mkfs.vfat', 'mount', 'umount', 'grub-install', 'grub-mkrescue',
         'grub-script-check', 'xorriso', 'mformat', 'chroot', 'bash', 'sync')


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_checked(source, target, expected):
    if not stat.S_ISREG(source.lstat().st_mode):
        raise ValueError('Input is not a regular, non-symlink file: ' + str(source))
    shutil.copyfile(source, target)
    if digest(target) != expected:
        raise ValueError('Official input SHA256 mismatch: ' + source.name)


def package_info(package):
    fields = run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}', package],
                 capture_output=True, text=True).stdout.split('\t')
    if len(fields) != 4 or not fields[1]:
        raise ValueError('Cannot determine installed package version: ' + package)
    return dict(zip(('package', 'version', 'source_package', 'source_version'), fields))


def check_nbd(device):
    if not re.fullmatch(r'/dev/nbd[0-9]+', device):
        raise ValueError('--device must be an unused whole /dev/nbdN, never a physical disk')
    path = Path(device)
    if not stat.S_ISBLK(path.lstat().st_mode):
        raise ValueError('NBD path is not a block device')
    node = Path('/sys/class/block') / path.name
    if (node / 'pid').exists() or (node / 'size').read_text().strip() != '0':
        raise ValueError('NBD device is already connected or nonempty')
    if any((node / 'holders').iterdir()):
        raise ValueError('NBD device has active holders')
    for member in [node] + [p for p in node.iterdir() if (p / 'partition').exists()]:
        if any((member / 'holders').iterdir()):
            raise ValueError('NBD partition has active holders')
        dev = (member / 'dev').read_text().strip()
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            if line.split()[2] == dev:
                raise ValueError('NBD device or partition is mounted')
    for line in Path('/proc/swaps').read_text().splitlines()[1:]:
        if re.fullmatch(re.escape(device) + r'(?:p[0-9]+)?', line.split()[0]):
            raise ValueError('NBD device backs swap')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', required=True, type=Path, help='fresh empty directory; all generated files stay here')
    parser.add_argument('--device', required=True, help='unused whole NBD device, e.g. /dev/nbd3')
    parser.add_argument('--inputs', type=Path, default=Path('/var/tmp/cxt-auto.T0ZIQVJ8/downloads'),
                        help='official cached downloads only; no customized initrd/source tree is read')
    parser.add_argument('--prepare-only', action='store_true', help='prepare hardened initrd and provenance only; do not connect NBD or build images')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('Run as root on a Debian 13 amd64 build host')
    for name in TOOLS:
        if shutil.which(name) is None:
            raise ValueError('Missing build dependency: ' + name)
    for path in ('/usr/bin/curl', '/usr/bin/ssh-keygen', '/usr/bin/od', '/etc/ssl/certs/ca-certificates.crt'):
        if not Path(path).is_file():
            raise ValueError('Missing embedded dependency: ' + path)
    grub = [package_info(name) for name in GRUB_PACKAGES]
    if len({item['version'] for item in grub}) != 1:
        raise ValueError('GRUB tools and all three platform module packages must have exactly the same version')
    manifest = json.loads((ROOT / 'inputs.json').read_text())
    kernel = manifest['kernel']
    kernel_package = package_info('linux-image-' + kernel)
    if kernel_package['version'] != manifest['kernel_package_version']:
        raise ValueError('Install the pinned official kernel package for matching copyright/source metadata')
    work = args.workdir.absolute()
    if work.is_symlink():
        raise ValueError('--workdir must not be a symlink')
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(work.iterdir()):
        raise ValueError('--workdir must be fresh and empty (including for --prepare-only)')
    work = work.resolve()
    if work == ROOT or ROOT in work.parents or work in ROOT.parents:
        raise ValueError('Keep build work outside the public source subtree')
    if not args.prepare_only:
        check_nbd(args.device)
    inputs = args.inputs.resolve(strict=True)
    downloads = work / 'downloads'
    downloads.mkdir()
    # Explicit whitelist: nothing from the old FAT/source/customized initrd or
    # old configuration/password files can enter this builder.
    evidence = {'status': 'prepared_pending_runtime_verification', 'kernel': kernel,
                'official_inputs': [], 'storage_modules': [], 'grub_packages': grub,
                'embedded_programs_and_libraries': [], 'embedded_packages': []}
    for name, entry in manifest['files'].items():
        copy_checked(inputs / name, downloads / name, entry['sha256'])
        url = (manifest['installer_base_url'] + entry['relative_url']
               if 'relative_url' in entry else manifest['storage_index_url'])
        evidence['official_inputs'].append({'file': name, 'sha256': entry['sha256'], 'source_url': url})
    sums = {}
    for line in (downloads / 'SHA256SUMS').read_text().splitlines():
        value, name = line.split(None, 1)
        sums[name.lstrip('*').removeprefix('./')] = value
    for name in ('linux', 'initrd.gz'):
        relative = 'netboot/debian-installer/amd64/' + name
        if sums.get(relative) != manifest['files'][name]['sha256']:
            raise ValueError('Pinned file does not match official installer SHA256SUMS: ' + name)
    records = {}
    for paragraph in gzip.decompress((downloads / 'storage-Packages.gz').read_bytes()).decode().split('\n\n'):
        fields = dict(line.split(': ', 1) for line in paragraph.splitlines() if ': ' in line and not line.startswith(' '))
        if 'Package' in fields:
            records[fields['Package']] = fields
    initrd = work / 'initrd'
    initrd.mkdir()
    run(['bash', '-o', 'pipefail', '-c', 'gzip -dc "$1" | cpio -idmu --quiet --no-absolute-filenames',
         'unpack-official-initrd', str(downloads / 'initrd.gz')], cwd=initrd)
    if not (initrd / 'lib').is_symlink() or (initrd / 'lib').readlink() != Path('usr/lib'):
        raise ValueError('Expected official usrmerged initrd /lib -> usr/lib')
    release = (initrd / 'etc/lsb-release').read_text()
    if '13 (trixie)' not in release or manifest['installer_build'] not in release:
        raise ValueError('Unexpected official Debian installer release')
    if {p.name for p in (initrd / 'usr/lib/modules').iterdir()} != {kernel}:
        raise ValueError('Official initrd has an unexpected kernel module version')
    for prefix in manifest['storage_modules']:
        name = prefix + '-modules-' + kernel + '-di'
        entry = records[name]
        if entry['Version'] != manifest['kernel_package_version'] or entry['Architecture'] != 'amd64':
            raise ValueError('Storage udeb version/architecture mismatch: ' + name)
        target = downloads / (name + '.udeb')
        copy_checked(inputs / target.name, target, entry['SHA256'])
        if target.stat().st_size != int(entry['Size']):
            raise ValueError('Storage udeb size mismatch: ' + name)
        package = run(['dpkg-deb', '-f', str(target), 'Package'], capture_output=True, text=True).stdout.strip()
        version = run(['dpkg-deb', '-f', str(target), 'Version'], capture_output=True, text=True).stdout.strip()
        if package != name or version != entry['Version']:
            raise ValueError('Storage udeb control metadata mismatch: ' + name)
        # A udeb has a real /lib; extracting over the usrmerged initrd corrupts
        # its /lib symlink. Unpack privately and copy *only* exact-kernel modules.
        with tempfile.TemporaryDirectory(dir=work, prefix='storage-extract.') as temp:
            run(['dpkg-deb', '-x', str(target), temp])
            modules = Path(temp) / 'lib/modules'
            if {p.name for p in modules.iterdir()} != {kernel}:
                raise ValueError('Unexpected module directory in ' + name)
            shutil.copytree(modules / kernel, initrd / 'usr/lib/modules' / kernel, dirs_exist_ok=True)
        evidence['storage_modules'].append({'package': name, 'version': version,
                                            'sha256': entry['SHA256'],
                                            'source_url': manifest['archive_base_url'] + entry['Filename']})
    run(['depmod', '-b', str(initrd), kernel])
    embedded = set()
    packages = set(GRUB_PACKAGES) | {'linux-image-' + kernel}

    def embed(path):
        source = Path(path)
        target = initrd / source.relative_to('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(initrd):
            raise ValueError('Unsafe initrd parent symlink: ' + str(target))
        if target.is_symlink():
            target.unlink()
        shutil.copy2(source, target, follow_symlinks=True)
        embedded.add(str(source))
        # This bundle is generated by update-ca-certificates, not owned as a
        # dpkg file; its constituent trust roots belong to ca-certificates.
        if source == Path('/etc/ssl/certs/ca-certificates.crt'):
            packages.add('ca-certificates')
            return
        ownership = subprocess.run(['dpkg-query', '-S', str(source.resolve())], capture_output=True, text=True)
        if ownership.returncode:
            ownership = run(['dpkg-query', '-S', str(source)], capture_output=True, text=True)
        for line in ownership.stdout.splitlines():
            owner, sep, owned = line.rpartition(': ')
            if sep and owned in (str(source), str(source.resolve())):
                packages.update(owner.split(', '))

    for binary in ('/usr/bin/curl', '/usr/bin/ssh-keygen', '/usr/bin/od'):
        embed(binary)
        output = run(['ldd', binary], capture_output=True, text=True).stdout
        if 'not found' in output:
            raise ValueError('Missing shared library for ' + binary)
        for dependency in sorted(set(re.findall(r'(/[^\s()]+)', output))):
            embed(dependency)
    embed('/etc/ssl/certs/ca-certificates.crt')
    # Retain actual Debian package copyright files and full common license texts;
    # these are component licenses, not a relicensing of the aggregate image.
    licenses = work / 'licenses'
    licenses.mkdir()
    shutil.copytree('/usr/share/common-licenses', licenses / 'common-licenses', symlinks=False)
    for package in sorted(packages):
        info = package_info(package)
        source_name = info['source_package'] or info['package'].split(':')[0]
        source_version = info['source_version'] or info['version']
        info['source_url'] = 'https://sources.debian.org/src/' + quote(source_name, safe='') + '/' + quote(source_version, safe='') + '/'
        info['copyright_url'] = info['source_url'] + 'debian/copyright/'
        evidence['embedded_packages'].append(info)
        plain = info['package'].split(':')[0]
        copyright_file = Path('/usr/share/doc') / plain / 'copyright'
        if not copyright_file.is_file():
            raise ValueError('Missing component copyright file: ' + package)
        destination = licenses / 'packages' / plain
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copyright_file, destination / 'copyright', follow_symlinks=True)
    shutil.copy2(ROOT / 'COPYRIGHT', licenses / 'COPYRIGHT')
    shutil.copy2(ROOT / 'inputs.json', licenses / 'inputs.json')
    (licenses / 'component-packages.json').write_text(json.dumps(evidence['embedded_packages'], indent=2) + '\n')
    # The stock installer has a different package set from the build host.
    # Preserve its official package metadata and publish versioned source and
    # per-source copyright locations rather than applying one blanket license.
    installer_components = []
    status_file = initrd / 'var/lib/dpkg/status'
    shutil.copy2(status_file, licenses / 'official-installer-package-status')
    for paragraph in status_file.read_text().split('\n\n'):
        fields = dict(line.split(': ', 1) for line in paragraph.splitlines() if ': ' in line and not line.startswith(' '))
        if 'Package' not in fields or 'Version' not in fields:
            continue
        archive = records.get(fields['Package'], {})
        source_field = fields.get('Source') or archive.get('Source') or fields['Package']
        source_name = source_field.split()[0]
        match = re.search(r'\(([^)]+)\)', source_field)
        source_version = match.group(1) if match else fields['Version']
        source_url = 'https://sources.debian.org/src/' + quote(source_name, safe='') + '/' + quote(source_version, safe='') + '/'
        installer_components.append({'package': fields['Package'], 'version': fields['Version'],
                                     'source_package': source_name, 'source_version': source_version,
                                     'source_url': source_url, 'copyright_url': source_url + 'debian/copyright/'})
    (licenses / 'installer-components.json').write_text(json.dumps(installer_components, indent=2) + '\n')
    shutil.copytree(licenses, initrd / 'usr/share/doc/cxt-debian13-public')
    for source in (ROOT / 'overlay').rglob('*'):
        relative = source.relative_to(ROOT / 'overlay')
        target = initrd / relative
        if source.is_symlink():
            raise ValueError('Overlay symlinks are not allowed: ' + str(relative))
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.parent.resolve().is_relative_to(initrd):
                raise ValueError('Unsafe overlay destination')
            if target.is_symlink():
                target.unlink()
            shutil.copy2(source, target)
    for name in ('early-install', 'select-mirror', 'preflight', 'preflight-lib.sh', 'read-auto', 'late-install'):
        (initrd / 'cxt' / name).chmod(0o755)
    original = initrd / 'var/lib/dpkg/info/choose-mirror.postinst'
    shutil.copy2(original, initrd / 'cxt/choose-mirror.original')
    original.write_text('#!/bin/sh\nset -e\n/cxt/early-install\nexec /cxt/choose-mirror.original "$@"\n')
    original.chmod(0o755)
    # Build-time closure checks do not access disks or networks.
    run(['chroot', str(initrd), '/usr/bin/curl', '--version'], stdout=subprocess.DEVNULL)
    packed = work / 'debian13-public-initrd.gz'
    with packed.open('xb') as output:
        run(['bash', '-o', 'pipefail', '-c',
             'find . -print0 | LC_ALL=C sort -z | cpio --null -o -H newc --quiet | gzip -9'], cwd=initrd, stdout=output)
    evidence['embedded_programs_and_libraries'] = sorted(embedded)
    evidence['installer_release'] = release
    evidence['hardened_initrd_sha256'] = digest(packed)
    evidence['detached_signature_verified_at_original_acquisition'] = False
    evidence['original_legacy_payload_included'] = False
    evidence['secure_boot'] = 'unsupported: unsigned GRUB'
    evidence['ia32_efi'] = 'built, runtime verification pending'
    report = work / 'PUBLIC-BUILD.json'
    report.write_text(json.dumps(evidence, indent=2) + '\n')
    if args.prepare_only:
        print('PREPARED', packed)
        return
    check_nbd(args.device)
    image = work / 'CXT-Debian13-Public.raw'
    with image.open('xb') as output:
        output.truncate(1073741824 + 4096)
    run(['sfdisk', str(image)], input='label: dos\nunit: sectors\n\nstart=2048, size=2095104, type=ef, bootable\n',
        text=True, stdout=subprocess.DEVNULL)
    mount = work / 'target'
    mount.mkdir()
    connected = mounted = False
    try:
        run(['qemu-nbd', '--format=raw', '--connect=' + args.device, str(image)])
        connected = True
        run(['udevadm', 'settle'])
        partition = args.device + 'p1'
        deadline = time.monotonic() + 15
        while not Path(partition).exists():
            if time.monotonic() > deadline:
                raise TimeoutError('NBD partition did not appear: ' + partition)
            time.sleep(0.1)
        run(['udevadm', 'settle'])
        run(['mkfs.vfat', '-a', '-F', '32', '-h', '2048', '-n', 'CXTDEB13', partition], stdout=subprocess.DEVNULL)
        run(['mount', partition, str(mount)])
        mounted = True
        (mount / 'Sources/Debian').mkdir(parents=True)
        (mount / 'grub').mkdir()
        (mount / 'CXT-DEBIAN13-BOOT').write_text('Unofficial CXT Debian 13 Public key-only installer\n')
        shutil.copy2(downloads / 'linux', mount / 'Sources/Debian/vmlinuz')
        shutil.copy2(packed, mount / 'Sources/Debian/initrd.gz')
        shutil.copy2(ROOT / 'grub.cfg', mount / 'grub/grub.cfg')
        shutil.copy2(ROOT / 'README.md', mount / 'README.md')
        shutil.copytree(licenses, mount / 'licenses')
        run(['grub-install', '--target=i386-pc', '--boot-directory=' + str(mount), '--no-floppy', args.device])
        for platform in ('x86_64-efi', 'i386-efi'):
            run(['grub-install', '--target=' + platform, '--boot-directory=' + str(mount), '--efi-directory=' + str(mount),
                 '--removable', '--no-nvram', '--no-uefi-secure-boot'])
        run(['grub-script-check', str(mount / 'grub/grub.cfg')])
        run(['sync', '-f', str(mount)])
    finally:
        try:
            if mounted:
                run(['umount', str(mount)])
        finally:
            if connected:
                run(['qemu-nbd', '--disconnect', args.device])
    stage = work / 'iso-root'
    (stage / 'boot/grub').mkdir(parents=True)
    (stage / 'Sources/Debian').mkdir(parents=True)
    shutil.copy2(downloads / 'linux', stage / 'Sources/Debian/vmlinuz')
    shutil.copy2(packed, stage / 'Sources/Debian/initrd.gz')
    shutil.copy2(ROOT / 'grub.cfg', stage / 'boot/grub/grub.cfg')
    (stage / 'CXT-DEBIAN13-BOOT').write_text('Unofficial CXT Debian 13 Public optical installer\n')
    shutil.copy2(ROOT / 'README.md', stage / 'README.md')
    shutil.copytree(licenses, stage / 'licenses')
    run(['grub-script-check', str(stage / 'boot/grub/grub.cfg')])
    iso = work / 'CXT-Debian13-Public.iso'
    run(['grub-mkrescue', '--output=' + str(iso), '--compress=xz', str(stage), '-volid', 'CXTDEB13ISO'])
    evidence['status'] = 'built_pending_runtime_verification'
    evidence['outputs'] = [{'file': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path)} for path in (image, iso)]
    report.write_text(json.dumps(evidence, indent=2) + '\n')
    (work / 'SHA256SUMS-Public').write_text(''.join(item['sha256'] + '  ' + item['file'] + '\n' for item in evidence['outputs']))
    print('PUBLIC_IMAGES_BUILT', image, iso)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit('Public build refused/failed: ' + str(error))
