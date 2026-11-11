"""DAB Band III channel table and frequency planning for the combiner."""

import math

# Centre frequencies in Hz (ETSI EN 300 401, Band III)
BAND_III = {
    "5A": 174_928_000, "5B": 176_640_000, "5C": 178_352_000, "5D": 180_064_000,
    "6A": 181_936_000, "6B": 183_648_000, "6C": 185_360_000, "6D": 187_072_000,
    "7A": 188_928_000, "7B": 190_640_000, "7C": 192_352_000, "7D": 194_064_000,
    "8A": 195_936_000, "8B": 197_648_000, "8C": 199_360_000, "8D": 201_072_000,
    "9A": 202_928_000, "9B": 204_640_000, "9C": 206_352_000, "9D": 208_064_000,
    "10A": 209_936_000, "10N": 210_096_000, "10B": 211_648_000, "10C": 213_360_000,
    "10D": 215_072_000,
    "11A": 216_928_000, "11N": 217_088_000, "11B": 218_640_000, "11C": 220_352_000,
    "11D": 222_064_000,
    "12A": 223_936_000, "12N": 224_096_000, "12B": 225_648_000, "12C": 227_360_000,
    "12D": 229_072_000,
    "13A": 230_784_000, "13B": 232_496_000, "13C": 234_208_000, "13D": 235_776_000,
    "13E": 237_488_000, "13F": 239_200_000,
}

BASE_RATE = 2_048_000        # native ODR-DabMod output rate
DAB_HALF_BW = 768_000        # half of the 1.536 MHz DAB signal
LO_GUARD = 50_000            # keep the HackRF DC/LO spike this far outside any ensemble
EDGE_FRACTION = 0.45         # usable fraction of fs on each side of centre
MAX_INTERP = 9               # 9 * 2.048 = 18.432 MS/s (HackRF max is 20)


class PlanError(ValueError):
    pass


def block_freq(block):
    key = str(block).strip().upper()
    if key not in BAND_III:
        raise PlanError(f"Unknown DAB block '{block}'")
    return BAND_III[key]


def _collides_with_lo(center, freqs):
    return any(abs(f - center) < DAB_HALF_BW + LO_GUARD for f in freqs)


def _pick_center(freqs):
    freqs = sorted(freqs)
    mid = (freqs[0] + freqs[-1]) // 2
    if not _collides_with_lo(mid, freqs):
        return mid
    # Try the gaps between adjacent ensembles, then just outside the edges.
    candidates = [(a + b) // 2 for a, b in zip(freqs, freqs[1:])]
    candidates += [freqs[0] - 1_024_000, freqs[-1] + 1_024_000]
    candidates = [c for c in candidates if not _collides_with_lo(c, freqs)]
    return min(candidates, key=lambda c: max(abs(f - c) for f in freqs))


def make_plan(blocks, center="auto", sample_rate="auto"):
    """Work out tuning frequency, sample rate and per-block offsets.

    Returns a dict:
      center_hz, sample_rate, interp, offsets {block: Hz}, warnings [str]
    """
    blocks = [str(b).strip().upper() for b in blocks]
    if not blocks:
        raise PlanError("No channels enabled")
    if len(set(blocks)) != len(blocks):
        raise PlanError("The same block is used more than once")
    freqs = {b: block_freq(b) for b in blocks}
    fl = sorted(freqs.values())
    for a, b in zip(fl, fl[1:]):
        if b - a < 2 * DAB_HALF_BW:
            raise PlanError("Two selected blocks overlap (e.g. 12A and 12N)")

    warnings = []
    if center in (None, "", "auto"):
        center_hz = _pick_center(fl)
    else:
        center_hz = int(float(center))
        if _collides_with_lo(center_hz, fl):
            warnings.append("Centre frequency sits inside an ensemble: the HackRF LO "
                            "leakage spike will land on it.")

    offsets = {b: f - center_hz for b, f in freqs.items()}
    need = max(abs(o) for o in offsets.values()) + DAB_HALF_BW

    if sample_rate in (None, "", "auto"):
        interp = None
        for i in range(2, MAX_INTERP + 1):
            if need <= EDGE_FRACTION * BASE_RATE * i:
                interp = i
                break
        if interp is None:
            raise PlanError("Selected blocks span too much bandwidth for one HackRF")
    else:
        sr = int(float(sample_rate))
        if sr % BASE_RATE:
            raise PlanError("Sample rate must be a multiple of 2.048 MS/s")
        interp = sr // BASE_RATE
        if interp < 1 or interp > MAX_INTERP:
            raise PlanError("Sample rate out of range for HackRF")
        if need > EDGE_FRACTION * BASE_RATE * interp:
            raise PlanError("Sample rate too low for the selected blocks")

    return {
        "center_hz": center_hz,
        "sample_rate": BASE_RATE * interp,
        "interp": interp,
        "offsets": offsets,
        "freqs": freqs,
        "warnings": warnings,
    }


def phase_increment(offset_hz, sample_rate):
    return 2 * math.pi * offset_hz / sample_rate
