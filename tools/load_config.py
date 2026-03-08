"""
load_config.py — Parse all three config CSVs and return template variables.

Usage (CLI test mode):
    python tools/load_config.py --cluster group_annotate --test
"""

import json
import os
import sys
import argparse
from urllib.parse import urlparse

import pandas as pd


def _default_base_dir():
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(tools_dir), "inputs")


def load_config(cluster: str, keyword: str, url: str, base_dir: str = None) -> dict:
    """
    Load all config CSVs and return a dict of ready-to-use template variables.

    Returns:
        {
            "GLOBAL_CONFIG": str,    # JSON: website, language, target_audience, positioning_statement
            "WRITER_CONFIG": str,    # JSON: website, language, target_audience (slim — for prompt_2)
            "CONTENT_RULES": str,    # raw text from content_rules.csv
            "CLUSTER_CONFIG": str,   # JSON: page_type, cluster_context, target_word_count
            "SECTION_MENU": str,     # raw long-form text block (from cluster_config.csv)
            "PAGE_CONFIG": str,      # JSON: {"keyword": "..."}
            "url_slug": str          # last path segment of URL
        }
    """
    if base_dir is None:
        base_dir = _default_base_dir()

    global_csv = os.path.join(base_dir, "global_config.csv")
    cluster_csv = os.path.join(base_dir, "cluster_config.csv")
    content_rules_csv = os.path.join(base_dir, "content_rules.csv")

    # Read CSVs — pandas handles complex multi-line quoted fields correctly
    global_df = pd.read_csv(global_csv)
    cluster_df = pd.read_csv(cluster_csv)
    content_rules_df = pd.read_csv(content_rules_csv)

    # --- GLOBAL_CONFIG (full — for prompt_1) ---
    row = global_df.iloc[0]
    global_config = json.dumps(
        {
            "website": row["website"],
            "language": row["language"],
            "target_audience": row["target_audience"],
            "positioning_statement": row["positioning_statement"],
        },
        ensure_ascii=False,
    )

    # --- WRITER_CONFIG (slim — for prompt_2, omits positioning_statement) ---
    writer_config = json.dumps(
        {
            "website": row["website"],
            "language": row["language"],
            "target_audience": row["target_audience"],
        },
        ensure_ascii=False,
    )

    # --- CONTENT_RULES ---
    content_rules = str(content_rules_df.iloc[0]["shared_rules"])

    # --- CLUSTER_CONFIG ---
    matches = cluster_df[cluster_df["cluster"] == cluster]
    if matches.empty:
        available = cluster_df["cluster"].tolist()
        raise ValueError(
            f"Cluster '{cluster}' not found in cluster_config.csv. "
            f"Available clusters: {available}"
        )
    cluster_row = matches.iloc[0]
    cluster_config = json.dumps(
        {
            "page_type": cluster_row["page_type"],
            "cluster_context": cluster_row["cluster_context"],
            "target_word_count": cluster_row["target_word_count"],
        },
        ensure_ascii=False,
    )

    # --- SECTION_MENU ---
    section_menu = str(cluster_row["section_menu"])

    # --- PAGE_CONFIG ---
    page_config = json.dumps({"keyword": keyword}, ensure_ascii=False)

    # --- URL slug ---
    url_slug = urlparse(url).path.rstrip("/").split("/")[-1]
    if not url_slug:
        raise ValueError(f"Could not derive a url_slug from URL: {url!r}")

    return {
        "GLOBAL_CONFIG": global_config,
        "WRITER_CONFIG": writer_config,
        "CONTENT_RULES": content_rules,
        "CLUSTER_CONFIG": cluster_config,
        "SECTION_MENU": section_menu,
        "PAGE_CONFIG": page_config,
        "url_slug": url_slug,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load and validate config variables.")
    parser.add_argument("--cluster", default="group_annotate", help="Cluster name")
    parser.add_argument("--keyword", default="Test Keyword", help="Target keyword")
    parser.add_argument(
        "--url",
        default="https://www.pdffiller.com/en/functionality/test-keyword",
        help="Page URL",
    )
    parser.add_argument(
        "--test", action="store_true", help="Print all resolved variables"
    )
    args = parser.parse_args()

    config = load_config(args.cluster, args.keyword, args.url)

    if args.test:
        print("=" * 60)
        print("GLOBAL_CONFIG:")
        print(config["GLOBAL_CONFIG"])
        print()
        print("WRITER_CONFIG:")
        print(config["WRITER_CONFIG"])
        print()
        print("CONTENT_RULES (first 300 chars):")
        print(config["CONTENT_RULES"][:300])
        print()
        print("CLUSTER_CONFIG:")
        print(config["CLUSTER_CONFIG"])
        print()
        print("SECTION_MENU (first 300 chars):")
        print(config["SECTION_MENU"][:300])
        print()
        print("PAGE_CONFIG:")
        print(config["PAGE_CONFIG"])
        print()
        print("url_slug:", config["url_slug"])
        print("=" * 60)
        print("All variables loaded successfully.")
    else:
        print(json.dumps({k: v[:100] + "..." if len(v) > 100 else v for k, v in config.items()}))
