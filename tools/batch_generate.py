"""
batch_generate.py — Batch runner for the two-prompt SEO content pipeline.

Reads page_config.csv, skips already-completed pages, tracks progress in
inputs/progress.csv. Outputs saved to output/. Safe to interrupt and resume at any time.

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
    return os.path.join(os.path.dirname(tools_dir), "inputs")


def _init_progress(page_config_path: str, progress_path: str, output_dir: str = None) -> pd.DataFrame:
    """Create progress.csv from page_config.csv, marking already-completed pages as 'done'."""
    from urllib.parse import urlparse

    pages = pd.read_csv(page_config_path)

    statuses = []
    url_slugs = []
    for url in pages["url"]:
        slug = urlparse(url).path.rstrip("/").split("/")[-1]
        if output_dir and slug:
            content_path = os.path.join(output_dir, slug, "content.html")
            if os.path.exists(content_path):
                statuses.append("done")
                url_slugs.append(slug)
                continue
        statuses.append("pending")
        url_slugs.append("")

    progress = pd.DataFrame(
        {
            "url": pages["url"],
            "keyword": pages["keyword"],
            "url_slug": url_slugs,
            "status": statuses,
            "error": "",
            "timestamp": "",
        }
    )
    progress.to_csv(progress_path, index=False)
    return progress


def _load_progress(progress_path: str, page_config_path: str, output_dir: str = None) -> pd.DataFrame:
    """Load existing progress.csv, falling back to re-init if corrupt."""
    try:
        df = pd.read_csv(progress_path)
        required = {"url", "keyword", "url_slug", "status", "error", "timestamp"}
        if not required.issubset(df.columns):
            raise ValueError("Missing columns")
        for col in ("url_slug", "status", "error", "timestamp"):
            df[col] = df[col].fillna("").astype(str)
        return df
    except Exception:
        print(f"  {os.path.basename(progress_path)} appears corrupt — reinitializing from page_config.csv")
        return _init_progress(page_config_path, progress_path, output_dir=output_dir)


def run_batch(
    cluster: str = None,
    limit: int = None,
    base_dir: str = None,
    model: str = "openai/gpt-4o-mini",
    temperature: float = 0.3,
    run_label: str = None,
):
    if base_dir is None:
        base_dir = _default_base_dir()

    page_config_path = os.path.join(base_dir, "page_config.csv")
    if run_label:
        progress_path = os.path.join(base_dir, f"progress_{run_label}.csv")
        output_dir = os.path.join(os.path.dirname(base_dir), "output", run_label)
    else:
        progress_path = os.path.join(base_dir, "progress.csv")
        output_dir = os.path.join(os.path.dirname(base_dir), "output")

    # Initialize or load progress
    if not os.path.exists(progress_path):
        print("No progress.csv found — initializing from page_config.csv")
        progress_df = _init_progress(page_config_path, progress_path, output_dir=output_dir)
    else:
        progress_df = _load_progress(progress_path, page_config_path, output_dir=output_dir)

    # Build url → cluster mapping from page_config.csv
    page_df = pd.read_csv(page_config_path)
    url_to_cluster = dict(zip(page_df["url"], page_df["cluster"]))

    # Select pending rows, optionally filtered by cluster
    pending = progress_df[progress_df["status"] != "done"].copy()
    if cluster is not None:
        pending = pending[pending["url"].map(url_to_cluster) == cluster].copy()
    if limit is not None:
        pending = pending.head(limit)

    total = len(pending)
    if total == 0:
        print("Nothing to do — all pages are already done.")
        return

    done_count = (progress_df["status"] == "done").sum()
    print(f"Batch starting: {total} pages to process ({done_count} already done).")
    if cluster:
        print(f"Cluster filter: {cluster}")
    print(f"Model: {model} | Temperature: {temperature}")
    if run_label:
        print(f"Run label: {run_label} → {output_dir}")
    print()

    completed = 0
    errors = 0

    # Load prompt templates and per-cluster config caches.
    # Each cluster's config is loaded once on first encounter.
    from load_config import load_config
    _dummy_url = "https://example.com/placeholder"
    with open(os.path.join(base_dir, "prompt_1.md"), "r", encoding="utf-8") as f:
        prompt1_template = f.read()
    with open(os.path.join(base_dir, "prompt_2.md"), "r", encoding="utf-8") as f:
        prompt2_template = f.read()

    cluster_caches: dict = {}

    for i, row in enumerate(pending.itertuples(), 1):
        print(f"[{i}/{total}] {row.keyword}")

        row_cluster = url_to_cluster.get(row.url)
        if not row_cluster:
            result = {
                "status": "error",
                "url_slug": None,
                "error": f"No cluster found for URL: {row.url}",
            }
        else:
            if row_cluster not in cluster_caches:
                cache = load_config(cluster=row_cluster, keyword="__cache__", url=_dummy_url, base_dir=base_dir)
                cache["PROMPT_1_TEMPLATE"] = prompt1_template
                cache["PROMPT_2_TEMPLATE"] = prompt2_template
                cluster_caches[row_cluster] = cache
            config_cache = cluster_caches[row_cluster]

            result = generate_single_page(
                keyword=row.keyword,
                url=row.url,
                cluster=row_cluster,
                base_dir=base_dir,
                output_dir=output_dir,
                model=model,
                temperature=temperature,
                config_cache=config_cache,
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

        # Rate limit delay between pages (skip after last page).
        # Use a short delay normally; fall back to 3s if the last call hit a rate limit.
        if i < total:
            error_msg = result.get("error") or ""
            delay = 3 if "rate limit" in error_msg.lower() else 0.5
            time.sleep(delay)

    print()
    print(f"Batch complete.")
    print(f"  {completed} done, {errors} errors")
    if errors > 0:
        print(f"  Error details in: {progress_path}")
        print(f"  Re-run to retry failed pages.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch generate SEO content pages.")
    parser.add_argument("--cluster", default=None, help="Filter by cluster name (optional — omit to process all clusters)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max pages to process in this run (for testing)",
    )
    parser.add_argument("--model", default="openai/gpt-4o-mini", help="Model name (provider-prefixed, e.g. openai/gpt-4o-mini)")
    parser.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature")
    parser.add_argument(
        "--run-label",
        default=None,
        help="Label for this run (creates separate output dir and progress file)",
    )
    args = parser.parse_args()

    run_batch(
        cluster=args.cluster,
        limit=args.limit,
        model=args.model,
        temperature=args.temperature,
        run_label=args.run_label,
    )
