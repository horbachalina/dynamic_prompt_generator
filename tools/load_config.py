"""
load_config.py — Parse all three config CSVs and return template variables.

Usage (CLI test mode):
    python tools/load_config.py --cluster group_annotate --test
    python tools/load_config.py --cluster group_annotate --locale fr --test
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


def load_config(cluster: str, keyword: str, url: str, base_dir: str = None, locale: str = "en") -> dict:
    """
    Load all config CSVs and return a dict of ready-to-use template variables.

    Args:
        locale: Language code (e.g. "en", "fr", "de"). Determines language and tone_of_voice
                injected into prompts. Defaults to "en".

    Returns:
        {
            "GLOBAL_CONFIG": str,    # JSON: website, language, target_audience, positioning_statement
            "CLUSTER_CONFIG": str,   # JSON: cluster_context, target_word_count
            "SECTION_MENU": str,     # raw long-form text block (from cluster_config.csv)
            "PAGE_CONFIG": str,      # JSON: {"h1": "...", "url": "..."}
            "LOCALE_CONFIG": str,    # JSON: {"language": "...", "tone_of_voice": "..."}
            "url_slug": str          # last path segment of URL, or subdomain if path is empty
        }
    """
    if base_dir is None:
        base_dir = _default_base_dir()

    global_csv = os.path.join(base_dir, "global_config.csv")
    cluster_csv = os.path.join(base_dir, "cluster_config.csv")
    locale_csv = os.path.join(base_dir, "locale_config.csv")

    # Read CSVs — pandas handles complex multi-line quoted fields correctly
    global_df = pd.read_csv(global_csv)
    cluster_df = pd.read_csv(cluster_csv)
    locale_df = pd.read_csv(locale_csv)

    # --- LOCALE lookup ---
    locale_matches = locale_df[locale_df["locale"] == locale]
    if locale_matches.empty:
        available = locale_df["locale"].tolist()
        raise ValueError(
            f"Locale '{locale}' not found in locale_config.csv. "
            f"Available locales: {available}"
        )
    locale_row = locale_matches.iloc[0]
    language = str(locale_row["language"])
    tone_of_voice = "" if pd.isna(locale_row["tone_of_voice"]) else str(locale_row["tone_of_voice"])
    locale_config = json.dumps(
        {"language": language, "tone_of_voice": tone_of_voice},
        ensure_ascii=False,
    )

    # --- GLOBAL_CONFIG (full — for prompt_1) ---
    row = global_df.iloc[0]
    global_config = json.dumps(
        {
            "website": row["website"],
            "language": language,
            "target_audience": row["target_audience"],
            "positioning_statement": row["positioning_statement"],
        },
        ensure_ascii=False,
    )

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
            "cluster_context": cluster_row["cluster_context"],
            "target_word_count": cluster_row["target_word_count"],
        },
        ensure_ascii=False,
    )

    # --- SECTION_MENU ---
    section_menu = str(cluster_row["section_menu"])

    # --- PAGE_CONFIG ---
    page_config = json.dumps({"h1": keyword, "url": url}, ensure_ascii=False)

    # --- URL slug ---
    parsed = urlparse(url)
    url_slug = parsed.path.rstrip("/").split("/")[-1]
    if not url_slug:
        # Fallback: use the first subdomain label (e.g. "compress-pdf" from compress-pdf.pdffiller.com)
        hostname = parsed.hostname or ""
        url_slug = hostname.split(".")[0] if hostname else ""
    if not url_slug:
        raise ValueError(f"Could not derive a url_slug from URL: {url!r}")

    return {
        "GLOBAL_CONFIG": global_config,
        "CLUSTER_CONFIG": cluster_config,
        "SECTION_MENU": section_menu,
        "PAGE_CONFIG": page_config,
        "LOCALE_CONFIG": locale_config,
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
    parser.add_argument("--locale", default="en", help="Locale code (e.g. en, fr, de)")
    parser.add_argument(
        "--test", action="store_true", help="Print all resolved variables"
    )
    args = parser.parse_args()

    config = load_config(args.cluster, args.keyword, args.url, locale=args.locale)

    if args.test:
        print("=" * 60)
        print("GLOBAL_CONFIG:")
        print(config["GLOBAL_CONFIG"])
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
        print("LOCALE_CONFIG (first 200 chars):")
        print(config["LOCALE_CONFIG"][:200] if config["LOCALE_CONFIG"] else "(empty)")
        print()
        print("url_slug:", config["url_slug"])
        print("=" * 60)
        print("All variables loaded successfully.")
    else:
        print(json.dumps({k: v[:100] + "..." if len(v) > 100 else v for k, v in config.items()}))
