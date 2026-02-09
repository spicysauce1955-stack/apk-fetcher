#!/usr/bin/env python3
"""Configuration loader for APK scraper."""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import yaml


@dataclass
class SourceConfig:
    """Configuration for a specific source."""
    name: str
    config: Dict


@dataclass  
class AppConfig:
    """Configuration for an app to fetch."""
    name: str
    versions: List[str]
    sources: Dict[str, Dict] = field(default_factory=dict)
    
    def get_source_config(self, source_name: str) -> Optional[Dict]:
        """Get config for a specific source."""
        return self.sources.get(source_name)


@dataclass
class Config:
    """Main configuration."""
    apps: Dict[str, AppConfig]
    output_dir: str = "./downloads"
    headless: bool = True
    
    def get_app(self, name: str) -> Optional[AppConfig]:
        """Get app config by name."""
        return self.apps.get(name)


def load_config(config_path: str = "config.yaml") -> Config:
    """Load configuration from YAML file."""
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)
    
    apps = {}
    for app_name, app_data in raw.get("apps", {}).items():
        apps[app_name] = AppConfig(
            name=app_name,
            versions=app_data.get("versions", []),
            sources=app_data.get("sources", {})
        )
    
    return Config(
        apps=apps,
        output_dir=raw.get("output_dir", "./downloads"),
        headless=raw.get("headless", True)
    )
