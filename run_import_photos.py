#!/usr/bin/env python3
"""
Runner script for import_photos.py

This script creates the necessary Model instance and runs the ImportPhotos functionality.
"""

import os
import sys

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from picframe.import_photos import ImportPhotos
from picframe.model import Model


def main():
    print("Starting import_photos with Model...")

    try:
        # Create Model instance with config file
        config_file = "~/picframe_data/config/configuration.yaml"
        print(f"Loading configuration from: {config_file}")
        model = Model(config_file)

        # Create ImportPhotos instance
        print("Creating ImportPhotos instance...")
        importer = ImportPhotos(model)

        # Check if import sources are configured
        aspect_config = model.get_aspect_config()
        sources = aspect_config.get("sources", {})

        enabled_sources = [name for name, config in sources.items() if config.get("enable", False)]
        if not enabled_sources:
            print("No import sources are enabled in configuration.")
            print("Please enable at least one source in your configuration.yaml file.")
            return 1

        print(f"Enabled import sources: {', '.join(enabled_sources)}")

        # Run the import process
        print("Starting import process...")

        # Actually run the import!
        import asyncio

        asyncio.run(importer.check_for_updates())

        print("Import process completed!")

        return 0

    except FileNotFoundError as e:
        print(f"Configuration file not found: {e}")
        print("Please ensure ~/picframe_data/config/configuration.yaml exists")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
