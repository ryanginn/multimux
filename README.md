# MultiMux

Run several DAB ensembles (e.g. **12A, 12B, 12C, 12D**) on **one HackRF**, all managed from a web GUI.

```
 ODR-AudioEnc ─► ODR-DabMux ─► ODR-DabMod ─┐   (one chain per block, ZeroMQ between each)
 ODR-AudioEnc ─► ODR-DabMux ─► ODR-DabMod ─┼─► combiner (GNU Radio) ─► HackRF
 ODR-AudioEnc ─► ODR-DabMux ─► ODR-DabMod ─┘
```

## Install (Debian / Ubuntu / Raspberry Pi OS)

```bash
curl -fsSL https://raw.githubusercontent.com/ryanginn/multimux/main/install.sh | sudo bash
```

The installer:

* installs GNU Radio, gr-osmosdr and the HackRF tools **from apt**, then checks that the system
  Python can actually import them (most broken installs come from pip, conda or venv GNU Radio)
* installs ODR-DabMux and ODR-DabMod from apt, or builds them from source when apt doesn't have them
* clones this repo to `/opt/multimux` and creates a `multimux` system user in the `plugdev` group
  (needed for HackRF USB access)
* installs and starts the `multimux` systemd service
* runs a system check and prints the web address and a generated password

Running it again upgrades in place and keeps your config. Options: `--branch`, `--port`,
`--build-odr`, `--skip-odr`, `--with-audioenc`, `--no-service`.

## Web GUI (`http://<host>:8080/`)

* **Start / Stop / Restart** the whole chain, or one process at a time
* **Live status** for each process: state, uptime, restart count, and a level meter that shows
  whether samples are arriving from each modulator and whether the output is clipping
* **Spectrum plan**: where each ensemble sits and where the HackRF LO is
* **Channels**: add or remove blocks, set gains and ports, and edit each ODR-DabMux config in the
  browser. You can add extra processes per channel (ODR-AudioEnc, ODR-PadEnc, …), and those are
  supervised too.
* **Transmitter**: TX gain, RF amp, centre frequency and sample rate (both default to *auto*), ppm
* **System check**: the same checks as `multimux doctor`, with suggested fixes
* **Check for updates**: pulls the latest version from GitHub and restarts the service

## Reliability

* Every process restarts automatically after a crash (backoff from 1 s up to 30 s).
* Processes start in order (encoders → muxes → modulators → combiner) and stop in reverse order,
  so the HackRF is always released cleanly.
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

## Feeding audio

Each channel's mux config has a DAB+ subchannel listening on a ZeroMQ port (for example `9001`).
Add an extra process to the channel in the GUI, for example:

```bash
odr-audioenc -v https://stream.example.com/live.mp3 -b 96 -r 48000 -o tcp://localhost:9001
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
