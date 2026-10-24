#!/usr/bin/env python3
"""MAT401 -- analysis, validation, tables and plots for download-time sequences."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# LOCKED EXPERIMENT SETTINGS
# ============================================================

LOCKED_SIZES = [
    1_000_000,
    5_000_000,
    10_000_000,
    20_000_000,
]

N_ANALYSIS = 30
N_TOTAL = 40

EPSILONS = [0.05, 0.10, 0.20]

FAILURE_FLAG_THRESHOLD = 3

BLOCKS = [
    (1, 10),
    (11, 20),
    (21, 30),
]


OBS_REQUIRED = {
    "session_id",
    "file_size_bytes",
    "n",
    "block",
    "round_index",
    "attempt_number",
    "timestamp",
    "download_time_seconds",
    "bytes_received",
    "throughput_mbps",
}


RAW_REQUIRED = {
    "attempt_id",
    "session_id",
    "timestamp",
    "file_size_bytes",
    "round_index",
    "block",
    "attempt_number",
    "observation_n",
    "url",
    "elapsed_seconds",
    "bytes_received",
    "http_status",
    "success",
    "error",
    "retry_of",
    "content_encoding",
}


# ============================================================
# MATHEMATICAL HELPERS
# ============================================================

def running_mean(xs):
    out = []
    total = 0.0

    for i, x in enumerate(xs, 1):
        total += x
        out.append(total / i)

    return out


def stability_K(errors, epsilon, ref):
    """
    Finite stability index:

        K(epsilon) = min K <= 30 such that
        E_n <= epsilon * reference
        for every n from K through 30.

    Returns None if the condition is never achieved.
    """

    if not math.isfinite(ref) or ref <= 0:
        return None

    threshold = epsilon * ref

    for k in range(1, len(errors) + 1):
        if all(
            e <= threshold
            for e in errors[k - 1:]
        ):
            return k

    return None


# ============================================================
# SERIES ANALYSIS
# ============================================================

def analyze_series(rows):
    rows = sorted(
        rows,
        key=lambda r: int(r["n"]),
    )

    analysis_rows = rows[:N_ANALYSIS]
    reference_rows = rows[N_ANALYSIS:N_TOTAL]

    ts = [
        float(r["download_time_seconds"])
        for r in analysis_rows
    ]

    ref = [
        float(r["download_time_seconds"])
        for r in reference_rows
    ]

    S = int(
        analysis_rows[0]["file_size_bytes"]
    )

    L_ref = statistics.mean(ref)

    # R_n = 8S / T_n
    throughput = [
        8 * S / t / 1e6
        for t in ts
    ]

    M = running_mean(ts)

    E = [
        abs(t - L_ref)
        for t in ts
    ]

    relative_error = [
        e / L_ref
        for e in E
    ]

    return {
        "S": S,

        "ts": ts,
        "R": throughput,
        "M": M,
        "E": E,
        "rel": relative_error,

        "L_ref": L_ref,

        "R_ref_mbps": (
            8 * S / L_ref / 1e6
        ),

        "sec_per_MB": (
            L_ref / (S / 1e6)
        ),

        "K5": stability_K(
            E,
            0.05,
            L_ref,
        ),

        "K10": stability_K(
            E,
            0.10,
            L_ref,
        ),

        "K20": stability_K(
            E,
            0.20,
            L_ref,
        ),

        "within5": (
            sum(x <= 0.05 for x in relative_error)
            / N_ANALYSIS
        ),

        "within10": (
            sum(x <= 0.10 for x in relative_error)
            / N_ANALYSIS
        ),

        "within20": (
            sum(x <= 0.20 for x in relative_error)
            / N_ANALYSIS
        ),

        "mean": statistics.mean(ts),
        "median": statistics.median(ts),
        "sd": statistics.stdev(ts),
        "min": min(ts),
        "max": max(ts),

        "ref_sd": statistics.stdev(ref),

        "ref_cv": (
            statistics.stdev(ref)
            / L_ref
        ),

        "blocks": [
            statistics.mean(
                ts[i - 1:j]
            )
            for i, j in BLOCKS
        ],
    }


# ============================================================
# CSV READING
# ============================================================

def read_csv(path, required):
    with open(
        path,
        newline="",
        encoding="utf-8",
    ) as f:

        reader = csv.DictReader(f)

        fields = set(
            reader.fieldnames or []
        )

        missing = required - fields

        if missing:
            raise ValueError(
                f"{path}: missing required columns: "
                f"{sorted(missing)}"
            )

        return list(reader)


def load_raw_for(obs_path):
    raw_path = (
        Path(obs_path)
        .with_name("raw_attempts.csv")
    )

    if raw_path.exists():
        return read_csv(
            raw_path,
            RAW_REQUIRED,
        )

    return []


# ============================================================
# VALIDATION
# ============================================================

def validate_group(rows, raw, source):
    issues = []

    try:
        ns = sorted(
            int(r["n"])
            for r in rows
        )

        if ns != list(range(1, 41)):
            issues.append(
                "n must be exactly 1..40; "
                f"got {ns[:5]}...{ns[-5:]}"
            )

        if len(rows) != N_TOTAL:
            issues.append(
                "requires exactly 40 valid "
                f"observations; got {len(rows)}"
            )

        for r in rows:
            S = int(
                r["file_size_bytes"]
            )

            n = int(r["n"])

            t = float(
                r["download_time_seconds"]
            )

            b = int(
                r["bytes_received"]
            )

            q = float(
                r["throughput_mbps"]
            )

            if S not in LOCKED_SIZES:
                issues.append(
                    f"invalid size {S}"
                )

            if (
                not math.isfinite(t)
                or t <= 0
            ):
                issues.append(
                    f"invalid time n={n}"
                )

            if b != S:
                issues.append(
                    f"wrong byte count n={n}: "
                    f"{b} != {S}"
                )

            expected = (
                8 * S
                / (t * 1e6)
            )

            if (
                not math.isfinite(q)
                or abs(q - expected)
                > max(
                    1e-6,
                    abs(expected) * 1e-5,
                )
            ):
                issues.append(
                    f"throughput mismatch n={n}"
                )

            expected_block = (
                "analysis"
                if n <= N_ANALYSIS
                else "reference"
            )

            if r["block"] != expected_block:
                issues.append(
                    f"wrong block n={n}: "
                    f"{r['block']}"
                )

        if len(
            {r["n"] for r in rows}
        ) != len(rows):
            issues.append(
                "duplicate n values"
            )

        # Every valid mathematical observation
        # must have a successful raw attempt.
        raw_success = {
            (
                r["session_id"],
                int(r["file_size_bytes"]),
                int(r["observation_n"]),
            )
            for r in raw
            if r.get("success") == "1"
        }

        for r in rows:
            key = (
                r["session_id"],
                int(r["file_size_bytes"]),
                int(r["n"]),
            )

            if key not in raw_success:
                issues.append(
                    f"observation n={r['n']} "
                    "has no matching successful "
                    "raw attempt"
                )

    except (
        ValueError,
        TypeError,
        ZeroDivisionError,
    ) as e:

        issues.append(
            f"parse error: {e}"
        )

    if not issues:
        status = "VALID ANALYZABLE"

    elif len(rows) < N_TOTAL:
        status = "INCOMPLETE"

    else:
        status = "INVALID"

    return status, issues


def validate_all(obs_paths):
    groups = {}
    raw_groups = {}

    for path in obs_paths:
        rows = read_csv(
            path,
            OBS_REQUIRED,
        )

        raw = load_raw_for(path)

        for r in rows:
            key = (
                r["session_id"],
                int(r["file_size_bytes"]),
            )

            groups.setdefault(
                key,
                [],
            ).append(r)

        for r in raw:
            key = (
                r["session_id"],
                int(r["file_size_bytes"]),
            )

            raw_groups.setdefault(
                key,
                [],
            ).append(r)

    statuses = []

    for key, rows in sorted(
        groups.items()
    ):

        raw = raw_groups.get(
            key,
            [],
        )

        status, issues = validate_group(
            rows,
            raw,
            "group",
        )

        failed = sum(
            1
            for r in raw
            if r.get("success") != "1"
        )

        if (
            failed > FAILURE_FLAG_THRESHOLD
            and status == "VALID ANALYZABLE"
        ):
            status = "FLAGGED"

        statuses.append(
            (
                key[0],
                key[1],
                len(rows),
                failed,
                status,
                issues,
            )
        )

    return groups, statuses


# ============================================================
# SELF TEST
# ============================================================

def selftest():

    assert (
        stability_K(
            [1, 1, 1, 5, 1],
            2,
            1,
        )
        == 5
    )

    assert (
        stability_K(
            [5, 1, 1, 1],
            2,
            1,
        )
        == 2
    )

    assert abs(
        running_mean(
            [9, 11, 9, 11]
        )[-1] - 10
    ) < 1e-12

    S = 5_000_000
    R = 20_000_000

    L = 8 * S / R

    t = L * 1.1
    r = 8 * S / t

    assert abs(
        abs(t - L) / L
        - abs(r - R) / r
    ) < 1e-12

    raw = [
        10 + (-1) ** n
        for n in range(1, 41)
    ]

    assert abs(
        running_mean(raw)[-1] - 10
    ) < 1e-12

    assert (
        stability_K(
            [0] * 29 + [10],
            0.2,
            1,
        )
        is None
    )

    print("SELFTEST: PASS")


# ============================================================
# OUTPUT WRITER
# ============================================================

def write_outputs(out, results):
    out.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Always sort the four locked sizes.
    results = sorted(
        results,
        key=lambda x: x["S"],
    )

    def write_table(
        filename,
        headers,
        rows,
    ):
        with open(
            out / filename,
            "w",
            newline="",
            encoding="utf-8",
        ) as f:

            writer = csv.writer(
                f,
                lineterminator="\n",
            )

            writer.writerows(
                [headers, *rows]
            )

    # --------------------------------------------------------
    # TABLE 1
    # --------------------------------------------------------

    write_table(
        "table1_reference_values.csv",

        [
            "file_size_bytes",
            "L_ref_seconds",
            "R_ref_mbps",
            "seconds_per_MB",
            "reference_sd",
            "reference_cv",
        ],

        [
            [
                x["S"],
                x["L_ref"],
                x["R_ref_mbps"],
                x["sec_per_MB"],
                x["ref_sd"],
                x["ref_cv"],
            ]
            for x in results
        ],
    )

    # --------------------------------------------------------
    # TABLE 2
    # --------------------------------------------------------

    write_table(
        "table2_stability.csv",

        [
            "file_size_bytes",
            "K_5pct",
            "K_10pct",
            "K_20pct",
            "fraction_5pct",
            "fraction_10pct",
            "fraction_20pct",
        ],

        [
            [
                x["S"],
                x["K5"],
                x["K10"],
                x["K20"],
                x["within5"],
                x["within10"],
                x["within20"],
            ]
            for x in results
        ],
    )

    # --------------------------------------------------------
    # TABLE 3
    # --------------------------------------------------------

    write_table(
        "table3_block_comparison.csv",

        [
            "file_size_bytes",
            "block_1_10_mean",
            "block_11_20_mean",
            "block_21_30_mean",
        ],

        [
            [
                x["S"],
                *x["blocks"],
            ]
            for x in results
        ],
    )

    # --------------------------------------------------------
    # TABLE 4
    # --------------------------------------------------------

    largest_R = results[-1]["R_ref_mbps"]

    write_table(
        "table4_file_size_analysis.csv",

        [
            "file_size_bytes",
            "L_ref_seconds",
            "R_ref_mbps",
            "seconds_per_MB",
            "predicted_time_at_largest_R_seconds",
            "observed_minus_predicted_seconds",
        ],

        [
            [
                x["S"],
                x["L_ref"],
                x["R_ref_mbps"],
                x["sec_per_MB"],

                8 * x["S"]
                / (largest_R * 1e6),

                x["L_ref"]
                - (
                    8 * x["S"]
                    / (largest_R * 1e6)
                ),
            ]
            for x in results
        ],
    )

    # --------------------------------------------------------
    # SEQUENCE TABLE
    # --------------------------------------------------------

    sequence_rows = []

    for x in results:

        for (
            n,
            t,
            r,
            m,
            e,
            rel,
        ) in zip(
            range(1, N_ANALYSIS + 1),
            x["ts"],
            x["R"],
            x["M"],
            x["E"],
            x["rel"],
        ):

            sequence_rows.append(
                [
                    x["S"],
                    n,
                    t,
                    r,
                    m,
                    e,
                    rel,
                ]
            )

    write_table(
        "table_sequences_n1_30.csv",

        [
            "file_size_bytes",
            "n",
            "T_seconds",
            "R_mbps",
            "M_n_seconds",
            "E_n_seconds",
            "relative_error",
        ],

        sequence_rows,
    )


# ============================================================
# TABLE 5 -- SESSION COMPARISON
# ============================================================

def write_session_comparison(
    out,
    by_session,
):
    """
    Compare empirical reference values between sessions.

    This table is descriptive. Neither session-specific
    reference value is treated as the mathematical limit.
    """

    sessions = sorted(
        by_session.keys()
    )

    # A comparison requires at least two sessions.
    if len(sessions) < 2:
        return

    lookup = {
        session: {
            x["S"]: x
            for x in results
        }
        for session, results
        in by_session.items()
    }

    rows = []

    for (
        session_a,
        session_b,
    ) in combinations(
        sessions,
        2,
    ):

        common_sizes = sorted(
            set(
                lookup[session_a]
            )
            & set(
                lookup[session_b]
            )
        )

        for S in common_sizes:

            a = lookup[
                session_a
            ][S]

            b = lookup[
                session_b
            ][S]

            L_a = a["L_ref"]
            L_b = b["L_ref"]

            R_a = a["R_ref_mbps"]
            R_b = b["R_ref_mbps"]

            absolute_time_difference = (
                abs(L_b - L_a)
            )

            relative_time_difference = (
                absolute_time_difference
                / L_a
            )

            absolute_throughput_difference = (
                abs(R_b - R_a)
            )

            relative_throughput_difference = (
                absolute_throughput_difference
                / R_a
            )

            rows.append(
                [
                    S,
                    session_a,
                    session_b,

                    L_a,
                    L_b,

                    absolute_time_difference,
                    relative_time_difference,

                    R_a,
                    R_b,

                    absolute_throughput_difference,
                    relative_throughput_difference,

                    a["ref_sd"],
                    b["ref_sd"],

                    a["ref_cv"],
                    b["ref_cv"],
                ]
            )

    headers = [
        "file_size_bytes",

        "session_a",
        "session_b",

        "L_ref_a_seconds",
        "L_ref_b_seconds",

        "absolute_difference_seconds",
        "relative_difference_to_a",

        "R_ref_a_mbps",
        "R_ref_b_mbps",

        "absolute_R_difference_mbps",
        "relative_R_difference_to_a",

        "reference_sd_a",
        "reference_sd_b",

        "reference_cv_a",
        "reference_cv_b",
    ]

    with open(
        out / "table5_session_comparison.csv",
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        csv.writer(
            f,
            lineterminator="\n",
        ).writerows(
            [headers, *rows]
        )


# ============================================================
# FIGURES
# ============================================================

def make_plots(out, results):

    results = sorted(
        results,
        key=lambda x: x["S"],
    )

    for x in results:

        S = x["S"]

        n = list(
            range(
                1,
                N_ANALYSIS + 1,
            )
        )

        tag = (
            f"{S // 1_000_000}MB"
        )

        plot_specs = [

            (
                f"fig1_time_sequence_{tag}.png",
                f"Download time T_n ({tag})",
                "T_n (s)",
                "T_n",
                x["ts"],
                x["L_ref"],
            ),

            (
                f"fig2_throughput_{tag}.png",
                f"Throughput R_n ({tag})",
                "R_n (Mbps)",
                "R_n",
                x["R"],
                None,
            ),

            (
                f"fig3_running_mean_{tag}.png",
                f"Running mean M_n ({tag})",
                "M_n (s)",
                "M_n",
                x["M"],
                x["L_ref"],
            ),

            (
                f"fig4_running_mean_deviation_{tag}.png",
                f"M_n - reference ({tag})",
                "M_n - L_ref (s)",
                "deviation",
                [
                    m - x["L_ref"]
                    for m in x["M"]
                ],
                0,
            ),

            (
                f"fig5_relative_error_{tag}.png",
                f"Relative error ({tag})",
                "relative error",
                "|T_n-L_ref|/L_ref",
                x["rel"],
                None,
            ),
        ]

        for (
            filename,
            title,
            ylabel,
            label,
            data,
            reference,
        ) in plot_specs:

            fig, ax = plt.subplots(
                figsize=(8, 5)
            )

            ax.plot(
                n,
                data,
                label=label,
            )

            if reference is not None:

                ax.axhline(
                    reference,
                    linestyle="--",
                    label=(
                        "empirical reference"
                        if reference != 0
                        else "zero"
                    ),
                )

            ax.set(
                title=title,
                xlabel="n",
                ylabel=ylabel,
            )

            ax.grid(
                True,
                alpha=0.25,
            )

            ax.legend()

            fig.tight_layout()

            fig.savefig(
                out / filename,
                dpi=150,
            )

            plt.close(fig)


def make_session_comparison_plot(
    out,
    by_session,
):
    """
    Plot empirical reference download time for
    each session across the four file sizes.
    """

    sessions = sorted(
        by_session.keys()
    )

    if len(sessions) < 2:
        return

    lookup = {
        session: {
            x["S"]: x["L_ref"]
            for x in results
        }
        for session, results
        in by_session.items()
    }

    x_positions = list(
        range(len(LOCKED_SIZES))
    )

    fig, ax = plt.subplots(
        figsize=(8, 5)
    )

    for session in sessions:

        values = [
            lookup[session].get(
                S,
                math.nan,
            )
            for S in LOCKED_SIZES
        ]

        ax.plot(
            x_positions,
            values,
            marker="o",
            label=session,
        )

    labels = [
        f"{S // 1_000_000} MB"
        for S in LOCKED_SIZES
    ]

    ax.set(
        title=(
            "Empirical reference download "
            "time by session"
        ),
        xlabel="File size",
        ylabel="Reference download time (s)",
        xticks=x_positions,
        xticklabels=labels,
    )

    ax.grid(
        True,
        alpha=0.25,
    )

    ax.legend()

    fig.tight_layout()

    fig.savefig(
        out / "fig6_session_comparison.png",
        dpi=150,
    )

    plt.close(fig)


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--selftest",
        action="store_true",
    )

    parser.add_argument(
        "--obs",
        nargs="+",
    )

    parser.add_argument(
        "--out",
        default="results",
    )

    args = parser.parse_args()

    # --------------------------------------------------------
    # Self-test
    # --------------------------------------------------------

    if args.selftest:
        selftest()
        return

    if not args.obs:
        parser.error(
            "--obs is required unless --selftest"
        )

    # --------------------------------------------------------
    # Validate all observation files
    # --------------------------------------------------------

    groups, statuses = validate_all(
        args.obs
    )

    out = Path(args.out)

    out.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # Validation report
    # --------------------------------------------------------

    with open(
        out / "validation_report.txt",
        "w",
        encoding="utf-8",
    ) as f:

        for (
            session,
            S,
            n,
            failed,
            status,
            issues,
        ) in statuses:

            f.write(
                f"{session}, {S} bytes: "
                f"{status}; "
                f"valid_observations={n}; "
                f"failed_http_attempts={failed}\n"
            )

            for issue in issues:
                f.write(
                    f"  - {issue}\n"
                )

    # --------------------------------------------------------
    # Analyze valid groups
    # --------------------------------------------------------

    valid = []

    for (
        session,
        S,
        n,
        failed,
        status,
        issues,
    ) in statuses:

        if status in (
            "VALID ANALYZABLE",
            "FLAGGED",
        ):

            rows = groups[
                (session, S)
            ]

            result = analyze_series(
                rows
            )

            valid.append(
                (
                    session,
                    result,
                )
            )

    # --------------------------------------------------------
    # Group by session
    # --------------------------------------------------------

    by_session = {}

    for session, result in valid:

        by_session.setdefault(
            session,
            [],
        ).append(result)

    # Sort every session by locked file size.
    for session in by_session:
        by_session[session].sort(
            key=lambda x: x["S"]
        )

    # --------------------------------------------------------
    # Write per-session tables and figures
    # --------------------------------------------------------

    for session, results in by_session.items():

        session_out = (
            out / session
        )

        session_out.mkdir(
            parents=True,
            exist_ok=True,
        )

        write_outputs(
            session_out,
            results,
        )

        make_plots(
            session_out,
            results,
        )

    # --------------------------------------------------------
    # Write combined session comparison
    # --------------------------------------------------------

    write_session_comparison(
        out,
        by_session,
    )

    make_session_comparison_plot(
        out,
        by_session,
    )

    # --------------------------------------------------------
    # Console summary
    # --------------------------------------------------------

    analyzable = sum(
        status in (
            "VALID ANALYZABLE",
            "FLAGGED",
        )
        for (
            session,
            S,
            n,
            failed,
            status,
            issues,
        )
        in statuses
    )

    print(
        f"VALIDATION: "
        f"{analyzable}/{len(statuses)} "
        f"groups analyzable"
    )

    for (
        session,
        S,
        n,
        failed,
        status,
        issues,
    ) in statuses:

        print(
            f"  {session} {S}: "
            f"{status} "
            f"(n={n}, "
            f"failed_http_attempts={failed})"
        )

    if len(by_session) >= 2:
        print(
            "SESSION COMPARISON: "
            "table5_session_comparison.csv "
            "and fig6_session_comparison.png "
            "written"
        )


if __name__ == "__main__":
    main()