"""WIRE COST: how many bytes a tunnel type puts on the wire for every byte its users move (the owner, 2026-11-05: "does the tunnel type matter for the traffic volume of the Iran servers? A type that is PROVEN
to carry a lot of overhead, like KCP, must be changed; the overhead is one of the Doctor's important benchmarks when it chooses a type - but it must still look at the quality").

A tunnel never moves only the users' bytes. The headers of the packets it rides in, the framing of its mux, the TLS, the parity and the resends of KCP + FEC travel on the card of the Iran server too: a user who downloads
1 GB costs the Iran server about 1.06 GB over a Backhaul tunnel and about 1.6 GB over a KCP one - and the provider that bills by the GB bills the second.

Two sources, the measured one first:
  measured  the speed test itself (ssh.py `probe_run`, run on the Iran server) reads the card of the default route before the streams, after them and a second later: what crossed the card DURING the streams, minus what
            crossed it anyway (the rate before and after, the mean of the two), over what the streams moved = `wire_ratio`. A test counts only when it moved enough (MIN_MOVED) and the card was calm (the two rates agree:
            MAX_NOISE): a busy server gives NO number, never a wrong one. The last KEEP numbers of a kind are kept in the memory of the pair (`knowledge.merge`, `wire`) and their MEDIAN is the cost. Only for the types that have
            their own test door (BackPack, Paqet, Chisel): a Backhaul over GRE or over a kernel link is tested over the LINK, and the link is only its first layer.
  model     the cost the design of the type gives (`MODEL`): header bytes per packet, framing, FEC. Used when nothing is measured yet and for the types whose tests only see the link.

The cost is a benchmark in `knowledge.ranked` (`lightness`: a type that costs twice counts a quarter) and decides, for a running tunnel, `knowledge.lighter_kinds` (see `bandwidth._upgrade_check`)."""
from __future__ import annotations

from statistics import median

LIGHT_EXP = 2.0           # lightness = (LIGHT_FLOOR / cost) ** LIGHT_EXP above the floor: 1.13 -> 0.95, 1.22 -> 0.81, 1.4 -> 0.62, 1.6 -> 0.47, 1.9 -> 0.34 (the overhead is a high-priority benchmark, the quality still counts)
LIGHT_FLOOR = 1.10        # the plain types (Backhaul, BackPack, Chisel, kernel links) cost 1.03 - 1.10 by their design, and the model is not that exact: up to here they count the same, and the order the Doctor has measured stays
HEAVY = 1.35              # a running type that costs this much or more is a candidate to be changed for a lighter one (KCP, xDi); the ones under it cost a few percent and are left alone
LIGHTER_BY = 0.85         # ... the type that takes its place costs at most this share of what the running one costs
UNKNOWN = 1.15            # a type this module has no model for
MIN_MOVED = 20_000_000    # a test that moved less than this (bytes) says nothing about the wire
MAX_NOISE = 0.15          # the two baselines of the card may differ by at most this share of what the streams moved
MAX_RATIO = 5.0           # a ratio above this is a reading gone wrong, not a tunnel
MIN_RATIO = 0.9           # ... and one under this is a reading of the wrong card
KEEP = 5                  # the measured ratios of one type kept per pair

# the type -> bytes on the wire per byte moved. The reasons: 1500-byte frames; TCP + IP = 52 bytes, GRE adds 24, a mux frame ~ 1 %, a WebSocket frame ~ 2 %, TLS records ~ 1 %; QUIC ~ 8 %; a UDP carrier with its
# acknowledgements ~ 7 %; KCP + FEC (parity shards 3 of 10, the resends of the fast modes, an ack for nearly every packet) about 1.4 - 1.6; KCP inside ICMP echo (xDi) and its parity about 1.9.
MODEL = {"tcpmux": 1.06, "tcp": 1.06, "wsmux": 1.08, "wssmux": 1.10,
         "bp:tcpmux": 1.04, "bp:tcp": 1.04, "bp:ws": 1.06, "bp:wsmux": 1.06, "bp:wss": 1.08, "bp:wssmux": 1.08, "bp:stealth": 1.08,
         "bp:pck": 1.06, "bpd:pck": 1.06, "bpd:udp": 1.07, "bp:quic": 1.12, "bpd:quic": 1.12, "bp:kcp": 1.60, "bp:xdi": 1.90, "bpd:xdi": 1.90,
         "paqet": 1.40, "chisel": 1.12, "chisel:tls": 1.13, "chisel:fwd": 1.10, "chisel:fwdtls": 1.12}
