#!/usr/bin/env python3
"""
APK Scraper CLI - Download specific APK versions from multiple sources.

Usage:
    python main.py                          # Fetch all apps/versions from config
    python main.py --app telegram           # Fetch specific app
    python main.py --version 12.4.0         # Fetch specific version (across apps)
    python main.py --bulk versions.json     # Bulk fetch from a JSON file
    python main.py --source apkmirror       # Use specific source only
"""

import argparse
import sys
import logging
import json
import os
from typing import List, Dict, Optional

from src import (
    load_config,
    setup_logging,
    APKMirrorScraper,
    UptodownScraper,
    APKPureScraper,
    ScraperError,
    VersionNotFoundError,
    DownloadError,
)

# Source priority and registry
SOURCE_PRIORITY = ["apkmirror", "uptodown", "apkpure"]

SCRAPERS = {
    "apkmirror": APKMirrorScraper,
    "uptodown": UptodownScraper,
    "apkpure": APKPureScraper,
}


def is_already_downloaded(output_dir: str, app_name: str, version: str) -> Optional[str]:
    """Check if any variant of this version is already downloaded."""
    app_dir = os.path.join(output_dir, app_name)
    if not os.path.isdir(app_dir):
        return None
        
    pattern = f"{app_name}-{version}-"
    for filename in os.listdir(app_dir):
        if filename.startswith(pattern) and filename.endswith(".apk"):
            return os.path.join(app_dir, filename)
    return None


def fetch_apk(app_config, version: str, output_dir: str, headless: bool, 
              source_filter: str = None, force: bool = False) -> bool:
    """
    Fetch a single APK version with fallback through sources.
    
    Returns True if download succeeded, False otherwise.
    """
    logger = logging.getLogger("fetcher")
    
    # Check if already exists
    if not force:
        existing_path = is_already_downloaded(output_dir, app_config.name, version)
        if existing_path:
            logger.info(f"⏭️  Skipping {app_config.name} v{version} - Already exists: {existing_path}")
            return True # Consider existing as a 'success' in terms of workflow
            
    # Determine which sources to try
    sources_to_try = [source_filter] if source_filter else SOURCE_PRIORITY
    
    for source_name in sources_to_try:
        source_config = app_config.get_source_config(source_name)
        
        if not source_config:
            logger.debug(f"No config for {source_name}, skipping")
            continue
        
        scraper_class = SCRAPERS.get(source_name)
        if not scraper_class:
            logger.warning(f"Unknown source: {source_name}")
            continue
        
        logger.info(f"Trying {source_name} for {app_config.name} v{version}...")
        
        try:
            scraper = scraper_class(output_dir=output_dir, headless=headless)
            result = scraper.scrape(source_config, version)
            
            if result:
                logger.info(f"✅ Success! Downloaded: {result.filepath}")
                logger.info(f"   Source: {result.source} | Size: {result.size_bytes:,} bytes")
                return True
                
        except VersionNotFoundError as e:
            logger.warning(f"⚠️  Version not found on {source_name}: {e}")
            continue
        except DownloadError as e:
            logger.warning(f"❌ Download failed on {source_name}: {e}")
            continue
        except ScraperError as e:
            logger.error(f"🚨 Scraper error on {source_name}: {e}")
            continue
        except Exception as e:
            logger.error(f"🔥 Unexpected error on {source_name}: {e}")
            continue
    
    logger.error(f"❌ Failed to download {app_config.name} v{version} from any source")
    return False


