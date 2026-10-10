#!/usr/bin/env bash
set -euo pipefail

SWAPFILE="${EVOLUTION_SWAPFILE:-/swapfile}"
SIZE="${EVOLUTION_SWAP_SIZE:-4G}"
SWAPPINESS="${EVOLUTION_SWAPPINESS:-10}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install_evolution_swap.sh" >&2
  exit 2
fi

if swapon --show=NAME --noheadings | awk '{print $1}' | grep -Fxq "${SWAPFILE}"; then
  echo "Evolution swap already active: ${SWAPFILE}"
else
  if [[ -e "${SWAPFILE}" ]]; then
    echo "Refusing to overwrite existing inactive file: ${SWAPFILE}" >&2
    exit 3
  fi
  fallocate -l "${SIZE}" "${SWAPFILE}"
  chmod 600 "${SWAPFILE}"
  mkswap "${SWAPFILE}"
  swapon "${SWAPFILE}"
fi

FSTAB_LINE="${SWAPFILE} none swap sw 0 0"
grep -Fqx "${FSTAB_LINE}" /etc/fstab || printf '%s\n' "${FSTAB_LINE}" >> /etc/fstab

cat >/etc/sysctl.d/99-evolution-swap.conf <<EOF
vm.swappiness=${SWAPPINESS}
EOF
sysctl -p /etc/sysctl.d/99-evolution-swap.conf

echo
free -h
echo
swapon --show
