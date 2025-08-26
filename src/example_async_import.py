#!/usr/bin/env python3
"""
Example of how to use the async ImportPhotos class
"""

import asyncio
import logging
from picframe.import_photos_async import ImportPhotosAsync

# Set up logging
logging.basicConfig(level=logging.INFO)

async def main():
    """Example usage of async photo import."""
    
    # You'll need to pass your model instance here
    # model = YourModelClass()
    
    try:
        # Create async importer
        async with ImportPhotosAsync(model) as importer:
            
            # Check if already importing
            if importer.is_importing():
                print("Import already in progress...")
                return
            
            # Start the import process
            print("Starting async photo import...")
            await importer.check_for_updates()
            print("Import completed!")
            
    except Exception as e:
        print(f"Error during import: {e}")

# Alternative: Run import in background
async def background_import_example():
    """Example of running import as a background task."""
    
    async def import_task():
        async with ImportPhotosAsync(model) as importer:
            await importer.check_for_updates()
    
    # Start import as background task
    task = asyncio.create_task(import_task())
    
    # Do other work while import runs
    for i in range(10):
        print(f"Doing other work... {i}")
        await asyncio.sleep(1)
    
    # Wait for import to complete
    await task
    print("Background import completed!")

if __name__ == "__main__":
    # Run the async import
    asyncio.run(main())




