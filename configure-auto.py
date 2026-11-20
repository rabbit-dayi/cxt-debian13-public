#!/usr/bin/env python3
"""Write CXT unattended-install authorization, or clear it, without rebooting.

WARNING: a configured image erases its ENTIRE SOURCE DISK at the next boot.
This helper writes only the reserved 4096-byte slot, not the boot partition.
Use --image only for an explicit disposable, unmounted raw image file.
"""
import argparse
import fcntl
import os
from pathlib import Path
import re
import stat
import struct
import sys

SLOT_OFFSET = 1073741824
SLOT_SIZE = 4096
MIN_SIZE = SLOT_OFFSET + SLOT_SIZE
PART_START = 2048
PART_SECTORS = 2095104
SYS_BLOCK = Path('/sys/class/block')
BLKGETSIZE64 = 0x80081272
BLKROGET = 0x125e


def github_name(value):
    if value.lower() == 'skip' or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', value) or '--' in value:
        raise argparse.ArgumentTypeError('a GitHub account with SSH keys is required: 1-39 letters/digits, single internal hyphens')
    return value


def proc_path(value):
    """Decode the kernel octal escapes used in mountinfo and /proc/swaps."""
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)


def uses_disk(node, disk, seen=None):
    node = node.resolve(strict=True)
    if node == disk or disk in node.parents:
        return True
    seen = set() if seen is None else seen
    if node in seen:
        raise ValueError('cyclic sysfs block dependencies')
    seen.add(node)
    slaves = node / 'slaves'
    return any(uses_disk(slave, disk, seen.copy()) for slave in slaves.iterdir()) if slaves.is_dir() else False


def check_block_safety(info):
    link = Path('/sys/dev/block') / f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
    node = link.resolve(strict=True)
    if not (node / 'dev').is_file() or (node / 'partition').exists():
        raise ValueError('target must be a whole block disk, not a partition')
    if re.match(r'(?:loop|ram|zram|sr|fd|dm-|md)', node.name):
        raise ValueError('target is not a supported physical/whole disk')
    if (node / 'ro').read_text().strip() != '0':
        raise ValueError('target is read-only')
    members = [node] + [child for child in node.iterdir() if (child / 'partition').exists()]
    for member in members:
        if any((member / 'holders').iterdir()):
            raise ValueError('target or a partition has active stacked-device holders')
    with open('/proc/self/mountinfo', encoding='ascii') as mounts:
        for line in mounts:
            fields = line.split()
            mounted = Path('/sys/dev/block') / fields[2]
            if mounted.exists() and uses_disk(mounted, node):
                raise ValueError('target backs a mounted filesystem')
    with open('/proc/swaps', encoding='ascii') as swaps:
        next(swaps)
        for line in swaps:
            swap = Path(proc_path(line.split()[0]))
            swap_info = swap.stat()
            dev = swap_info.st_rdev if stat.S_ISBLK(swap_info.st_mode) else swap_info.st_dev
            active = Path('/sys/dev/block') / f'{os.major(dev)}:{os.minor(dev)}'
            if active.exists() and uses_disk(active, node):
                raise ValueError('target backs active swap')


def check_image_safety(path, info):
    # Refuse even an unmounted loop claim: its consumer may write concurrently.
    # Match both canonical paths and inode identity (hard-link aliases included).
    real = path.resolve(strict=True)
    for block in SYS_BLOCK.iterdir():
        backing = block / 'loop/backing_file'
        if not backing.exists():
            continue
        name = proc_path(backing.read_text().rstrip('\n'))
        if not name:
            continue
        claimed = Path(name)
        if not claimed.is_absolute():
            claimed = Path('/') / claimed
        if claimed.resolve() == real:
            raise ValueError('raw image is claimed by a loop device')
        try:
            claimed_info = claimed.stat()
        except FileNotFoundError:
            continue  # Host backing files can be outside this mount namespace.
        if (claimed_info.st_dev, claimed_info.st_ino) == (info.st_dev, info.st_ino):
            raise ValueError('raw image inode is claimed by a loop device')
    with open('/proc/swaps', encoding='ascii') as swaps:
        next(swaps)
        for line in swaps:
            active = Path(proc_path(line.split()[0])).stat()
            if (active.st_dev, active.st_ino) == (info.st_dev, info.st_ino):
                raise ValueError('raw image is active swap')
    # Covers direct file bind mounts, not just normal loop-mounted filesystems.
    with open('/proc/self/mountinfo', encoding='ascii') as mounts:
        for line in mounts:
            mounted = Path(proc_path(line.split()[4]))
            try:
                active = mounted.stat()
            except FileNotFoundError:
                continue
            if (active.st_dev, active.st_ino) == (info.st_dev, info.st_ino):
                raise ValueError('raw image is mounted')


def read_exact(fd, count, offset):
    result = bytearray()
    while len(result) < count:
        part = os.pread(fd, count - len(result), offset + len(result))
        if not part:
            raise ValueError('short read from target')
        result.extend(part)
    return bytes(result)


