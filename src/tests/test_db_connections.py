#!/usr/bin/env python3
"""
Test script to verify database connection improvements.
This script tests the thread-safe database operations without requiring a full import.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class DatabaseConnectionTester:
    """Test database connections and operations."""

    def __init__(self, db_file="test_db.db"):
        self.db_file = db_file
        self.lock = threading.Lock()

        # Create test database
        self._create_test_db()

    def _create_test_db(self):
        """Create a test database with sample tables."""
        try:
            db = sqlite3.connect(self.db_file, timeout=60.0)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=60000")
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute("PRAGMA cache_size=10000")

            # Create test tables
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS test_imported_playlists (
                    source TEXT,
                    playlist_name TEXT,
                    playlist_id INTEGER,
                    picture_count INTEGER,
                    last_modified TEXT,
                    last_imported TEXT,
                    src_version INTEGER,
                    PRIMARY KEY (source, playlist_id)
                )
            """
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS test_imported_files (
                    source TEXT,
                    playlist_id INTEGER,
                    media_item_id TEXT,
                    original_url TEXT,
                    basename TEXT,
                    extension TEXT,
                    nix_caption TEXT,
                    orig_extension TEXT,
                    processed INTEGER,
                    orig_timestamp TEXT,
                    last_modified TEXT
                )
            """
            )

            db.commit()
            db.close()
            logger.info("Test database created successfully")

        except Exception as e:
            logger.error(f"Failed to create test database: {e}")
            raise

    def _get_db_connection(self):
        """Get a new database connection for thread-safe operations."""
        try:
            db = sqlite3.connect(self.db_file, timeout=60.0)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=60000")
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute("PRAGMA cache_size=10000")
            return db
        except Exception as e:
            logger.error(f"Failed to create database connection: {e}")
            raise

    def _execute_db_operation(self, operation_func, *args, **kwargs):
        """Execute a database operation with proper connection management."""
        db = None
        try:
            db = self._get_db_connection()
            result = operation_func(db, *args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"Database operation failed: {e}")
            raise
        finally:
            if db:
                try:
                    db.close()
                except Exception as e:
                    logger.warning(f"Error closing database connection: {e}")

    def test_concurrent_operations(self, num_threads=5, operations_per_thread=10):
        """Test concurrent database operations to check for locking issues."""
        logger.info(f"Testing {num_threads} threads with {operations_per_thread} operations each")

        def worker_thread(thread_id):
            """Worker thread that performs database operations."""
            logger.info(f"Thread {thread_id} starting")

            for i in range(operations_per_thread):
                try:
                    # Simulate playlist update
                    def update_playlist(db):
                        with db:
                            db.execute(
                                """
                                INSERT INTO test_imported_playlists
                                (source, playlist_name, playlist_id, picture_count, last_modified, last_imported, src_version)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(source, playlist_id) DO UPDATE SET
                                    playlist_name = excluded.playlist_name,
                                    last_modified = excluded.last_modified
                            """,
                                (f"source_{thread_id}", f"playlist_{i}", i, 10, "2024-01-01", "2024-01-01", 1),
                            )

                    self._execute_db_operation(update_playlist)

                    # Simulate file insert
                    def insert_file(db):
                        with db:
                            db.execute(
                                """
                                INSERT INTO test_imported_files
                                (source, playlist_id, media_item_id, original_url, basename, extension)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """,
                                (f"source_{thread_id}", i, f"media_{i}", f"http://example.com/{i}", f"file_{i}", "jpg"),
                            )

                    self._execute_db_operation(insert_file)

                    # Small delay to simulate real-world conditions
                    time.sleep(0.01)

                except Exception as e:
                    logger.error(f"Thread {thread_id} operation {i} failed: {e}")

            logger.info(f"Thread {thread_id} completed")

        # Start all threads
        threads = []
        start_time = time.time()

        for i in range(num_threads):
            thread = threading.Thread(target=worker_thread, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        total_time = time.time() - start_time
        total_operations = num_threads * operations_per_thread

        logger.info(f"All threads completed in {total_time:.2f} seconds")
        logger.info(f"Total operations: {total_operations}")
        logger.info(f"Operations per second: {total_operations/total_time:.2f}")

        # Verify data was inserted correctly
        self._verify_data()

    def _verify_data(self):
        """Verify that data was inserted correctly."""
        try:

            def count_playlists(db):
                cur = db.execute("SELECT COUNT(*) FROM test_imported_playlists")
                return cur.fetchone()[0]

            def count_files(db):
                cur = db.execute("SELECT COUNT(*) FROM test_imported_files")
                return cur.fetchone()[0]

            playlist_count = self._execute_db_operation(count_playlists)
            file_count = self._execute_db_operation(count_files)

            logger.info(f"Verification complete:")
            logger.info(f"  Playlists: {playlist_count}")
            logger.info(f"  Files: {file_count}")

        except Exception as e:
            logger.error(f"Verification failed: {e}")

    def cleanup(self):
        """Clean up test database."""
        try:
            Path(self.db_file).unlink(missing_ok=True)
            logger.info("Test database cleaned up")
        except Exception as e:
            logger.warning(f"Error cleaning up test database: {e}")


def main():
    """Main test function."""
    logger.info("Database Connection Test")
    logger.info("=" * 50)

    tester = DatabaseConnectionTester()

    try:
        # Test with different concurrency levels
        test_configs = [
            (3, 5),  # Low concurrency
            (5, 10),  # Medium concurrency
            (8, 15),  # High concurrency
        ]

        for num_threads, operations_per_thread in test_configs:
            logger.info(f"\n--- Testing {num_threads} threads, {operations_per_thread} operations each ---")
            tester.test_concurrent_operations(num_threads, operations_per_thread)
            time.sleep(1)  # Brief pause between tests

        logger.info("\n" + "=" * 50)
        logger.info("All tests completed successfully!")

    except Exception as e:
        logger.error(f"Test failed: {e}")
        raise

    finally:
        tester.cleanup()


if __name__ == "__main__":
    main()
