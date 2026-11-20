#!/bin/sh
# Shared preflight primitives; no network request or disk write when sourced.
cxt_valid_github() {
    [ "$#" -eq 1 ] && [ -n "$1" ] && [ "${#1}" -le 39 ] || return 1
    case "$1" in [Ss][Kk][Ii][Pp]|*[!A-Za-z0-9-]*|-*|*-|*--*) return 1 ;; esac
}

cxt_fetch_keys() {
    cxt_user=$1
    cxt_dir=$2
    cxt_valid_github "$cxt_user" || return 1
    rm -f "$cxt_dir/authorized_keys" "$cxt_dir/fingerprints"
    mkdir -p "$cxt_dir" || return 1
    chmod 700 "$cxt_dir" || return 1
    if ! curl --disable --fail --silent --show-error --location --max-redirs 3 \
        --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 15 \
        --max-time 60 --max-filesize 1048576 \
        --cacert /etc/ssl/certs/ca-certificates.crt \
        --output "$cxt_dir/download.keys" "https://github.com/$cxt_user.keys" \
        2>"$cxt_dir/fetch-error"; then
        return 1
    fi
    : >"$cxt_dir/keys.checked"
    : >"$cxt_dir/fingerprints.checked"
    cxt_count=0
    while IFS= read -r cxt_line || [ -n "$cxt_line" ]; do
        [ -n "$cxt_line" ] || continue
        # Reject authorized_keys options and certificates; GitHub publishes raw keys.
        read -r cxt_type cxt_blob cxt_comment <<EOF
$cxt_line
EOF
        case "$cxt_type" in
            ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com) ;;
            *) return 1 ;;
        esac
        [ -n "$cxt_blob" ] || return 1
        printf '%s %s\n' "$cxt_type" "$cxt_blob" >"$cxt_dir/one.key"
        ssh-keygen -l -E sha256 -f "$cxt_dir/one.key" >>"$cxt_dir/fingerprints.checked" 2>/dev/null || return 1
        cat "$cxt_dir/one.key" >>"$cxt_dir/keys.checked" || return 1
        cxt_count=$((cxt_count + 1))
        [ "$cxt_count" -le 128 ] || return 1
    done <"$cxt_dir/download.keys"
    [ "$cxt_count" -gt 0 ] || return 1
    mv "$cxt_dir/keys.checked" "$cxt_dir/authorized_keys" || return 1
    mv "$cxt_dir/fingerprints.checked" "$cxt_dir/fingerprints" || return 1
    chmod 600 "$cxt_dir/authorized_keys" || return 1
    rm -f "$cxt_dir/one.key" "$cxt_dir/download.keys"
}

# sysfs encodes both partitions and stacked storage (dm/LVM/md) relationships.
cxt_uses_disk() {
    cxt_node=$(readlink -f "$1") || return 1
    cxt_disk_node=$(readlink -f "$2") || return 1
    case "$cxt_node" in "$cxt_disk_node"|"$cxt_disk_node"/*) return 0 ;; esac
    for cxt_slave in "$cxt_node"/slaves/*; do
        [ -e "$cxt_slave" ] || continue
        # A subshell keeps recursive scratch variables local on BusyBox ash.
        (cxt_uses_disk "$cxt_slave" "$cxt_disk_node") && return 0
    done
    return 1
}

cxt_disk_safe() {
    case "$1" in /dev/*) ;; *) return 1 ;; esac
    [ -b "$1" ] || return 1
    cxt_name=${1#/dev/}
    case "$cxt_name" in */*|loop*|ram*|zram*|sr*|fd*|dm-*|md*) return 1 ;; esac
    cxt_sys=/sys/class/block/$cxt_name
    [ -d "$cxt_sys" ] && [ ! -e "$cxt_sys/partition" ] || return 1
    [ "$(cat "$cxt_sys/ro")" = 0 ] || return 1
    [ "$(cat "$cxt_sys/size")" -gt 0 ] || return 1
    while read -r cxt_mount cxt_parent cxt_dev cxt_rest; do
        [ -e "/sys/dev/block/$cxt_dev" ] || continue
        (cxt_uses_disk "/sys/dev/block/$cxt_dev" "$cxt_sys") && return 1
    done </proc/self/mountinfo
    while read -r cxt_swap cxt_rest; do
        case "$cxt_swap" in /dev/*) ;; *) continue ;; esac
        cxt_swap_name=$(basename "$(readlink -f "$cxt_swap")")
        (cxt_uses_disk "/sys/class/block/$cxt_swap_name" "$cxt_sys") && return 1
    done </proc/swaps
    return 0
}