def check_identity(fd, size):
    if size < MIN_SIZE:
        raise ValueError(f'target must be at least {MIN_SIZE} bytes; old 1-GiB images have no slot')
    mbr = read_exact(fd, 512, 0)
    first = mbr[446:462]
    if (mbr[510:] != b'\x55\xaa' or first[0] != 0x80 or first[4] != 0xef
            or struct.unpack_from('<II', first, 8) != (PART_START, PART_SECTORS)
            or mbr[462:510] != bytes(48)):
        raise ValueError('not the CXT single-partition MBR layout (start 2048, end exactly 1 GiB)')
    fat = read_exact(fd, 512, PART_START * 512)
    u16 = lambda offset: struct.unpack_from('<H', fat, offset)[0]
    u32 = lambda offset: struct.unpack_from('<I', fat, offset)[0]
    if (fat[510:] != b'\x55\xaa' or u16(11) != 512 or fat[16] != 2
            or u16(17) != 0 or u16(19) != 0 or u16(22) != 0
            or u32(28) != PART_START or u32(32) != PART_SECTORS
            or u32(36) == 0 or u16(42) != 0 or u32(44) < 2
            or fat[66] != 0x29 or fat[71:82] != b'CXTDEB13   '
            or fat[82:90] != b'FAT32   '):
        raise ValueError('partition 1 is not the expected CXTDEB13 FAT32 boot volume')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--device', type=Path, help='unmounted whole block disk; this ENTIRE disk is erased at next boot')
    target.add_argument('--image', type=Path, help='explicit disposable, unmounted raw image (never a block device)')
    parser.add_argument('--region', choices=('auto', 'cn', 'global'), help='required when configuring; no implicit default')
    parser.add_argument('--github', type=github_name, help='required GitHub username with SSH keys; no credentials or bypass')
    parser.add_argument('--confirm', required=True, choices=('ERASE',), help='explicit authorization for whole-disk erasure at next boot')
    parser.add_argument('--clear', action='store_true', help='zero the reserved slot instead of enabling unattended installation')
    args = parser.parse_args()
    if args.clear:
        if args.github is not None or args.region is not None:
            parser.error('--clear does not accept --github or --region')
        payload = bytes(SLOT_SIZE)
    else:
        if args.github is None or args.region is None:
            parser.error('configuring requires both --github USER and --region auto|cn|global')
        text = f'CXT-AUTO-V1\nregion={args.region}\ngithub={args.github}\nconfirm=ERASE\n'.encode('ascii')
        payload = text + bytes(SLOT_SIZE - len(text))
    path = args.image if args.image is not None else args.device
    before = path.lstat()
    image_mode = args.image is not None
    if image_mode and not stat.S_ISREG(before.st_mode):
        raise ValueError('--image must name a regular raw file, not a symlink or device')
    if not image_mode and not stat.S_ISBLK(before.st_mode):
        raise ValueError('--device must name a whole block disk, not a symlink, partition, or file')
    print(f'Target: {path}\nReserved slot: offset {SLOT_OFFSET}, length {SLOT_SIZE}', flush=True)
    if args.clear:
        print('Clearing unattended authorization; this helper does not reboot.', flush=True)
    else:
        print(f'WARNING: NEXT BOOT ERASES THE ENTIRE SOURCE DISK. region={args.region} github={args.github}\n'
              'Only the configuration slot is written now. This helper does not reboot.', flush=True)
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    if not image_mode:
        flags |= os.O_EXCL  # Kernel block-device claim also refuses mounted/in-use devices.
    fd = os.open(path, flags)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_rdev, stat.S_IFMT(info.st_mode)) != (
                before.st_dev, before.st_ino, before.st_rdev, stat.S_IFMT(before.st_mode)):
            raise ValueError('target changed while opening')
        if image_mode:
            size = info.st_size
            check_image_safety(path, info)
        else:
            check_block_safety(info)
            if struct.unpack('I', fcntl.ioctl(fd, BLKROGET, bytes(4)))[0]:
                raise ValueError('block disk is read-only')
            size = struct.unpack('Q', fcntl.ioctl(fd, BLKGETSIZE64, bytes(8)))[0]
        check_identity(fd, size)
        # Repeat dependency checks immediately before the sole write operation.
        if image_mode:
            check_image_safety(path, info)
        else:
            check_block_safety(info)
        done = 0
        while done < SLOT_SIZE:
            written = os.pwrite(fd, payload[done:], SLOT_OFFSET + done)
            if written <= 0:
                raise OSError('short configuration write; do not boot until cleared or repaired')
            done += written
        os.fsync(fd)
        if read_exact(fd, SLOT_SIZE, SLOT_OFFSET) != payload:
            raise OSError('configuration read-back mismatch; do not boot until cleared or repaired')
    finally:
        os.close(fd)
    print('Slot cleared and verified.' if args.clear else 'Configuration written, fsynced, and byte-for-byte verified.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'Refusing/failed configuration: {error}', file=sys.stderr)
        sys.exit(1)
