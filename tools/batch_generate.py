"""
batch_generate.py — Batch runner for the two-prompt SEO content pipeline.

Reads page_config.csv, skips already-completed pages, tracks progress in
.tmp/progress.csv. Safe to interrupt and resume at any time.

Usage:
    python tools/batch_generate.py
    python tools/batch_generate.py --cluster group_annotate --limit 3
"""

import argparse
import os
import sys
import time
from datetime import datetime

import pandas as pd

# Allow importing generate_page from the same tools/ directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_page import generate_single_page


def _default_base_dir():
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(tools_dir), ".tmp")


def _init_progress(page_config_path: str, progress_path: str) -> pd.DataFrame:
    """Create progress.csv from page_config.csv with all rows as 'pending'."""
    pages = pd.read_csv(page_config_path)
    progress = pd.DataFrame(
        {
            "url": pages["url"],
            "keyword": pages["keyword"],
            "url_slug": "",
            "status": "pending",
            "error": "",
            "timestamp": "",
        }
    )
    progress.to_csv(progress_path, index=False)
    return progress


def _load_progress(progress_path: str, page_config_path: str) -> pd.DataFrame:
    """Load existing progress.csv, falling back to re-init if corrupt."""
    try:
        df = pd.read_csv(progress_path)
        required = {"url", "keyword", "url_slug", "status", "error", "timestamp"}
        if not required.issubset(df.columns):
            raise ValueError("Missing columns")
        return df
    except Exception:
        print("  progress.csv appears corrupt — reinitializing from page_config.csv")
        return _init_progress(page_config_path, progress_path)


def run_batch(cluster: str, limit: int = None, base_dir: str = None):
    if base_dir is None:
        base_dir = _default_base_dir()

    page_config_path = os.path.join(base_dir, "page_config.csv")
    progress_path = os.path.join(base_dir, "progress.csv")

    # Initialize or load progress
    if not os.path.exists(progress_path):
        print("No progress.csv found — initializing from page_config.csv")
        progress_df = _init_progress(page_config_path, progress_path)
    else:
        progress_df = _load_progress(progress_path, page_config_path)

    # Select pending rows
    pending = progress_df[progress_df["status"] != "done"].copy()
    if limit is not None:
        pending = pending.head(limit)

    total = len(pending)
    if total == 0:
        print("Nothing to do — all pages are already done.")
        return

    done_count = (progress_df["status"] == "done").sum()
    print(f"Batch starting: {total} pages to process ({done_count} already done).")
    print(f"Cluster: {cluster}")
    print()

    completed = 0
    errors = 0

    for i, row in enumerate(pending.itertuples(), 1):
        print(f"[{i}/{total}] {row.keyword}")

        result = generate_single_page(
            keyword=row.keyword,
            url=row.url,
            cluster=cluster,
            base_dir=base_dir,
        )

        # Update progress_df in memory
        idx = progress_df[progress_df["url"] == row.url].index[0]
        progress_df.at[idx, "status"] = result["status"]
        progress_df.at[idx, "url_slug"] = result.get("url_slug") or ""
        progress_df.at[idx, "error"] = result.get("error") or ""
        progress_df.at[idx, "timestamp"] = datetime.now().isoformat(timespec="seconds")

        # Write to disk immediately after every page
        progress_df.to_csv(progress_path, index=False)

        if result["status"] == "done":
            completed += 1
            print(f"  ✓ Done → {result['content_path']}")
        else:
            errors += 1
            print(f"  ✗ Error: {result['error']}")

        # Rate limit delay between pages (skip after last page)
        if i < total:
            time.sleep(3)

    print()
    print(f"Batch complete.")
    print(f"  {completed} done, {errors} errors")
    if errors > 0:
        print(f"  Error details in: {progress_path}")
        print(f"  Re-run to retry failed pages.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch generate SEO content pages.")
    parser.add_argument("--cluster", default="group_annotate", help="Cluster name")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max pages to process in this run (for testing)",
    )
    args = parser.parse_args()

    run_batch(cluster=args.cluster, limit=args.limit)
