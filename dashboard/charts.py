import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

sns.set_theme(style="darkgrid")
TEAL = "#00C896"


def plot_gap_distribution(df: pd.DataFrame, output_path: str = "outputs/gap_distribution_chart.png") -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(df["gap"], bins=40, color=TEAL, edgecolor="white", alpha=0.85)
    ax.axvline(0.03, color="red", linestyle="--", label="+3% threshold")
    ax.axvline(-0.03, color="red", linestyle="--", label="-3% threshold")
    ax.set_xlabel("Gap (Kalshi prob − Vegas prob)")
    ax.set_ylabel("Game count")
    ax.set_title("Vegas vs. Kalshi Price Gap Distribution")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_win_rate_by_threshold(df: pd.DataFrame, output_path: str = "outputs/win_rate_by_threshold.png") -> None:
    thresholds = [i / 100 for i in range(1, 16)]
    rows = []
    for t in thresholds:
        flagged = df[df["abs_gap"] >= t]
        if len(flagged) < 10:
            continue
        wr = (
            ((flagged["gap"] > 0) & (flagged["result"] == "away_win")) |
            ((flagged["gap"] < 0) & (flagged["result"] == "home_win"))
        ).mean()
        rows.append({"threshold": t * 100, "win_rate": wr, "n": len(flagged)})

    plot_df = pd.DataFrame(rows)
    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax2 = ax1.twinx()
    ax1.plot(plot_df["threshold"], plot_df["win_rate"] * 100, color=TEAL, marker="o", label="Win rate %")
    ax2.bar(plot_df["threshold"], plot_df["n"], alpha=0.3, color="gray", label="Sample size")
    ax1.axhline(52, color="red", linestyle="--", label="52% minimum")
    ax1.set_xlabel("Gap threshold (%)")
    ax1.set_ylabel("Vegas win rate (%)")
    ax2.set_ylabel("Games in sample")
    ax1.set_title("Win Rate When Trading Vegas Side, by Gap Threshold")
    ax1.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_sport_breakdown(sport_df: pd.DataFrame, output_path: str = "outputs/sport_breakdown_table.png") -> None:
    fig, ax = plt.subplots(figsize=(6, 2))
    ax.axis("off")
    table = ax.table(
        cellText=sport_df.values,
        colLabels=sport_df.columns,
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1.4, 1.6)
    ax.set_title("Win Rate by Sport", fontweight="bold", pad=12)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
