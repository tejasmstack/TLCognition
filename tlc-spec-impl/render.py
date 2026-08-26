"""Dashboard rendering for phase E/F output. Spec 8-preamble + 11.

Findings are drawn as translucent band highlights + numbered badges, never circles
(trap 12.13: circle annotations failed with the actual user, and plt.Circle renders
as a sliver in aspect='auto' axes anyway).
"""
import sys, os, glob, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, cv2
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import gridspec
from matplotlib.patches import Rectangle, FancyBboxPatch

from tlccore.normalize import find_plate, to_od
from tlccore.gate import run_gate
from tlccore.structure import analyse_structure
from tlccore.extract import extract
from tlccore.graph import vertical_span, to_rf, vmax_for
from tlccore.lanes import resolve_roles, structural_crosscheck, VLM_LABEL_CACHE

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(ROOT, "out", "figures"); os.makedirs(OUT, exist_ok=True)
SH = {'MEHQ-P29-4hr_29th July26':'P29','MEHQ-P30-4hr_29th July26':'P30',
      'MEHQ-P31-4hr_30th July26':'P31','MEHQ-P32-1_3hr_4th Aug26_ACN':'P32-1',
      'MEHQ-P32-4hr_30th July26':'P32','MEHQ-P32_4+3hr_3rd Aug26':'P32b',
      'MEHQ-P33 4hr_31st July26':'P33'}
plt.rcParams.update({"font.size":8,"axes.spines.top":False,"axes.spines.right":False,
                     "figure.dpi":170})
TIER_C = {"confirmed":"#dc2626", "candidate":"#d97706"}
NEAR_TXT = "#7c3aed"   # flagged: sits beside the handwritten header

def load(path):
    bgr = cv2.imread(path); pl = find_plate(bgr); od = to_od(pl.warped)
    st = analyse_structure(od.od, od.noise); ex = extract(od.od, od.noise, st)
    return bgr, pl, od, st, ex

def roles_for(sid, n, ex):
    c = VLM_LABEL_CACHE[sid]
    labels = (c["labels"] + ["?"]*n)[:n]; confs = (c["conf"] + [0.0]*n)[:n]
    r = resolve_roles(labels, confs)
    return structural_crosscheck(r, [sum(1 for s in l.spots if s.tier=="confirmed") for l in ex.lanes])

def draw_pixel_graph(ax, od, st, ex, roles, title=None, badges=True):
    """The centrepiece: the OD array itself, rendered against position axes."""
    y_top, y_bot = vertical_span(od.od, st, ex)
    sub = od.od[y_top:y_bot+1]
    rf_top = to_rf(y_top, st, y_top, y_bot); rf_bot = to_rf(y_bot, st, y_top, y_bot)
    w = od.od.shape[1]
    ax.imshow(sub, cmap="gray_r", vmin=0, vmax=vmax_for(od.od), aspect="auto",
              interpolation="nearest", extent=[0, w, rf_bot, rf_top])
    # origin (dashed at 0) and front (solid blue at Rf 1) drawn INSIDE the graph
    if st.origin is not None:
        ax.axhline(to_rf(st.origin.y, st, y_top, y_bot), color="#111827", ls="--", lw=1.0)
        ax.text(w*0.995, to_rf(st.origin.y, st, y_top, y_bot), " origin", ha="right", va="bottom",
                fontsize=6.2, color="#111827")
    if st.front is not None:
        yf = to_rf(st.front.y, st, y_top, y_bot)
        ax.axhline(yf, color="#2563eb", ls="-", lw=1.2)
        ax.text(w*0.995, yf, " front", ha="right", va="bottom", fontsize=6.2, color="#2563eb")
    # findings: translucent band highlight per spot + numbered badge
    k = 1
    for l, r in zip(ex.lanes, roles):
        L = st.lanes[l.index]
        for s in l.spots:
            rf = to_rf(s.y, st, y_top, y_bot)
            hw = max(L.half_width, 0.02*w)
            band_h = max(2.2*s.sigma/max(y_bot-y_top,1), 0.012)
            ax.add_patch(Rectangle((L.cx-hw, rf-band_h/2), 2*hw, band_h,
                        facecolor=TIER_C[s.tier], alpha=0.16 if s.tier=="confirmed" else 0.10,
                        edgecolor=NEAR_TXT if s.near_text_band else TIER_C[s.tier],
                        lw=1.3 if s.near_text_band else 0.6,
                        ls="-" if s.tier=="confirmed" else ":"))
            if badges:
                ax.text(L.cx, rf, str(k), ha="center", va="center", fontsize=5.6,
                        color="white", zorder=6,
                        bbox=dict(boxstyle="circle,pad=0.16", fc=TIER_C[s.tier], ec="none"))
            k += 1
    for l, r in zip(ex.lanes, roles):
        L = st.lanes[l.index]
        ax.axvline(L.cx - max(L.half_width,0.02*w), color="#94a3b8", lw=0.4, alpha=0.7)
        ax.axvline(L.cx + max(L.half_width,0.02*w), color="#94a3b8", lw=0.4, alpha=0.7)
    ax.set_xticks([st.lanes[l.index].cx for l in ex.lanes])
    ax.set_xticklabels([f"{r.name_raw}\n{r.role.split('_')[0].lower()}" for r in roles], fontsize=6.6)
    ax.set_ylabel("Rf" if st.front is not None else "relative position\n(origin 0 -> top of band 1)",
                  fontsize=7.5)
    ax.set_ylim(rf_bot, rf_top)
    if title: ax.set_title(title, fontsize=8.5)
    return y_top, y_bot

