"""Set building: pick and order tracks for a target duration, rising tempo/energy, harmonic mixing."""
import numpy as np

# ------------------------------------------------------------ Camelot

KEYS = [f"{n}{l}" for n in range(1, 13) for l in "AB"]
KIDX = {k: i for i, k in enumerate(KEYS)}


def key_cost(a, b):
    if a not in KIDX or b not in KIDX:
        return 3.0
    na, la, nb, lb = int(a[:-1]), a[-1], int(b[:-1]), b[-1]
    dn = (nb - na) % 12
    if na == nb and la == lb: return 0.0
    if la == lb and dn in (1, 11): return 0.6       # +-1 on the wheel
    if na == nb: return 0.8                         # relative major/minor
    if la == lb and dn == 2: return 2.0             # energy boost
    if la != lb and dn in (1, 11): return 2.8       # diagonal
    if la == lb and dn == 7: return 3.5
    return 9.0


KC = np.array([[key_cost(a, b) for b in KEYS + ["?"]] for a in KEYS + ["?"]])


def transition_level(a, b):
    """'ok' / 'moyen' / 'clash' for the UI."""
    k = key_cost(a["camelot"], b["camelot"])
    db = b["bpm"] - a["bpm"]
    if k >= 9 or db < -2.5 or db > 4:
        return "clash"
    if k >= 2 or db < -0.6 or db > 2.1:
        return "moyen"
    return "ok"


STRICTNESS = {"stricte": 3.5, "normale": 2.2, "souple": 1.0}


# ------------------------------------------------------------ curves

def bpm_curve(start, end, shape):
    d = end - start
    if shape == "plateau":
        pts = [(0, start), (0.25, start + 0.5 * d), (0.85, start + 0.8 * d), (1, end)]
    elif shape == "lineaire":
        pts = [(0, start), (1, end)]
    else:  # progressive: soft warm-up, steady climb, peak at the end
        pts = [(0, start), (0.2, start + 0.25 * d), (0.45, start + 0.55 * d), (0.72, start + 0.75 * d), (1, end)]
    xs, ys = zip(*pts)
    return lambda f: float(np.interp(f, xs, ys))


def energy_curve(f):
    return float(np.interp(f, [0, 0.17, 0.5, 0.83, 1], [-1.0, -0.4, 0.2, 0.7, 0.9]))


# ------------------------------------------------------------ data prep

class Pool:
    def __init__(self, tracks):
        self.t = tracks
        self.ids = [t["id"] for t in tracks]
        self.pos = {t["id"]: i for i, t in enumerate(tracks)}
        self.bpm = np.array([t["bpm"] for t in tracks], dtype=float)
        self.energy = np.array([t["energy"] for t in tracks], dtype=float)
        self.score = np.array([t["score"] for t in tracks], dtype=float)
        self.play = np.array([t["play"] for t in tracks], dtype=float) / 60.0
        self.key = np.array([KIDX.get(t["camelot"], 24) for t in tracks])
        fams = {}
        self.fam = np.array([fams.setdefault(t["family"], len(fams)) for t in tracks])
        self.nfam = len(fams)


def play_minutes(duration_s):
    """Time a track is heard alone in a mix: its length minus the blend with the next one."""
    return duration_s - min(60.0, max(25.0, 0.15 * duration_s))


# ------------------------------------------------------------ set generation

def build_set(tracks, duration_min=90, bpm_start=122, bpm_end=129, shape="progressive", strictness="normale",
              prefix=(), beam=2500):
    """Beam search over candidate sequences. `prefix` ids are kept as the fixed start of the set."""
    pool = Pool([t for t in tracks if not t.get("excluded")])
    n = len(pool.ids)
    if n == 0:
        return []
    kw = STRICTNESS.get(strictness, 2.2)
    tb = bpm_curve(bpm_start, bpm_end, shape)
    # make the beam narrower on big libraries so generation stays a few seconds
    beam = int(max(300, min(beam, 1_500_000 / max(1, n * max(8, duration_min / 3.5)))))

    used0 = np.zeros(n, bool)
    fam0 = np.zeros(pool.nfam, bool)
    m0 = cost0 = 0.0
    last0 = -1
    seq0 = []
    for tid in prefix:
        if tid in pool.pos:
            i = pool.pos[tid]
            used0[i] = True; fam0[pool.fam[i]] = True
            m0 += pool.play[i]; seq0.append(i); last0 = i
    forced = np.array([bool(t.get("forced")) for t in pool.t])

    beams = [(cost0, seq0, used0, fam0, m0, last0)]
    done = []
    lo, hi = duration_min - 1.5, duration_min + 3
    if m0 >= lo:
        return [pool.ids[i] for i in seq0]
    while beams:
        cand = []
        for bi, (c, seq, used, fam, m, last) in enumerate(beams):
            f = min(1.0, m / duration_min)
            cost = 1.5 * np.abs(pool.bpm - tb(f)) + 0.8 * np.abs(pool.energy - energy_curve(f)) - pool.score
            if last >= 0:
                cost = cost + kw * KC[pool.key[last], pool.key]
                db = pool.bpm - pool.bpm[last]
                cost = cost + np.where(db < -0.6, 6 + 4 * np.abs(db), 0) + np.where(db > 2.1, 3 * (db - 2), 0)
            elif not seq:
                cost = cost + np.where(pool.energy > -0.2, 4.0, 0.0)   # open soft
            # forced tracks: pull them in before the set fills up
            cost = cost - np.where(forced, 25.0, 0.0)
            cost = np.where(used | fam[pool.fam], np.inf, cost)
            k = min(len(cost), 60)
            best = np.argpartition(cost, k - 1)[:k]
            for i in best:
                if np.isfinite(cost[i]):
                    cand.append((c + float(cost[i]), bi, int(i)))
        if not cand:
            break
        cand.sort(key=lambda x: x[0])
        nxt = []
        for c, bi, i in cand:
            _, seq, used, fam, m, _ = beams[bi]
            nm = m + pool.play[i]
            if nm >= lo:
                if nm <= hi:
                    done.append((c, seq + [i]))
                continue
            if len(nxt) < beam:
                u = used.copy(); u[i] = True
                fm = fam.copy(); fm[pool.fam[i]] = True
                nxt.append((c, seq + [i], u, fm, nm, i))
        beams = nxt
        if len(done) > beam * 4:
            break
    if not done:  # library shorter than the target: take the longest path found
        return [pool.ids[i] for i in (beams[0][1] if beams else seq0)]
    return [pool.ids[i] for i in min(done, key=lambda x: x[0])[1]]


