#!/usr/bin/env python3
"""
APK Scraper CLI - Download specific APK versions from multiple sources.

Usage:
    python main.py                          # Fetch all apps/versions from config
    python main.py --app telegram           # Fetch specific app
    python main.py --version 12.4.0         # Fetch specific version (across apps)
    python main.py --bulk versions.json     # Bulk fetch from a JSON file
    python main.py --source apkmirror       # Use specific source only
    python main.py --version 12.4.0 --all-sources    # Every variant from every source
    python main.py --version 12.4.0 --platform x86   # Only packages that support x86
    python main.py --check 12.4.0           # Report whether anything newer is listed
"""

import argparse
import sys
import logging
import json
from collections import Counter
from enum import Enum
from typing import List, Dict

from src import (
    load_config,
    setup_logging,
    APKMirrorScraper,
    UptodownScraper,
    APKPureScraper,
    ScraperError,
    VersionNotFoundError,
    DownloadError,
    SourceBlockedError,
    PlatformMismatchError,
    PLATFORM_ABIS,
    detect_architectures,
    is_version_number,
    version_key,
)

# Source priority and registry
SOURCE_PRIORITY = ["apkmirror", "uptodown", "apkpure"]

SCRAPERS = {
    "apkmirror": APKMirrorScraper,
    "uptodown": UptodownScraper,
    "apkpure": APKPureScraper,
}

# Distinct from 1 (could not check) and 2 (argparse's bad usage).
EXIT_NEWER_VERSION = 3


class FetchStatus(Enum):
    """How much of what was requested for one version was fetched."""
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


def _format_abis(abis) -> str:
    """Render an ABI list for logging."""
    return ", ".join(abis) if abis else "unknown"


def _summary(counts: Counter) -> str:
    """Render per-status version counts for the end-of-run log line."""
    return (f"{counts[FetchStatus.COMPLETE]} succeeded, "
            f"{counts[FetchStatus.PARTIAL]} partial, "
            f"{counts[FetchStatus.FAILED]} failed")


def _all_complete(counts: Counter) -> bool:
    return not (counts[FetchStatus.PARTIAL] or counts[FetchStatus.FAILED])


def parse_platforms(text: str) -> tuple:
    """Turn a --platform value such as "x86,arm64" into sorted Android ABIs."""
    requested = [part.strip().lower() for part in text.split(",")]
    for part in requested:
        if part not in PLATFORM_ABIS:
            raise argparse.ArgumentTypeError(
                f"unknown platform {part!r}; choose from "
                f"{', '.join(PLATFORM_ABIS)}"
            )
    return tuple(sorted({PLATFORM_ABIS[part] for part in requested}))


def version_argument(text: str) -> str:
    """Accept a --check value only if it can be compared."""
    if not is_version_number(text):
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a version number such as 13.2.0"
        )
    return text


def check_for_newer(app_config, version: str, output_dir: str, headless: bool,
                    source_filter: str = None) -> int:
    """Report whether any source lists a version newer than version.

    Returns the exit code: EXIT_NEWER_VERSION when one is listed, 0 when every
    source was read and none is newer, otherwise 1.
    """
    logger = logging.getLogger("check")
    newest_by_source = {}
    unreadable = []

    for source_name in ([source_filter] if source_filter else SOURCE_PRIORITY):
        source_config = app_config.get_source_config(source_name)
        if not source_config:
            continue
        try:
            scraper = SCRAPERS[source_name](output_dir=output_dir, headless=headless)
            listed = scraper.newest_version(source_config)
        except Exception as e:
            logger.warning(f"❌ Could not read {source_name}: {e}")
            unreadable.append(source_name)
            continue
        logger.info(f"{source_name}: newest listed version is {listed}")
        newest_by_source[source_name] = listed

    newest = max(newest_by_source.values(), key=version_key, default=version)
    if version_key(newest) > version_key(version):
        sources = ", ".join(name for name, listed in newest_by_source.items()
                            if version_key(listed) == version_key(newest))
        logger.info(f"🔔 Newer version of {app_config.name} available: {newest} "
                    f"(compared with {version}), listed on {sources}")
        return EXIT_NEWER_VERSION

    if unreadable or not newest_by_source:
        logger.error(f"❌ Could not check every source for {app_config.name}; "
                     f"nothing newer than {version} was found on the rest")
        return 1

    logger.info(f"✅ No source lists anything newer than {app_config.name} {version}")
    return 0


