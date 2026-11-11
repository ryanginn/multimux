# MultiMux

Take several existing DAB feeds (ETI or EDI from your ODR-DabMux setups) and transmit them as
separate ensembles (e.g. **12A, 12B, 12C, 12D**) on **one HackRF**, managed from a web GUI.
MultiMux does **not** run the muxes: point each channel at a feed that already exists.

```
 your mux A ─ ETI/ZMQ ─► ODR-DabMod ─┐
 your mux B ─ EDI ─────► ODR-DabMod ─┼─► combiner (GNU Radio) ─► HackRF
 remote ODR-DabMod ─ IQ/ZMQ ─────────┘
```

Supported feed types per channel:

| Type | Example address | Notes |
|---|---|---|
| ETI over ZeroMQ | `tcp://192.168.1.10:9100` | ODR-DabMux `outputs { zmq "zmq+tcp://*:9100" }` |
| EDI | `tcp://192.168.1.10:9201`, `udp://:12000`, `udp://@239.1.1.1:12000` | ODR-DabMux `edi` output |
| ETI over TCP | `tcp://192.168.1.10:9300` | raw ETI stream |
| ETI file / pipe | `/path/to/feed.eti` | |
| IQ from remote ODR-DabMod | `tcp://192.168.1.20:54001` | already modulated (2.048 MS/s, `output=zmq`); no local modulator is started |

## Install (Debian / Ubuntu / Raspberry Pi OS)

```bash
curl -fsSL https://raw.githubusercontent.com/ryanginn/multimux/main/install.sh | sudo bash
```

The installer:

* installs GNU Radio, gr-osmosdr and the HackRF tools **from apt**, then checks that the system
  Python can actually import them (most broken installs come from pip, conda or venv GNU Radio)
* installs ODR-DabMod from apt, or builds it from source when apt doesn't have it
* clones this repo to `/opt/multimux` and creates a `multimux` system user in the `plugdev` group
  (needed for HackRF USB access)
* installs and starts the `multimux` systemd service
* runs a system check and prints the web address and a generated password

Running it again upgrades in place and keeps your config. Options: `--branch`, `--port`,
`--build-odr`, `--skip-odr`, `--no-service`.

## Web GUI (`http://<host>:8080/`)

* **Start / Stop / Restart** the whole chain, or one process at a time
* **Live status** for each process: state, uptime, restart count, and a level meter that shows
  whether samples are arriving from each modulator and whether the output is clipping
* **Spectrum plan**: where each ensemble sits and where the HackRF LO is
* **Channels**: add or remove blocks, choose each one's feed type and address, and set gains
* **Transmitter**: TX gain, RF amp, centre frequency and sample rate (both default to *auto*), ppm
* **System check**: the same checks as `multimux doctor`, with suggested fixes
* **Check for updates**: pulls the latest version from GitHub and restarts the service

## Reliability

* Every process restarts automatically after a crash (backoff from 1 s up to 30 s).
* The modulators start before the combiner and stop after it, so the HackRF is always released
  cleanly. If a feed drops, ODR-DabMod keeps reconnecting and the meter shows **no samples** for
  that block.
* The systemd service uses `Restart=always`, so it comes back after reboots, crashes and updates.
* The frequency plan is calculated for you. The centre frequency and sample rate are picked so that
  every block fits and the HackRF LO/DC spike stays outside every ensemble. Invalid layouts are
  rejected before anything is started.
* Config is saved atomically, and invalid configs are refused, so a typo can't break a running
  service.
* The web GUI and supervisor use only the Python standard library, so there is nothing to
  `pip install`.

## Command line

```bash
multimux doctor            # check the install
multimux plan              # show centre frequency / sample rate / offsets
multimux combiner          # run only the combiner in the foreground
sudo systemctl status multimux
sudo journalctl -u multimux -f
```

## Levels

`master_scale` defaults to `1/N`. If the output meter shows **clip**, lower the master scale or the
per-channel combiner gain. If the peak is very low, raise it. Peaks around 0.5–0.8 are a good
target. After that, set RF power with the TX gain.

## Uninstall

```bash
sudo /opt/multimux/uninstall.sh          # keeps /etc/multimux
sudo /opt/multimux/uninstall.sh --purge  # also removes the config and user
```

> Only transmit on frequencies you are licensed to use.
