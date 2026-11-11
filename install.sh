#!/usr/bin/env bash
# MultiMux installer for Debian / Ubuntu / Raspberry Pi OS.
#
#   curl -fsSL https://raw.githubusercontent.com/ryanginn/multimux/main/install.sh | sudo bash
#
# Safe to re-run: it upgrades in place and never overwrites your config.
#
# Options:
#   --branch NAME       git branch to install / follow for updates (default: main)
#   --repo URL          git repository (default: https://github.com/ryanginn/multimux.git)
#   --port N            web GUI port (default: 8080)
#   --build-odr         always build ODR-DabMod from source
#   --skip-odr          don't install ODR-DabMod (only "IQ from remote ODR-DabMod" feeds will work)
#   --no-service        install files only, don't create the systemd service
set -euo pipefail

REPO_URL="https://github.com/ryanginn/multimux.git"
BRANCH="main"
PORT=8080
ODR_MODE="auto"
SERVICE=1
PREFIX=/opt/multimux
CONF_DIR=/etc/multimux
STATE_DIR=/var/lib/multimux
SRC_DIR=/usr/local/src/multimux-odr
SVC_USER=multimux

while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --build-odr) ODR_MODE="build"; shift ;;
    --skip-odr) ODR_MODE="skip"; shift ;;
    --no-service) SERVICE=0; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

c_ok()   { printf '\033[32m==>\033[0m %s\n' "$*"; }
c_warn() { printf '\033[33m==> WARNING:\033[0m %s\n' "$*"; }
die()    { printf '\033[31m==> ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root: sudo bash install.sh"
command -v apt-get >/dev/null || die "Only apt-based systems (Debian, Ubuntu, Raspberry Pi OS) are supported"
export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------- packages
c_ok "Installing GNU Radio, gr-osmosdr, HackRF tools and git (this can take a while)"
apt-get update -q
apt-get install -y -q git python3 python3-numpy gnuradio gr-osmosdr hackrf ca-certificates curl \
  || die "apt could not install the base packages. Check 'apt-get install gnuradio gr-osmosdr hackrf' manually."

# Verify the exact imports the combiner needs, using the *system* python.
# (The #1 cause of broken installs: GNU Radio installed for a different python / a venv.)
/usr/bin/python3 -c "from gnuradio import gr, blocks, zeromq, filter; import osmosdr, numpy" \
  || die "GNU Radio is installed but /usr/bin/python3 cannot import it. Remove any pip/conda GNU Radio and retry."
c_ok "GNU Radio $(/usr/bin/python3 -c 'from gnuradio import gr; print(gr.version())') OK"

# ---------------------------------------------------------------- ODR tools
build_odr() {  # name repo configure-args...
  local name="$1" repo="$2"; shift 2
  c_ok "Building $name from source"
  mkdir -p "$SRC_DIR"
  if [ -d "$SRC_DIR/$name/.git" ]; then
    git -C "$SRC_DIR/$name" fetch -q --tags origin
    git -C "$SRC_DIR/$name" reset -q --hard "origin/$(git -C "$SRC_DIR/$name" rev-parse --abbrev-ref HEAD)"
  else
    git clone -q --depth 1 "$repo" "$SRC_DIR/$name"
  fi
  ( cd "$SRC_DIR/$name"
    ./bootstrap.sh >/dev/null 2>&1 || autoreconf -fi
    ./configure "$@"
    make -j"$(nproc)"
    make install ) || die "Building $name failed (see output above)"
  ldconfig
}

need_odr_build_deps() {
  apt-get install -y -q build-essential automake autoconf libtool pkg-config \
    libzmq3-dev libcurl4-openssl-dev libboost-system-dev libboost-thread-dev libfftw3-dev
}

if [ "$ODR_MODE" != "skip" ]; then
  if [ "$ODR_MODE" = "auto" ] && ! command -v odr-dabmod >/dev/null; then
    apt-get install -y -q odr-dabmod >/dev/null 2>&1 && c_ok "odr-dabmod installed from apt" || true
  fi
  if [ "$ODR_MODE" = "build" ] || ! command -v odr-dabmod >/dev/null; then
    need_odr_build_deps
    # Only the ZeroMQ output is needed: the HackRF is driven by the combiner.
    build_odr ODR-DabMod https://github.com/Opendigitalradio/ODR-DabMod.git --disable-output-uhd --disable-native
  fi
fi

# ---------------------------------------------------------------- user + files
if ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$SVC_USER"
fi
for g in plugdev dialout; do
  getent group "$g" >/dev/null && usermod -aG "$g" "$SVC_USER"
done

c_ok "Installing MultiMux into $PREFIX (branch $BRANCH)"
if [ -d "$PREFIX/.git" ]; then
  git -C "$PREFIX" remote set-url origin "$REPO_URL"
  git -C "$PREFIX" fetch -q origin "$BRANCH"
  git -C "$PREFIX" checkout -q -B "$BRANCH" "origin/$BRANCH"
else
  rm -rf "$PREFIX"
  git clone -q --branch "$BRANCH" "$REPO_URL" "$PREFIX"
fi
chown -R "$SVC_USER:$SVC_USER" "$PREFIX"
git config --system --get-all safe.directory 2>/dev/null | grep -qx "$PREFIX" \
  || git config --system --add safe.directory "$PREFIX"

mkdir -p "$CONF_DIR" "$STATE_DIR"
chown "$SVC_USER:$SVC_USER" "$CONF_DIR" "$STATE_DIR"
chmod 750 "$CONF_DIR"

NEW_PASSWORD=""
if [ ! -f "$CONF_DIR/config.json" ]; then
  NEW_PASSWORD="$(/usr/bin/python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  cat > "$CONF_DIR/config.json" <<EOF
{
  "web": {"host": "0.0.0.0", "port": $PORT, "username": "admin", "password": "$NEW_PASSWORD"},
  "update": {"remote": "origin", "branch": "$BRANCH"},
  "autostart": false
}
EOF
  chown "$SVC_USER:$SVC_USER" "$CONF_DIR/config.json"
  chmod 600 "$CONF_DIR/config.json"
fi

cat > /usr/local/bin/multimux <<EOF
#!/bin/sh
export MULTIMUX_CONFIG=$CONF_DIR/config.json MULTIMUX_STATE=$STATE_DIR PYTHONPATH=$PREFIX
exec /usr/bin/python3 -m multimux "\$@"
EOF
chmod 755 /usr/local/bin/multimux

# ---------------------------------------------------------------- service
if [ "$SERVICE" = 1 ]; then
  install -m 644 "$PREFIX/systemd/multimux.service" /etc/systemd/system/multimux.service
  systemctl daemon-reload
  systemctl enable multimux.service >/dev/null
  systemctl restart multimux.service
  c_ok "Service started (systemctl status multimux)"
fi

echo
runuser -u "$SVC_USER" -- env MULTIMUX_CONFIG="$CONF_DIR/config.json" MULTIMUX_STATE="$STATE_DIR" \
  PYTHONPATH="$PREFIX" /usr/bin/python3 -m multimux doctor || \
  c_warn "Some checks failed - see above. The web GUI's 'Run system check' shows the same list."

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
c_ok "Done. Open http://${IP:-localhost}:$PORT/"
if [ -n "$NEW_PASSWORD" ]; then
  echo "    username: admin"
  echo "    password: $NEW_PASSWORD"
  echo "    (stored in $CONF_DIR/config.json, change it in the GUI under Settings)"
fi
