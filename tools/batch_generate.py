"""
batch_generate.py — Batch runner for the two-prompt SEO content pipeline.

Reads page_config.csv, skips already-completed pages, tracks progress in
output/progress.csv. Output saved to output/batch_{timestamp}.csv.
Safe to interrupt and resume at any time.

Usage:
    python tools/batch_generate.py --models openai/gpt-4o-mini
    python tools/batch_generate.py --models openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022 --cluster group_annotate --limit 3
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_page import generate_single_page


def _default_base_dir():
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(tools_dir), "inputs")


def sanitize_model_name(model: str) -> str:
    """Convert a model name to a filesystem-safe column slug."""
    slug = model.replace("/", "_")
    slug = re.sub(r"[^a-zA-Z0-9._-]", "", slug)
    return slug.lower()


def write_batch_csv(rows: list, models: list, output_path: str) -> None:
    """Write batch results to CSV — one row per URL, two columns per model.

    When a fallback fired for a model slot, an extra {slug}_model_used column is
    written so it's clear which model actually produced the output.
    """
    model_slugs = [sanitize_model_name(m) for m in models]

    # Detect if any row recorded a model_used override (i.e. fallback fired)
    has_model_used = any(
        "model_used" in row["model_results"].get(slug, {})
        for row in rows
        for slug in model_slugs
    )

    columns = ["url", "keyword", "url_slug", "cluster"]
    for slug in model_slugs:
        columns += [f"{slug}_blueprint", f"{slug}_content"]
        if has_model_used:
            columns.append(f"{slug}_model_used")

    records = []
    for row in rows:
        record = {
            "url": row["url"],
            "keyword": row["keyword"],
            "url_slug": row["url_slug"],
            "cluster": row["cluster"],
        }
        for slug in model_slugs:
            result_data = row["model_results"].get(slug, {})
            record[f"{slug}_blueprint"] = result_data.get("blueprint", "")
            record[f"{slug}_content"] = result_data.get("content", "")
            if has_model_used:
                record[f"{slug}_model_used"] = result_data.get("model_used", "")
        records.append(record)
    df = pd.DataFrame(records, columns=columns)
    df.to_csv(output_path, index=False, encoding="utf-8")


def _init_progress(page_config_path: str, progress_path: str) -> pd.DataFrame:
    """Create progress.csv from page_config.csv with all pages as pending."""
    from urllib.parse import urlparse

    pages = pd.read_csv(page_config_path)
    url_slugs = [urlparse(u).path.rstrip("/").split("/")[-1] for u in pages["url"]]

    progress = pd.DataFrame(
        {
            "url": pages["url"],
            "keyword": pages["keyword"],
            "url_slug": url_slugs,
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
        for col in ("url_slug", "status", "error", "timestamp"):
            df[col] = df[col].fillna("").astype(str)
        return df
    except Exception:
        print(f"  {os.path.basename(progress_path)} appears corrupt — reinitializing from page_config.csv")
        return _init_progress(page_config_path, progress_path)


def run_batch(
    models: list,
    fallback_models: list = None,
    cluster: str = None,
    limit: int = None,
    base_dir: str = None,
    temperature: float = 0.3,
    timeout: int = None,
    run_label: str = None,
):
    if base_dir is None:
        base_dir = _default_base_dir()

    page_config_path = os.path.join(base_dir, "page_config.csv")

    output_root = os.path.join(os.path.dirname(base_dir), "output")
    os.makedirs(output_root, exist_ok=True)

    progress_path = (
        os.path.join(output_root, f"progress_{run_label}.csv")
        if run_label
        else os.path.join(output_root, "progress.csv")
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_name = f"batch_{run_label}.csv" if run_label else f"batch_{timestamp}.csv"
    csv_path = os.path.join(output_root, csv_name)

    # Initialize or load progress
    if not os.path.exists(progress_path):
        print("No progress.csv found — initializing from page_config.csv")
        progress_df = _init_progress(page_config_path, progress_path)
    else:
        progress_df = _load_progress(progress_path, page_config_path)

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
    model_names = ", ".join(m.split("/")[-1] for m in models)
    print(f"Batch starting: {total} pages to process ({done_count} already done).")
    if cluster:
        print(f"Cluster filter: {cluster}")
    if fallback_models:
        fallback_names = ", ".join(m.split("/")[-1] for m in fallback_models)
        print(f"Models: {model_names} (fallback: {fallback_names})")
    else:
        print(f"Models: {model_names}")
    print(f"Temperature: {temperature}")
    if timeout:
        print(f"Timeout: {timeout}s per model")
    print(f"CSV: {csv_path}")
    if run_label:
        print(f"Run label: {run_label}")
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
    rows = []  # accumulated result rows for CSV

    for i, row in enumerate(pending.itertuples(), 1):
        print(f"[{i}/{total}] {row.keyword}")

        row_cluster = url_to_cluster.get(row.url)
        if not row_cluster:
            page_row = {
                "url": row.url,
                "keyword": row.keyword,
                "url_slug": row.url_slug or "",
                "cluster": "",
                "model_results": {
                    sanitize_model_name(m): {
                        "blueprint": "ERROR: cluster not found",
                        "content": "ERROR: cluster not found",
                    }
                    for m in models
                },
            }
            rows.append(page_row)
            write_batch_csv(rows, models, csv_path)

            idx = progress_df[progress_df["url"] == row.url].index[0]
            progress_df.at[idx, "status"] = "error"
            progress_df.at[idx, "error"] = "No cluster found for URL"
            progress_df.at[idx, "timestamp"] = datetime.now().isoformat(timespec="seconds")
            progress_df.to_csv(progress_path, index=False)
            errors += 1
            print(f"  ✗ Error: No cluster found for URL")
            continue

        if row_cluster not in cluster_caches:
            cache = load_config(cluster=row_cluster, keyword="__cache__", url=_dummy_url, base_dir=base_dir)
            cache["PROMPT_1_TEMPLATE"] = prompt1_template
            cache["PROMPT_2_TEMPLATE"] = prompt2_template
            cluster_caches[row_cluster] = cache
        config_cache = cluster_caches[row_cluster]

        page_row = {
            "url": row.url,
            "keyword": row.keyword,
            "url_slug": row.url_slug or "",
            "cluster": row_cluster,
            "model_results": {},
        }
        rows.append(page_row)

        page_ok = True
        last_result = None

        for model in models:
            model_slug = sanitize_model_name(model)
            model_short = model.split("/")[-1]

            last_result = generate_single_page(
                keyword=row.keyword,
                url=row.url,
                cluster=row_cluster,
                base_dir=base_dir,
                model=model,
                fallback_models=fallback_models,
                temperature=temperature,
                timeout=timeout,
                config_cache=config_cache,
                return_raw=True,
            )

            if last_result["status"] == "done":
                used_model = last_result.get("model_used", model)
                result_entry = {
                    "blueprint": last_result["blueprint"],
                    "content": last_result["content"],
                }
                if fallback_models:
                    result_entry["model_used"] = used_model
                page_row["model_results"][model_slug] = result_entry
                word_count = len(last_result["content"].split())
                if used_model != model:
                    print(f"  ✓ {model_short} → ~{word_count} words (fallback: {used_model.split('/')[-1]})")
                else:
                    print(f"  ✓ {model_short} → ~{word_count} words")
            else:
                error_msg = last_result.get("error") or "unknown error"
                blueprint_val = last_result.get("blueprint") or f"ERROR: {error_msg}"
                result_entry = {
                    "blueprint": blueprint_val,
                    "content": f"ERROR: {error_msg}",
                }
                if fallback_models:
                    result_entry["model_used"] = ""
                page_row["model_results"][model_slug] = result_entry
                page_ok = False
                print(f"  ✗ {model_short}: {error_msg}")

            # Write CSV after every model result (safe to interrupt mid-page)
            write_batch_csv(rows, models, csv_path)

        # Update progress
        idx = progress_df[progress_df["url"] == row.url].index[0]
        progress_df.at[idx, "status"] = "done" if page_ok else "error"
        progress_df.at[idx, "url_slug"] = page_row["url_slug"]
        progress_df.at[idx, "error"] = "" if page_ok else "one or more models failed — see CSV"
        progress_df.at[idx, "timestamp"] = datetime.now().isoformat(timespec="seconds")
        progress_df.to_csv(progress_path, index=False)

        if page_ok:
            completed += 1
        else:
            errors += 1

        # Rate limit delay between pages (skip after last page)
        if i < total:
            error_msg = (last_result.get("error") or "") if last_result else ""
            delay = 3 if "rate limit" in error_msg.lower() else 0.5
            time.sleep(delay)

    print()
    print(f"Batch complete.")
    print(f"  {completed} done, {errors} errors")
    print(f"  CSV: {csv_path}")
    if errors > 0:
        print(f"  Error details in: {progress_path}")
        print(f"  Re-run to retry failed pages.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch generate SEO content pages.")
    parser.add_argument(
        "--models",
        default="openai/gpt-4o-mini",
        help="Comma-separated model names (provider-prefixed, e.g. openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022)",
    )
    parser.add_argument(
        "--fallback-models",
        default=None,
        help="Comma-separated fallback models tried in order if the primary fails (e.g. openai/gpt-4o-mini). Cannot be combined with multi-model comparison.",
    )
    parser.add_argument("--cluster", default=None, help="Filter by cluster name (optional — omit to process all clusters)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max pages to process in this run (for testing)",
    )
    parser.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature")
    parser.add_argument("--timeout", type=int, default=None, help="Timeout in seconds per model (default: none)")
    parser.add_argument(
        "--run-label",
        default=None,
        help="Label for this run (creates separate progress file and fixed CSV name for resume)",
    )
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("Error: --models must specify at least one model.")
        sys.exit(1)

    fallback_models = None
    if args.fallback_models:
        fallback_models = [m.strip() for m in args.fallback_models.split(",") if m.strip()]
        if len(models) > 1:
            print("Error: --fallback-models cannot be combined with multi-model comparison (--models A,B).")
            print("  Use a single primary model with --models, then list fallbacks with --fallback-models.")
            sys.exit(1)

    run_batch(
        models=models,
        fallback_models=fallback_models,
        cluster=args.cluster,
        limit=args.limit,
        temperature=args.temperature,
        timeout=args.timeout,
        run_label=args.run_label,
    )
