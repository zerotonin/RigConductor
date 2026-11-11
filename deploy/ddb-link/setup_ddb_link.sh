#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║  setup_ddb_link.sh — direct cable tablet ↔ rig for DrosoDB       ║
# ╚══════════════════════════════════════════════════════════════════╝
# A point-to-point Ethernet cable (USB-C/USB-A Ethernet adapter on each machine)
# carries ONE thing: Syncthing pushing the tablet's DrosoDB backup folder to the
# rig, one-way. No internet, no default route, no other ports, no USB sticks.
#
#   tablet 10.99.77.2  /srv/ddb/backups   Send Only   (ignores history/)
#   rig    10.99.77.1  ~/data/drosodb      Receive Only → ddb.latest.sqlite3
#
# Run on each machine, as the user whose Syncthing runs (needs sudo for the
# firewall and NetworkManager). Dry run by default; add --apply to change things.
#
#   rig:    setup_ddb_link.sh rig    <iface> <TABLET-SYNCTHING-ID> [--apply]
#   tablet: setup_ddb_link.sh tablet <iface> <RIG-SYNCTHING-ID>    [--apply]
#
# <iface>: the adapter's name from `ip -br link` after plugging it in (enx…).
# Syncthing IDs: `syncthing --device-id` (or Actions → Show ID in the web UI).
set -euo pipefail

ROLE="${1:?rig|tablet}"; IFACE="${2:?interface, e.g. enx00e04c680001}"; PEER_ID="${3:?peer Syncthing device ID}"
APPLY=0; [ "${4:-}" = "--apply" ] && APPLY=1
NET=10.99.77; PORT=22000; FOLDER_ID=ddb-backups
case "$ROLE" in
  rig)    ME=$NET.1; PEER=$NET.2; PEER_NAME=ddb-tablet; FOLDER_PATH="$HOME/data/drosodb"; FTYPE=receiveonly ;;
  tablet) ME=$NET.2; PEER=$NET.1; PEER_NAME=bg-motif01; FOLDER_PATH=/srv/ddb/backups;   FTYPE=sendonly ;;
  *) echo "role must be rig or tablet" >&2; exit 2 ;;
esac

run() { echo "+ $*"; [ $APPLY = 1 ] && "$@" || true; }

echo "== 1. link: $IFACE = $ME/30, never the default route, no IPv6"
run sudo nmcli connection add type ethernet ifname "$IFACE" con-name ddb-link \
  ipv4.method manual ipv4.addresses "$ME/30" ipv4.never-default yes \
  ipv6.method disabled connection.autoconnect yes
run sudo nmcli connection up ddb-link

echo "== 2. firewall: on $IFACE only Syncthing from $PEER; nothing routed"
run sudo ufw allow in on "$IFACE" from "$PEER" to "$ME" port $PORT proto tcp comment 'ddb-link syncthing'
run sudo ufw deny in on "$IFACE" comment 'ddb-link: nothing else'
run sudo ufw route deny in on "$IFACE" comment 'ddb-link: no forwarding'
run sudo ufw --force enable

echo "== 3. Syncthing: peer $PEER_NAME at tcp://$PEER:$PORT, folder $FOLDER_ID ($FTYPE)"
CFG=""
for d in "$HOME/.local/state/syncthing" "$HOME/.config/syncthing"; do
  [ -f "$d/config.xml" ] && { CFG=$d; break; }
done
[ -n "$CFG" ] || { echo "Syncthing config.xml not found; start Syncthing once first" >&2; exit 1; }
KEY=$(sed -n 's:.*<apikey>\(.*\)</apikey>.*:\1:p' "$CFG/config.xml")
GUI=$(sed -n '/<gui /,/<\/gui>/s:.*<address>\(.*\)</address>.*:\1:p' "$CFG/config.xml")
API="http://${GUI:-127.0.0.1:8384}/rest/config"
# the API key stays in this shell; it is not echoed
st() { echo "+ syncthing $1 $2"; [ $APPLY = 1 ] && curl -fsS -X "$1" -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' "$API/$2" ${3:+-d "$3"} >/dev/null || true; }
st PUT "devices/$PEER_ID" "{\"deviceID\":\"$PEER_ID\",\"name\":\"$PEER_NAME\",
  \"addresses\":[\"tcp://$PEER:$PORT\"],\"autoAcceptFolders\":false}"
mkdir -p "$FOLDER_PATH"
st PUT "folders/$FOLDER_ID" "{\"id\":\"$FOLDER_ID\",\"label\":\"DrosoDB backups\",
  \"path\":\"$FOLDER_PATH\",\"type\":\"$FTYPE\",\"devices\":[{\"deviceID\":\"$PEER_ID\"}],
  \"rescanIntervalS\":300,\"fsWatcherEnabled\":true}"
if [ "$ROLE" = tablet ]; then
  echo "+ ignore history/ (only ddb.latest.sqlite3 travels)"
  [ $APPLY = 1 ] && printf 'history\n.last-hash\n' > "$FOLDER_PATH/.stignore" || true
fi

echo "== done. Check: ping -c1 $PEER ; on the rig: rigconductor doctor (DrosoDB line)"
[ $APPLY = 0 ] && echo "(dry run — nothing changed; add --apply)"
