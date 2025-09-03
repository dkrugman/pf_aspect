#!/usr/bin/env python3
"""
Test script to demonstrate import throttling functionality.
This script shows how the throttling parameters work without requiring a full import operation.
"""

import asyncio
import logging
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class ThrottlingDemo:
    """Demonstrate throttling functionality similar to ImportPhotos."""

    def __init__(self, max_concurrent_downloads=5, max_concurrent_db_operations=3, download_batch_size=10):
        self.max_concurrent_downloads = max_concurrent_downloads
        self.max_concurrent_db_operations = max_concurrent_db_operations
        self.download_batch_size = download_batch_size

        logger.info(f"Throttling demo initialized with:")
        logger.info(f"  max_concurrent_downloads: {self.max_concurrent_downloads}")
        logger.info(f"  max_concurrent_db_operations: {self.max_concurrent_db_operations}")
        logger.info(f"  download_batch_size: {self.download_batch_size}")

    async def simulate_downloads(self, total_items=50):
        """Simulate downloading items with throttling."""
        logger.info(f"\n=== Starting download simulation for {total_items} items ===")

        # Create semaphores for throttling
        download_semaphore = asyncio.Semaphore(self.max_concurrent_downloads)
        db_semaphore = asyncio.Semaphore(self.max_concurrent_db_operations)

        # Process items in batches
        processed = 0
        start_time = time.time()

        for i in range(0, total_items, self.download_batch_size):
            batch = list(range(i, min(i + self.download_batch_size, total_items)))
            batch_num = (i // self.download_batch_size) + 1
            total_batches = (total_items + self.download_batch_size - 1) // self.download_batch_size

            logger.info(f"\n--- Processing batch {batch_num}/{total_batches} ({len(batch)} items) ---")

            # Create tasks for this batch
            download_tasks = []
            for item_id in batch:
                task = self._simulate_single_download(item_id, download_semaphore, db_semaphore)
                download_tasks.append(task)

            # Execute batch downloads with throttling
            if download_tasks:
                results = await asyncio.gather(*download_tasks, return_exceptions=True)

                # Log batch results
                success_count = sum(1 for r in results if r is True)
                error_count = len(results) - success_count
                processed += len(batch)

                logger.info(
                    f"Batch {batch_num}/{total_batches} complete: {success_count} successful, {error_count} failed"
                )
                logger.info(f"Total progress: {processed}/{total_items}")

                # Small delay between batches
                if i + self.download_batch_size < total_items:
                    await asyncio.sleep(0.5)

        total_time = time.time() - start_time
        logger.info(f"\n=== Download simulation completed in {total_time:.2f} seconds ===")
        logger.info(f"Average time per item: {total_time/total_items:.2f} seconds")

    async def _simulate_single_download(self, item_id, download_semaphore, db_semaphore):
        """Simulate downloading a single item with throttling."""
        async with download_semaphore:  # Limit concurrent downloads
            start_time = time.time()
            logger.debug(f"Starting download for item {item_id}")

            # Simulate download time (random between 0.1 and 0.5 seconds)
            import random

            download_time = random.uniform(0.1, 0.5)
            await asyncio.sleep(download_time)

            # Simulate database operation with throttling
            async with db_semaphore:  # Limit concurrent database operations
                db_time = random.uniform(0.05, 0.2)
                await asyncio.sleep(db_time)

                total_time = time.time() - start_time
                logger.debug(
                    f"Item {item_id} completed in {total_time:.3f}s (download: {download_time:.3f}s, db: {db_time:.3f}s)"
                )

                return True


async def main():
    """Main function to run the throttling demo."""
    logger.info("Import Throttling Demo")
    logger.info("=" * 50)

    # Test with default settings
    demo = ThrottlingDemo()
    await demo.simulate_downloads(50)

    logger.info("\n" + "=" * 50)

    # Test with more restrictive settings
    logger.info("Testing with more restrictive throttling...")
    demo_restrictive = ThrottlingDemo(max_concurrent_downloads=2, max_concurrent_db_operations=1, download_batch_size=5)
    await demo_restrictive.simulate_downloads(30)

    logger.info("\n" + "=" * 50)

    # Test with very permissive settings
    logger.info("Testing with very permissive throttling...")
    demo_permissive = ThrottlingDemo(
        max_concurrent_downloads=10, max_concurrent_db_operations=5, download_batch_size=20
    )
    await demo_permissive.simulate_downloads(40)


if __name__ == "__main__":
    asyncio.run(main())
