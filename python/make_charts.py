"""Render docs/rate_sensitivity.png from dbo.vw_RateSensitivity.

Run the ETL for the current month first: the view uses the portfolio from the most recent run.
"""
import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from seed_tables import connect, load_env

DOCS = Path(__file__).resolve().parent.parent / "docs"
SURFACE, INK, MUTED, GRID, SERIES = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0", "#2a78d6"


def fetch():
    cur = connect().cursor()
    cur.execute("SELECT EffectiveDate, BaseInterestRate, Loans, EstimatedProfit, RateChange, ProfitChange "
                "FROM dbo.vw_RateSensitivity ORDER BY EffectiveDate")
    return [(r.EffectiveDate, float(r.BaseInterestRate), r.Loans, float(r.EstimatedProfit),
             None if r.RateChange is None else float(r.RateChange),
             None if r.ProfitChange is None else float(r.ProfitChange)) for r in cur.fetchall()]


def render(rows, out_name, figsize, scale=1.0, titled=True):
    """scale enlarges all type for small-screen use (carousel); titled=False leaves the headline to the slide."""
    months = [r[0] for r in rows]
    profit = [r[3] / 1000 for r in rows]                      # SEK thousands

    fig, ax = plt.subplots(figsize=figsize, dpi=160, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    ax.step(months, profit, where="post", color=SERIES, linewidth=2.4 * scale, solid_joinstyle="round")
    ax.plot(months[-1], profit[-1], "o", color=SERIES, markersize=8 * scale, markeredgecolor=SURFACE, markeredgewidth=2)

    # Direct labels on the moves: the rate change that caused each step, and what it cost.
    for month, rate, _, p, d_rate, d_profit in rows:
        if d_rate:
            ax.annotate(f"policy rate {rate - d_rate:.2f}% → {rate:.2f}%\n{d_profit / 1000:+,.0f} k SEK".replace(",", " "),
                        xy=(month, p / 1000), xytext=(14 * scale, 26 * scale), textcoords="offset points", color=INK, fontsize=10 * scale,
                        arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8))
    ax.annotate(f"{profit[-1]:,.0f} k SEK".replace(",", " "), xy=(months[-1], profit[-1]), xytext=(-8, 14),
                textcoords="offset points", ha="right", color=INK, fontsize=10 * scale, fontweight="bold")

    ax.set_ylim(profit[-1] * 0.9, profit[0] * 1.04)
    ax.set_ylabel("Estimated profit, SEK thousands", color=MUTED, fontsize=10 * scale)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}".replace(",", " "))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3 if scale == 1 else 6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(colors=MUTED, length=0, labelsize=9.5 * scale)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)

    if titled:
        fig.text(0.06, 0.95, "Two 25 bp Riksbank cuts took 12% off this loan book's estimated profit",
                 color=INK, fontsize=14, fontweight="bold", ha="left", va="top")
        fig.text(0.06, 0.895, f"Same {rows[0][2]} vehicles repriced at each month's real policy rate (synthetic prices, real rates)",
                 color=MUTED, fontsize=10, ha="left", va="top")
        fig.subplots_adjust(left=0.1, right=0.97, top=0.82, bottom=0.1)
    else:
        fig.subplots_adjust(left=0.17, right=0.97, top=0.97, bottom=0.1)
    fig.savefig(DOCS / out_name, facecolor=SURFACE)
    plt.close(fig)


def main():
    load_env()
    rows = fetch()
    DOCS.mkdir(exist_ok=True)
    render(rows, "rate_sensitivity.png", (10, 5.6))
    render(rows, "rate_sensitivity_carousel.png", (8, 7.2), scale=1.3, titled=False)

    # Same numbers for the README / slides, so no figure is ever retyped by hand.
    (DOCS / "rate_sensitivity.json").write_text(json.dumps(
        [dict(month=r[0].isoformat(), rate=r[1], loans=r[2], estimated_profit=r[3],
              rate_change=r[4], profit_change=r[5]) for r in rows], indent=2))
    first, last = rows[0][3], rows[-1][3]
    print(f"{rows[0][0]} {first:,.2f} -> {rows[-1][0]} {last:,.2f}  ({(last / first - 1) * 100:+.1f}%)")


if __name__ == "__main__":
    main()