# ------------------------------------------------------------ ordering every track (extended set)

def order_all(tracks, after=None, beam=1200):
    """Order every given track as one harmonic, rising wave (no track dropped)."""
    if not tracks:
        return []
    pool = Pool(tracks)
    n = len(pool.ids)
    beam = int(max(150, min(beam, 400_000 / max(1, n * n / 4))))
    prev_key = KIDX.get(after["camelot"], 24) if after else 24
    beams = [(0.0, [], np.zeros(n, bool), -1)]
    for _ in range(n):
        cand = []
        for bi, (c, seq, used, last) in enumerate(beams):
            floor = pool.bpm[~used].min()
            if last < 0:
                cost = 2.2 * KC[prev_key, pool.key] + 0.8 * (pool.bpm - floor)   # each wave restarts low
            else:
                db = pool.bpm - pool.bpm[last]
                cost = (2.2 * KC[pool.key[last], pool.key] + np.where(db < -0.6, 25 + 10 * np.abs(db), 0)
                        + np.where(db > 2.1, 3 * (db - 2), 0))
            cost = cost + 1.5 * np.maximum(0, pool.bpm - floor - 1.5)  # don't leave slow tracks behind
            cost = np.where(used, np.inf, cost)
            k = min(n, 30)
            for i in np.argpartition(cost, k - 1)[:k]:
                if np.isfinite(cost[i]):
                    cand.append((c + float(cost[i]), bi, int(i)))
        cand.sort(key=lambda x: x[0])
        nxt = []
        for c, bi, i in cand[:beam]:
            _, seq, used, _ = beams[bi]
            u = used.copy(); u[i] = True
            nxt.append((c, seq + [i], u, i))
        beams = nxt
    return [pool.ids[i] for i in beams[0][1]]


def build_extended(tracks, set_ids, known_min=4, wave_min=90):
    """Set first, then remaining well-known tracks, then the rest in rising waves, then tracks to avoid."""
    by = {t["id"]: t for t in tracks}
    rest = [t for t in tracks if t["id"] not in set(set_ids)]
    avoid = [t for t in rest if t.get("excluded") or t.get("short") or t.get("duplicate_in_set")
             or t.get("health") == "bad"]
    rest = [t for t in rest if t not in avoid]
    known = [t for t in rest if t["known"] >= known_min]
    others = sorted([t for t in rest if t["known"] < known_min], key=lambda t: t["bpm"])
    blocks = [{"name": "Set", "ids": list(set_ids)}]
    last = by[set_ids[-1]] if set_ids else None
    if known:
        ids = order_all(known, last)
        blocks.append({"name": "Suite – titres connus", "ids": ids})
        last = by[ids[-1]]
    total = sum(t["play"] for t in others) / 60
    waves = max(1, round(total / wave_min))
    for w in range(waves):
        part = others[w::waves]  # interleave by tempo so every wave spans the full BPM range
        if part:
            ids = order_all(part, last)
            blocks.append({"name": f"Reste – vague {w + 1}" if waves > 1 else "Reste", "ids": ids})
            last = by[ids[-1]]
    if avoid:
        blocks.append({"name": "À éviter (extraits, doublons, fichiers abîmés, exclus)",
                       "ids": [t["id"] for t in sorted(avoid, key=lambda t: t["bpm"])]})
    return blocks


# ------------------------------------------------------------ editing helpers

def suggestions(tracks, set_ids, position, limit=8):
    """Tracks that would fit at `position` between its neighbours."""
    by = {t["id"]: t for t in tracks}
    prev = by.get(set_ids[position - 1]) if position > 0 else None
    nxt = by.get(set_ids[position + 1]) if position + 1 < len(set_ids) else None
    cur = by.get(set_ids[position])
    used = set(set_ids)
    target = np.mean([x["bpm"] for x in (prev, nxt) if x] or [cur["bpm"]])
    out = []
    for t in tracks:
        if t["id"] in used or t.get("excluded"):
            continue
        c = 1.2 * abs(t["bpm"] - target) - t["score"]
        if prev: c += 2.2 * key_cost(prev["camelot"], t["camelot"])
        if nxt: c += 2.2 * key_cost(t["camelot"], nxt["camelot"])
        out.append((c, t["id"]))
    out.sort()
    return [i for _, i in out[:limit]]