def fetch_apk(app_config, version: str, output_dir: str, headless: bool,
              source_filter: str = None, force: bool = False,
              all_sources: bool = False, platforms: tuple = ()) -> FetchStatus:
    """
    Fetch a single APK version.

    Each source downloads every variant it lists for the version, skipping
    variants already on disk (unless force). With platforms, only packages
    supporting every listed ABI are kept.

    Default mode: try sources in priority order, stop on first success (fallback).
    all_sources mode: attempt every configured source.

    A source succeeds when at least one variant was downloaded or already
    existed. The result is FAILED when no source succeeded and PARTIAL when one
    did but a variant failed, or, in all_sources mode, a source errored.
    """
    logger = logging.getLogger("fetcher")

    # Determine which sources to try
    if source_filter:
        sources_to_try = [source_filter]
    else:
        sources_to_try = SOURCE_PRIORITY

    any_success = False
    variant_failures = False
    source_errors = False

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
            scraper = scraper_class(output_dir=output_dir, headless=headless,
                                    platforms=platforms)
            result = scraper.scrape(source_config, version, force=force)

            for info in result.downloaded:
                logger.info(f"✅ Downloaded: {info.filepath}")
                logger.info(
                    f"   Source: {info.source} | Variant: {info.variant_id} | "
                    f"Type: {info.package_type} | "
                    f"Size: {info.size_bytes:,} bytes | "
                    f"Arch: {_format_abis(info.architectures)}"
                )
            for path in result.existing:
                logger.info(
                    f"⏭️  Already exists: {path} | "
                    f"Arch: {_format_abis(detect_architectures(path))}"
                )
            for variant_id, reason in result.failed:
                logger.warning(
                    f"❌ Variant {variant_id} failed on {source_name}: {reason}"
                )
            variant_failures = variant_failures or bool(result.failed)

            if result.succeeded:
                any_success = True
                if not all_sources:
                    break
                continue

        except VersionNotFoundError as e:
            logger.warning(f"⚠️  Version not found on {source_name}: {e}")
            continue
        except PlatformMismatchError as e:
            logger.warning(f"⚠️  {e}")
            continue
        except SourceBlockedError as e:
            logger.warning(f"🛑 Blocked by {source_name}: {e}")
            source_errors = True
            continue
        except DownloadError as e:
            logger.warning(f"❌ Download failed on {source_name}: {e}")
            source_errors = True
            continue
        except ScraperError as e:
            logger.error(f"🚨 Scraper error on {source_name}: {e}")
            source_errors = True
            continue
        except Exception as e:
            logger.error(f"🔥 Unexpected error on {source_name}: {e}")
            source_errors = True
            continue

    if not any_success:
        logger.error(f"❌ Failed to download {app_config.name} v{version} from any source")
        return FetchStatus.FAILED
    if variant_failures or (all_sources and source_errors):
        logger.warning(f"⚠️  {app_config.name} v{version} was only partly fetched")
        return FetchStatus.PARTIAL
    return FetchStatus.COMPLETE