LINK = {"gre": 1.05, "ipip": 1.03, "ipipfou": 1.05, "ipipgue": 1.05, "fou": 1.05, "gue": 1.05, "ipsec": 1.07, "ipsecnat": 1.09, "wg": 1.07, "l2tpv3": 1.05, "gretap": 1.07, "vxlan": 1.07, "geneve": 1.07}
TRANSPORT = {"tcpmux": 0.01, "tcp": 0.01, "wsmux": 0.03, "wssmux": 0.05}          # what the Backhaul transport adds over a link
CARRIED = {"wgchisel": 1.21, "wgchiselfwd": 1.19}          # WireGuard carried inside Chisel's TLS: both layers


def model_cost(kind: str) -> float:
    """Pure: the cost the design of the type gives (UNKNOWN for a type this table does not know)."""
    k = str(kind or "")

    if k in MODEL:
        return MODEL[k]

    link, _, transport = k.partition(":")

    if link in CARRIED:
        return round(CARRIED[link] + TRANSPORT.get(transport, 0.0), 2)

    if link in LINK and transport in TRANSPORT:
        return round(LINK[link] + TRANSPORT[transport], 2)

    return UNKNOWN


def wire_ratio(d: dict | None) -> float | None:
    """Pure: the wire cost one speed test measured, from the raw readings of `probe_run` - w_moved (bytes the streams moved), w_nic (bytes that crossed the card of the Iran server during them), w_dur (seconds), w_pre and
    w_post (bytes per second the card carried just before and just after). None when the test moved too little, a reading is missing, or the card was not calm; never below 1 (a card cannot carry less than was moved)."""
    d = d or {}

    try:
        moved, nic, dur = float(d["w_moved"]), float(d["w_nic"]), float(d["w_dur"])
    except (KeyError, TypeError, ValueError):
        return None

    rates = [float(d[k]) for k in ("w_pre", "w_post") if d.get(k) is not None]

    if moved < MIN_MOVED or nic <= 0 or dur <= 0 or not rates:
        return None

    base = sum(rates) / len(rates)
    noise = abs(rates[0] - rates[1]) * dur if len(rates) == 2 else base * dur * 0.5          # (one baseline only: the whole card might have moved by half of it)

    if noise > MAX_NOISE * moved or base * dur > moved:
        return None

    net = nic - base * dur

    if net <= 0:
        return None

    ratio = net / moved

    if ratio > MAX_RATIO or ratio < MIN_RATIO:          # (far less crossed the card than was moved: the tunnel does not use the card of the default route - it says nothing)
        return None

    return round(max(1.0, ratio), 2)


def cost(kind: str, wire, raw: bool = False) -> tuple[float, str]:
    """Pure: (the cost, "measured" | "model") of a type on a pair. `wire` = the measured ratios kept for it (the median counts); `raw` = the type is tested over a link only (GRE / kernel link): its tests see the first
    layer, so the model decides."""
    vals = []

    for x in wire or []:
        try:
            vals.append(float(x))
        except (TypeError, ValueError):
            continue

    if vals and not raw:
        return round(median(vals), 2), "measured"

    return model_cost(kind), "model"


def lightness(c: float) -> float:
    """Pure: the factor a type's rank is multiplied by - 1 up to LIGHT_FLOOR, about a third for a cost of 2."""
    return (LIGHT_FLOOR / max(float(c), LIGHT_FLOOR)) ** LIGHT_EXP


def note(c: float, src: str) -> str:
    """Pure: the cost in words, for the reason sentences."""
    return f"about {c:.2f} bytes on the wire for every byte the users move ({'measured' if src == 'measured' else 'by its design'})"
