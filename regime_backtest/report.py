"""Markdown tables and PNG charts. Nothing here computes a result; it only
formats what ``run.py`` produced."""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent

HOW_TO_READ = """\
- **CAGR**: compound annual growth rate, the steady yearly return that turns the starting value into the ending value.
- **Vol**: annualized standard deviation of daily returns; how bumpy the ride is.
- **Sharpe**: return above T-bills divided by volatility. Higher means more return per unit of risk. Differences under 0.03 are treated as no difference.
- **Sortino**: like Sharpe, but only downside moves count as risk.
- **Max DD**: the largest peak-to-trough fall in the strategy's value.
- **Calmar**: CAGR divided by the size of the max drawdown.
- **Worst 12m**: the worst return over any rolling 12-month (252 trading day) stretch.
- **% invested**: the share of days holding SPY rather than T-bills.
- **Switches**: moves between SPY and T-bills. Each one costs the stated basis points.
- **Avg hold**: the average length of an in-market stretch, in trading days.
- **bp per side**: cost per switch in basis points (1 bp = 0.01%).
- **90% interval**: the range the Sharpe difference falls in across 2,000 bootstrap resamples. If it includes zero, the difference can't be told apart from luck.
- **Common window vs longest history**: the common window uses the same dates for every rule, so rows are directly comparable. Longest history runs each rule from its own first usable day, so rows are not directly comparable."""


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M")


def git_commit(cwd: Path = REPO_ROOT) -> str:
    """Short HEAD hash, "-dirty" when the working tree has changes, "unknown"
    when git is unavailable or this is not a checkout."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=cwd, capture_output=True,
                             text=True, check=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True,
                               text=True, check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if not sha:
        return "unknown"
    return f"{sha}-dirty" if dirty else sha

# Fixed categorical order — a strategy keeps its colour on every chart.
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BENCH = "#52514e"
SURFACE = "#fcfcfb"
INK, INK2 = "#0b0b0b", "#52514e"


def pct(x, digits: int = 1) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{digits}f}%"


def num(x, digits: int = 2) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{digits}f}"


def md_table(header: list[str], rows: list[list[str]], align: str | None = None) -> str:
    align = align or ("l" + "r" * (len(header) - 1))
    sep = ["---:" if a == "r" else ":---" for a in align]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


SUMMARY_COLS = [
    ("Period", lambda m: f"{m['start']} → {m['end']}"),
    ("CAGR", lambda m: pct(m["cagr"])),
    ("Vol", lambda m: pct(m["vol"])),
    ("Sharpe", lambda m: num(m["sharpe"])),
    ("Sortino", lambda m: num(m["sortino"])),
    ("Max DD", lambda m: pct(m["max_dd"])),
    ("Calmar", lambda m: num(m["calmar"])),
    ("Worst 12m", lambda m: pct(m["worst_12m"])),
    ("% invested", lambda m: pct(m["pct_invested"], 0)),
    ("Switches", lambda m: str(m["switches"])),
    ("Avg hold (days)", lambda m: f"{m['avg_hold_days']:.0f}"),
]


def summary_table(stats: dict[str, dict]) -> str:
    header = ["Strategy"] + [c for c, _ in SUMMARY_COLS]
    rows = [[name] + [f(m) for _, f in SUMMARY_COLS] for name, m in stats.items() if m]
    return md_table(header, rows)


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c9c8c2")
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(True, color="#e6e5e0", linewidth=0.6)
    ax.set_axisbelow(True)


def plot_equity(frames: dict[str, pd.DataFrame], path: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 6), facecolor=SURFACE)
    _style(ax)
    for i, (name, df) in enumerate(frames.items()):
        eq = (1.0 + df["ret"]).cumprod()
        bench = i == 0
        ax.plot(eq.index, eq.values, lw=2.2 if bench else 1.5, label=name,
                color=BENCH if bench else COLORS[(i - 1) % len(COLORS)])
        ax.annotate(f"{eq.iloc[-1]:.1f}×", (eq.index[-1], eq.iloc[-1]), xytext=(4, 0),
                    textcoords="offset points", fontsize=8, color=INK2, va="center")
    ax.set_yscale("log")
    ax.set_ylabel("Growth of $1 (log scale)", color=INK2, fontsize=9)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)


def plot_drawdowns(frames: dict[str, pd.DataFrame], path: Path, title: str) -> None:
    """Small multiples: each strategy's drawdown over buy-and-hold's (grey)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .metrics import drawdown_series

    names = list(frames)
    bench_name, others = names[0], names[1:]
    bench_dd = drawdown_series(frames[bench_name]["ret"])
    n = max(1, len(others))
    fig, axes = plt.subplots(n, 1, figsize=(11, 1.9 * n + 0.6), sharex=True, sharey=True,
                             facecolor=SURFACE, squeeze=False)
    for i, name in enumerate(others):
        ax = axes[i, 0]
        _style(ax)
        dd = drawdown_series(frames[name]["ret"])
        ax.fill_between(bench_dd.index, bench_dd.values * 100, 0, color="#d6d5cf", lw=0,
                        label=f"{bench_name}")
        ax.plot(dd.index, dd.values * 100, color=COLORS[i % len(COLORS)], lw=1.3, label=name)
        ax.set_ylabel("%", color=INK2, fontsize=8)
        ax.text(0.005, 0.08, f"{name}  (max {dd.min() * 100:.0f}% vs {bench_dd.min() * 100:.0f}%)",
                transform=ax.transAxes, fontsize=8.5, color=INK)
    axes[0, 0].set_title(title + " — grey area: buy-and-hold", color=INK, fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)


def plot_heatmaps(panels: list[tuple[str, pd.DataFrame, float]], path: Path,
                  xlabel: str, ylabel: str) -> None:
    """One heatmap per period of Sharpe across the credit-rule grid. Colour is
    diverging around buy-and-hold's Sharpe for that period (grey = equal,
    blue = better, orange = worse); every cell is labelled with its value."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

    cmap = LinearSegmentedColormap.from_list("div", ["#c2491b", "#f0efec", "#1c5cab"])
    fig, axes = plt.subplots(1, len(panels), figsize=(5.2 * len(panels), 4.4), facecolor=SURFACE,
                             squeeze=False)
    for ax, (title, grid, bench) in zip(axes[0], panels):
        vals = grid.to_numpy(dtype=float)
        span = max(0.05, np.nanmax(np.abs(vals - bench)))
        norm = TwoSlopeNorm(vcenter=bench, vmin=bench - span, vmax=bench + span)
        ax.imshow(vals, cmap=cmap, norm=norm, aspect="auto")
        ax.set_xticks(range(grid.shape[1]), [str(c) for c in grid.columns], fontsize=8)
        ax.set_yticks(range(grid.shape[0]), [str(r) for r in grid.index], fontsize=8)
        ax.set_xlabel(xlabel, fontsize=8, color=INK2)
        ax.set_ylabel(ylabel, fontsize=8, color=INK2)
        for (r, c), v in np.ndenumerate(vals):
            ax.text(c, r, f"{v:.2f}", ha="center", va="center", fontsize=8.5, color=INK)
        ax.set_title(f"{title}\nSharpe; buy-and-hold = {bench:.2f}", fontsize=9, color=INK, loc="left")
        for s in ax.spines.values():
            s.set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
