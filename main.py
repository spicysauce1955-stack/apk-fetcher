#!/usr/bin/env python3
"""
APK Scraper CLI - Download specific APK versions from multiple sources.

Usage:
    python main.py                          # Fetch all apps/versions from config
    python main.py --app telegram           # Fetch specific app
    python main.py --app telegram --version 12.4.0
    python main.py --source apkmirror       # Use specific source only
"""

import argparse
import sys
import logging

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


def fetch_apk(app_config, version: str, output_dir: str, headless: bool, 
              source_filter: str = None) -> bool:
    """
    Fetch a single APK version with fallback through sources.
    
    Returns True if download succeeded, False otherwise.
    """
    logger = logging.getLogger("fetcher")
    
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
            logger.warning(f"Version not found on {source_name}: {e}")
            continue
        except DownloadError as e:
            logger.warning(f"Download failed on {source_name}: {e}")
            continue
        except ScraperError as e:
            logger.error(f"Scraper error on {source_name}: {e}")
            continue
        except Exception as e:
            logger.error(f"Unexpected error on {source_name}: {e}")
            continue
    
    logger.error(f"❌ Failed to download {app_config.name} v{version} from any source")
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Download specific APK versions from multiple sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--config", "-c", default="config.yaml",
                        help="Path to config file (default: config.yaml)")
    parser.add_argument("--app", "-a", 
                        help="Specific app to fetch (default: all apps in config)")
    parser.add_argument("--version", "-v",
                        help="Specific version to fetch (default: all versions in config)")
    parser.add_argument("--source", "-s", choices=list(SCRAPERS.keys()),
                        help="Use only this source (default: try all with fallback)")
    parser.add_argument("--output", "-o",
                        help="Output directory (default: from config)")
    parser.add_argument("--no-headless", action="store_true",
                        help="Show browser window (useful for debugging)")
    parser.add_argument("--debug", "-d", action="store_true",
                        help="Enable debug logging")
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    logger = logging.getLogger("main")
    
    # Load config
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        logger.error(f"Config error: {e}")
        sys.exit(1)
    
    output_dir = args.output or config.output_dir
    headless = not args.no_headless and config.headless
    
    # Determine what to fetch
    if args.app:
        app_config = config.get_app(args.app)
        if not app_config:
            logger.error(f"App '{args.app}' not found in config")
            sys.exit(1)
        apps_to_fetch = {args.app: app_config}
    else:
        apps_to_fetch = config.apps
    
    # Track results
    success_count = 0
    fail_count = 0
    
    # Fetch each app/version
    for app_name, app_config in apps_to_fetch.items():
        versions = [args.version] if args.version else app_config.versions
        
        for version in versions:
            logger.info(f"\n{'='*60}")
            logger.info(f"Fetching: {app_name} v{version}")
            logger.info(f"{'='*60}")
            
            if fetch_apk(app_config, version, output_dir, headless, args.source):
                success_count += 1
            else:
                fail_count += 1
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"Complete: {success_count} succeeded, {fail_count} failed")
    logger.info(f"{'='*60}")
    
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