def per_plate_figure(path, sid):
    bgr, pl, od, st, ex = load(path)
    g = run_gate(pl.warped, od.od, od.noise, pl.src_height)
    roles = roles_for(sid, len(st.lanes), ex)
    n = len(ex.lanes)
    fig = plt.figure(figsize=(11.6, 4.9))
    gs = gridspec.GridSpec(1, 3 + n, width_ratios=[1.0, 1.0, 1.5] + [0.85]*n, wspace=0.42)

    # panel 1: as shot
    a0 = fig.add_subplot(gs[0]); a0.imshow(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    a0.set_title(f"{sid} as shot\n{bgr.shape[1]}x{bgr.shape[0]} px", fontsize=8)
    a0.set_xticks([]); a0.set_yticks([])

    # panel 2: structure overlay on the standardised warp
    a1 = fig.add_subplot(gs[1]); a1.imshow(cv2.cvtColor(pl.warped, cv2.COLOR_BGR2RGB))
    h, w = od.od.shape
    for L in st.lanes:
        c = "#22c55e" if (L.from_dot or L.from_projection) else "#f59e0b"
        a1.axvline(L.cx, color=c, lw=0.9, ls="-" if L.quality > 0.5 else "--")
    for d in st.dots:
        a1.plot(d.x, d.y, marker="o", ms=3.4, mfc="none", mec="#ef4444", mew=1.0)
    if st.origin is not None: a1.axhline(st.origin.y, color="#111827", ls="--", lw=1.0)
    if st.front is not None:  a1.axhline(st.front.y,  color="#2563eb", lw=1.2)
    a1.set_title(f"B+C: warp {w}x{h}\norigin={st.origin_source}, front={st.front_source}", fontsize=7.6)
    a1.set_xticks([]); a1.set_yticks([])

    # panel 3: the pixel-faithful graph
    a2 = fig.add_subplot(gs[2])
    draw_pixel_graph(a2, od, st, ex, roles,
                     title=f"E: pixel-faithful graph\nnoise={od.noise:.4f} OD, {100*od.sat_frac:.0f}% clipped")

    # panels 4..: per-lane densitograms
    y_top, y_bot = vertical_span(od.od, st, ex)
    for i, (l, r) in enumerate(zip(ex.lanes, roles)):
        ax = fig.add_subplot(gs[3+i])
        yy = np.arange(ex.y0, ex.y0+len(l.profile))
        rf = np.array([to_rf(v, st, y_top, y_bot) for v in yy])
        ax.plot(l.profile, rf, color="#2563eb", lw=1.0)
        ax.axvline(3*l.sigma_noise, color="#dc2626", ls=":", lw=0.7)
        ax.axvline(2*l.sigma_noise, color="#d97706", ls=":", lw=0.7)
        for s in l.spots:
            srf = to_rf(s.y, st, y_top, y_bot)
            ax.plot(s.amp, srf, "o", ms=3.2, color=TIER_C[s.tier])
            ax.annotate(f"{s.share_pct:.0f}%", (s.amp, srf), textcoords="offset points",
                        xytext=(4,-1), fontsize=5.6, color=TIER_C[s.tier])
        ttl = f"{r.name_raw}"
        if l.is_streak: ttl += "\nSTREAK: % withheld"
        ax.set_title(ttl, fontsize=7, color="#dc2626" if l.is_streak else "#0f172a")
        ax.set_ylim(min(rf.min(), 0), max(rf.max(), 1.0) if st.front else rf.max())
        ax.tick_params(labelsize=6)
        if i == 0: ax.set_ylabel("Rf" if st.front is not None else "rel. pos", fontsize=7)
        else: ax.set_yticklabels([])
        ax.set_xlabel("OD", fontsize=6.5)
    plt.tight_layout()
    p = os.path.join(OUT, f"{sid}_dashboard.png"); plt.savefig(p, bbox_inches="tight"); plt.close()
    return p

def summary_sheet(paths):
    fig, axes = plt.subplots(1, 7, figsize=(15.5, 5.4))
    for ax, (path, sid) in zip(axes, paths):
        bgr, pl, od, st, ex = load(path)
        roles = roles_for(sid, len(st.lanes), ex)
        draw_pixel_graph(ax, od, st, ex, roles, title=None, badges=False)
        nc = sum(1 for l in ex.lanes for s in l.spots if s.tier=="confirmed")
        ncd = sum(1 for l in ex.lanes for s in l.spots if s.tier=="candidate")
        ax.set_title(f"{sid}\n{len(ex.lanes)} lanes, {nc} confirmed, {ncd} candidate\n"
                     f"{100*od.sat_frac:.0f}% clipped", fontsize=7.4)
        if ax is not axes[0]: ax.set_ylabel("")
    plt.suptitle("Phase E - pixel-faithful graphs, all seven plates  "
                 "(red band = confirmed spot >=3 sigma, amber dotted = candidate 2-3 sigma)",
                 fontsize=9.5, y=1.02)
    plt.tight_layout()
    p = os.path.join(OUT, "ALL_pixel_graphs.png"); plt.savefig(p, bbox_inches="tight"); plt.close()
    return p

if __name__ == "__main__":
    files = sorted(glob.glob(os.path.join(ROOT, "data", "*.png")))
    pairs = [(f, SH[os.path.splitext(os.path.basename(f))[0]]) for f in files]
    for f, sid in pairs:
        print("wrote", per_plate_figure(f, sid))
    print("wrote", summary_sheet(pairs))
