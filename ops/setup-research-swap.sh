#!/usr/bin/env bash
# Explicit operator action only. Never reformats/replaces an existing file or swap.
set -euo pipefail
mode=${1:---check}
[[ "$mode" == --check || "$mode" == --apply ]] || { echo 'Usage: setup-research-swap.sh --check|--apply' >&2; exit 2; }
mount=/mnt/research
swapfile=$mount/research.swap
size=$((16*1024*1024*1024))
[[ ! -L "$mount" && ! -L "$swapfile" ]] || { echo 'Symlink refused' >&2; exit 1; }
mountpoint -q "$mount" || { echo '/mnt/research is not mounted; aborting' >&2; exit 1; }
[[ $(findmnt -n -o FSTYPE --target "$mount") == ext4 ]] || { echo 'Expected ext4 research filesystem' >&2; exit 1; }
[[ $(stat -c %d "$mount") != $(stat -c %d /) ]] || { echo 'Research mount is on root filesystem; aborting' >&2; exit 1; }
read -r total free < <(df -B1 --output=size,avail "$mount" | tail -1)
reserve=$((total/10)); (( reserve >= 10*1024*1024*1024 )) || reserve=$((10*1024*1024*1024))
if [[ -e "$swapfile" ]]; then
  [[ -f "$swapfile" && $(stat -c %h "$swapfile") == 1 && $(stat -c %s "$swapfile") == "$size" ]] || { echo 'Existing swap path is not a private 16GiB regular file; preserved' >&2; exit 1; }
else
  (( free >= size+reserve )) || { echo 'Insufficient space for 16GiB swap plus reserve' >&2; exit 1; }
fi
echo "Research mount: $(findmnt -n -o SOURCE,TARGET --target "$mount")"
echo "Free bytes=$free reserve bytes=$reserve swap bytes=$size"
swapon --show
[[ "$mode" == --apply ]] || { echo 'CHECK ONLY; no changes made'; exit 0; }
[[ $EUID == 0 ]] || { echo 'Run --apply with sudo' >&2; exit 1; }
exec 9>/run/setup-research-swap.lock
flock -n 9 || { echo 'Another swap setup is running' >&2; exit 1; }
[[ ! -L /etc/fstab && ! -L /etc/sysctl.d/90-research-swap.conf ]] || { echo 'System config symlink refused' >&2; exit 1; }
count=$(awk -v p="$swapfile" '$1==p {n++} END {print n+0}' /etc/fstab)
(( count <= 1 )) || { echo 'Duplicate existing fstab entries; preserved, review required' >&2; exit 1; }
awk -v p="$swapfile" '$1==p && $3!="swap" {bad=1} END {exit bad}' /etc/fstab || { echo 'Conflicting fstab entry; preserved' >&2; exit 1; }
config=/etc/sysctl.d/90-research-swap.conf
if [[ -e "$config" ]] && ! grep -Eq '^vm\.swappiness[[:space:]]*=[[:space:]]*10[[:space:]]*$' "$config"; then
  echo 'Existing sysctl config differs; preserved, review required' >&2; exit 1
fi
if [[ -e "$swapfile" ]]; then
  [[ $(blkid -p -s TYPE -o value "$swapfile") == swap ]] || { echo 'Existing file has no swap signature; preserved' >&2; exit 1; }
else
  # noclobber claims the new path; interrupted/partial creation is never reformatted on retry.
  (set -o noclobber; : > "$swapfile")
  chmod 600 "$swapfile"
  fallocate -l "$size" "$swapfile"
  mkswap "$swapfile"
fi
chmod 600 "$swapfile"
if ! swapon --show --noheadings --raw --output NAME | grep -Fxq "$swapfile"; then
  swapon "$swapfile"
fi
if (( count == 0 )); then
  printf '\n%s none swap sw 0 0\n' "$swapfile" >> /etc/fstab
fi
if [[ ! -e "$config" ]]; then
  (set -o noclobber; printf 'vm.swappiness=10\n' > "$config")
fi
sysctl -w vm.swappiness=10
echo 'Research swap ready; existing other swap devices remain unchanged'