def process_bulk(versions_file: str, apps_to_fetch: Dict, output_dir: str, headless: bool, source_filter: str, force: bool = False) -> bool:
    """Process a bulk download from a JSON file."""
    logger = logging.getLogger("bulk")
    
    try:
        with open(versions_file, "r") as f:
            versions_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load versions file '{versions_file}': {e}")
        return False

    success_count = 0
    fail_count = 0
    skip_count = 0
    
    # Normally bulk is for a specific app (e.g. telegram)
    for app_name, app_config in apps_to_fetch.items():
        logger.info(f"\n--- Starting bulk fetch for {app_name} ({len(versions_data)} versions) ---")
        
        for item in versions_data:
            version = item.get("version")
            if not version:
                continue
                
            if not force:
                existing_path = is_already_downloaded(output_dir, app_name, version)
                if existing_path:
                    logger.info(f"⏭️  v{version} already downloaded. Skipping.")
                    skip_count += 1
                    continue

            logger.info(f"\n{'='*60}")
            logger.info(f"Target: {app_name} v{version}")
            logger.info(f"{'='*60}")
            
            if fetch_apk(app_config, version, output_dir, headless, source_filter, force):
                success_count += 1
            else:
                fail_count += 1
                
    logger.info(f"\n{'='*60}")
    logger.info(f"Bulk Fetch Summary: {success_count} succeeded, {fail_count} failed, {skip_count} skipped")
    logger.info(f"{'='*60}")
    return fail_count == 0


def main():
    parser = argparse.ArgumentParser(
        description="Professional APK Scraper - Download specific versions from multiple sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    # Core selectors
    group = parser.add_argument_group("Selection Options")
    group.add_argument("--app", "-a", 
                        help="Specific app to fetch (default: all apps in config)")
    group.add_argument("--version", "-v",
                        help="Specific version to fetch (if omitted, uses versions from config)")
    group.add_argument("--bulk", "-b",
                        help="Path to versions.json for bulk retrieval")
    
    # Execution options
    exec_group = parser.add_argument_group("Execution Options")
    exec_group.add_argument("--config", "-c", default="config.yaml",
                            help="Path to config file (default: config.yaml)")
    exec_group.add_argument("--source", "-s", choices=list(SCRAPERS.keys()),
                            help="Use only this source (default: try all with fallback)")
    exec_group.add_argument("--output", "-o",
                            help="Output directory (default: from config)")
    exec_group.add_argument("--force", "-f", action="store_true",
                            help="Overwrite existing downloads")
    exec_group.add_argument("--no-headless", action="store_true",
                            help="Show browser window (useful for debugging)")
    exec_group.add_argument("--debug", "-d", action="store_true",
                            help="Enable debug logging")
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    logger = logging.getLogger("main")
    
    # Load config
    try:
        config = load_config(args.config)
    except Exception as e:
        logger.error(f"Config error: {e}")
        sys.exit(1)
    
    output_dir = args.output or config.output_dir
    headless = not args.no_headless and config.headless
    
    # Determine apps to process
    if args.app:
        app_config = config.get_app(args.app)
        if not app_config:
            logger.error(f"App '{args.app}' not found in configuration")
            sys.exit(1)
        apps_to_fetch = {args.app: app_config}
    else:
        apps_to_fetch = config.apps

    # Handle Bulk Mode
    if args.bulk:
        success = process_bulk(args.bulk, apps_to_fetch, output_dir, headless, args.source, args.force)
        sys.exit(0 if success else 1)
        
    # Handle Standard Mode
    success_count = 0
    fail_count = 0
    skip_count = 0
    
    for app_name, app_config in apps_to_fetch.items():
        # Version priority: 1. CLI Arg, 2. Config list, 3. Raise Error
        versions = [args.version] if args.version else app_config.versions
        
        if not versions:
            logger.warning(f"No versions specified for {app_name}, skipping")
            continue
            
        for version in versions:
            if not args.force:
                existing_path = is_already_downloaded(output_dir, app_name, version)
                if existing_path:
                    logger.info(f"⏭️  {app_name} v{version} already downloaded. Skipping.")
                    skip_count += 1
                    continue

            logger.info(f"\n{'='*60}")
            logger.info(f"Task: {app_name} v{version}")
            logger.info(f"{'='*60}")
            
            if fetch_apk(app_config, version, output_dir, headless, args.source, args.force):
                success_count += 1
            else:
                fail_count += 1
    
    # Final Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"Execution Complete: {success_count} downloaded, {fail_count} failed, {skip_count} skipped")
    logger.info(f"{'='*60}")
    
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping...")
        sys.exit(130)