def process_bulk(versions_file: str, apps_to_fetch: Dict, output_dir: str,
                 headless: bool, source_filter: str, force: bool = False,
                 all_sources: bool = False, platforms: tuple = ()) -> bool:
    """Process a bulk download from a JSON file."""
    logger = logging.getLogger("bulk")
    
    try:
        with open(versions_file, "r") as f:
            versions_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load versions file '{versions_file}': {e}")
        return False

    counts = Counter()

    # Normally bulk is for a specific app (e.g. telegram)
    for app_name, app_config in apps_to_fetch.items():
        logger.info(f"\n--- Starting bulk fetch for {app_name} ({len(versions_data)} versions) ---")
        
        for item in versions_data:
            version = item.get("version")
            if not version:
                continue

            logger.info(f"\n{'='*60}")
            logger.info(f"Target: {app_name} v{version}")
            logger.info(f"{'='*60}")

            counts[fetch_apk(app_config, version, output_dir, headless,
                             source_filter, force, all_sources,
                             platforms=platforms)] += 1

    logger.info(f"\n{'='*60}")
    logger.info(f"Bulk Fetch Summary: {_summary(counts)}")
    logger.info(f"{'='*60}")
    return _all_complete(counts)


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
    group.add_argument("--check", metavar="VERSION", type=version_argument,
                        help="Download nothing; report whether any source lists "
                             "a version newer than VERSION (exit code 3 if so)")
    group.add_argument("--platform", "-p", metavar="PLATFORMS",
                        type=parse_platforms, default=(),
                        help="Only keep packages that support every listed "
                             "platform, comma separated: arm (32-bit), arm64, "
                             "x86 (32-bit), x86_64")
    
    # Execution options
    exec_group = parser.add_argument_group("Execution Options")
    exec_group.add_argument("--config", "-c", default="config.yaml",
                            help="Path to config file (default: config.yaml)")
    exec_group.add_argument("--source", "-s", choices=list(SCRAPERS.keys()),
                            help="Use only this source (default: try all with fallback)")
    exec_group.add_argument("--all-sources", action="store_true",
                            help="Download from every configured source instead of "
                                 "stopping at the first success "
                                 "(every variant from every source)")
    exec_group.add_argument("--output", "-o",
                            help="Output directory (default: from config)")
    exec_group.add_argument("--force", "-f", action="store_true",
                            help="Overwrite existing downloads")
    exec_group.add_argument("--no-headless", action="store_true",
                            help="Show browser window (useful for debugging)")
    exec_group.add_argument("--debug", "-d", action="store_true",
                            help="Enable debug logging")
    
    args = parser.parse_args()

    if args.all_sources and args.source:
        parser.error("--all-sources cannot be combined with --source")
    if args.check:
        clashes = [flag for flag, used in (("--version", args.version),
                                           ("--bulk", args.bulk),
                                           ("--force", args.force),
                                           ("--platform", args.platform))
                   if used]
        if clashes:
            parser.error(f"--check cannot be combined with {', '.join(clashes)}")

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

    # Handle Check Mode
    if args.check:
        if len(apps_to_fetch) != 1:
            parser.error("--check needs --app when several apps are configured")
        app_config = next(iter(apps_to_fetch.values()))
        sys.exit(check_for_newer(app_config, args.check, output_dir, headless,
                                 args.source))

    # Handle Bulk Mode
    if args.bulk:
        success = process_bulk(args.bulk, apps_to_fetch, output_dir, headless,
                               args.source, args.force, args.all_sources,
                               platforms=args.platform)
        sys.exit(0 if success else 1)
        
    # Handle Standard Mode
    counts = Counter()

    for app_name, app_config in apps_to_fetch.items():
        # Version priority: 1. CLI Arg, 2. Config list, 3. Raise Error
        versions = [args.version] if args.version else app_config.versions
        
        if not versions:
            logger.warning(f"No versions specified for {app_name}, skipping")
            continue
            
        for version in versions:
            logger.info(f"\n{'='*60}")
            logger.info(f"Task: {app_name} v{version}")
            logger.info(f"{'='*60}")

            counts[fetch_apk(app_config, version, output_dir, headless,
                             args.source, args.force, args.all_sources,
                             platforms=args.platform)] += 1

    # Final Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"Execution Complete: {_summary(counts)}")
    logger.info(f"{'='*60}")

    sys.exit(0 if _all_complete(counts) else 1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping...")
        sys.exit(130)
