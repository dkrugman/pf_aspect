#!/usr/bin/env python3
"""
File Time Utilities for Picframe

This module provides enhanced file time functionality, including creation time,
for systems where Python's os.stat() doesn't support st_birthtime.
"""

import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


class FileTimeInfo:
    """Enhanced file time information including creation time."""

    def __init__(self, file_path: Union[str, Path]):
        self.file_path = str(file_path)
        self._stat_info = None
        self._birth_time = None
        self._load_times()

    def _load_times(self):
        """Load all available time information for the file."""
        try:
            # Get basic stat info
            self._stat_info = os.stat(self.file_path)

            # Try to get birth time using system stat command
            self._birth_time = self._get_birth_time_system()

        except Exception as e:
            logger.warning(f"Error loading time info for {self.file_path}: {e}")

    def _get_birth_time_system(self) -> Optional[datetime]:
        """Get file creation time using system stat command."""
        try:
            result = subprocess.run(["stat", self.file_path], capture_output=True, text=True, timeout=5)

            if result.returncode == 0:
                # Look for Birth: line in stat output
                for line in result.stdout.split("\n"):
                    if line.strip().startswith("Birth:"):
                        timestamp_str = line.split("Birth:")[1].strip()
                        return self._parse_timestamp(timestamp_str)

            return None

        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout getting birth time for {self.file_path}")
            return None
        except Exception as e:
            logger.debug(f"System stat failed for {self.file_path}: {e}")
            return None

    def _parse_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        """Parse timestamp string from stat command output."""
        try:
            # Handle different timestamp formats
            if "." in timestamp_str:
                # Format: 2025-08-12 11:37:02.227031759 -0400
                dt_str = timestamp_str.split(".")[0]
                return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            else:
                # Format: 2025-08-12 11:37:02 -0400
                return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")

        except ValueError as e:
            logger.debug(f"Error parsing timestamp '{timestamp_str}': {e}")
            return None

    @property
    def birth_time(self) -> Optional[datetime]:
        """Get file creation time (birth time)."""
        return self._birth_time

    @property
    def modification_time(self) -> Optional[datetime]:
        """Get file modification time."""
        if self._stat_info:
            return datetime.fromtimestamp(self._stat_info.st_mtime)
        return None

    @property
    def change_time(self) -> Optional[datetime]:
        """Get file change time (metadata change)."""
        if self._stat_info:
            return datetime.fromtimestamp(self._stat_info.st_ctime)
        return None

    @property
    def access_time(self) -> Optional[datetime]:
        """Get file access time."""
        if self._stat_info:
            return datetime.fromtimestamp(self._stat_info.st_atime)
        return None

    def get_all_times(self) -> dict:
        """Get all available time information."""
        return {
            "birth_time": self.birth_time,
            "modification_time": self.modification_time,
            "change_time": self.change_time,
            "access_time": self.access_time,
            "birth_time_available": self.birth_time is not None,
        }

    def __str__(self) -> str:
        times = self.get_all_times()
        return f"FileTimeInfo({self.file_path}): {times}"


def get_file_birth_time(file_path: Union[str, Path]) -> Optional[datetime]:
    """
    Get file creation time (birth time).

    Args:
        file_path: Path to the file

    Returns:
        datetime object representing creation time, or None if unavailable
    """
    try:
        time_info = FileTimeInfo(file_path)
        return time_info.birth_time
    except Exception as e:
        logger.debug(f"Error getting birth time for {file_path}: {e}")
        return None


def get_file_times(file_path: Union[str, Path]) -> dict:
    """
    Get all available time information for a file.

    Args:
        file_path: Path to the file

    Returns:
        Dictionary containing all available time information
    """
    try:
        time_info = FileTimeInfo(file_path)
        return time_info.get_all_times()
    except Exception as e:
        logger.debug(f"Error getting file times for {file_path}: {e}")
        return {}


def is_birth_time_available() -> bool:
    """
    Check if file creation time (birth time) is available on this system.

    Returns:
        True if birth time is available, False otherwise
    """
    try:
        # Create a temporary file to test
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_path = f.name

        try:
            # Check if we can get birth time
            birth_time = get_file_birth_time(temp_path)
            return birth_time is not None
        finally:
            # Clean up
            if os.path.exists(temp_path):
                os.remove(temp_path)

    except Exception:
        return False


def get_file_age(file_path: Union[str, Path], reference_time: Optional[datetime] = None) -> Optional[float]:
    """
    Get the age of a file in seconds.

    Args:
        file_path: Path to the file
        reference_time: Reference time (defaults to current time)

    Returns:
        Age in seconds, or None if unavailable
    """
    try:
        if reference_time is None:
            reference_time = datetime.now()

        # Try birth time first, fall back to modification time
        birth_time = get_file_birth_time(file_path)
        if birth_time:
            return (reference_time - birth_time).total_seconds()

        # Fall back to modification time
        time_info = FileTimeInfo(file_path)
        if time_info.modification_time:
            return (reference_time - time_info.modification_time).total_seconds()

        return None

    except Exception as e:
        logger.debug(f"Error getting file age for {file_path}: {e}")
        return None


def sort_files_by_time(file_paths: list, time_type: str = "birth", reverse: bool = False) -> list:
    """
    Sort files by time (birth, modification, change, or access).

    Args:
        file_paths: List of file paths
        time_type: Type of time to sort by ('birth', 'modification', 'change', 'access')
        reverse: Sort in reverse order (newest first)

    Returns:
        Sorted list of file paths
    """
    try:

        def get_sort_key(file_path):
            time_info = FileTimeInfo(file_path)
            if time_type == "birth" and time_info.birth_time:
                return time_info.birth_time
            elif time_type == "modification" and time_info.modification_time:
                return time_info.modification_time
            elif time_type == "change" and time_info.change_time:
                return time_info.change_time
            elif time_type == "access" and time_info.access_time:
                return time_info.access_time
            else:
                # Fall back to modification time
                return time_info.modification_time or datetime.min

        return sorted(file_paths, key=get_sort_key, reverse=reverse)

    except Exception as e:
        logger.error(f"Error sorting files by time: {e}")
        return file_paths


# Convenience functions for common use cases
def get_oldest_file(file_paths: list) -> Optional[str]:
    """Get the oldest file from a list of file paths."""
    sorted_files = sort_files_by_time(file_paths, reverse=False)
    return sorted_files[0] if sorted_files else None


def get_newest_file(file_paths: list) -> Optional[str]:
    """Get the newest file from a list of file paths."""
    sorted_files = sort_files_by_time(file_paths, reverse=True)
    return sorted_files[0] if sorted_files else None


# Test function
if __name__ == "__main__":
    # Test the module
    print("File Time Utilities Test")
    print("=" * 30)

    # Check if birth time is available
    print(f"Birth time available: {is_birth_time_available()}")

    # Test with a real file
    test_file = __file__
    if os.path.exists(test_file):
        print(f"\nTesting with: {test_file}")

        # Get all times
        times = get_file_times(test_file)
        for time_type, time_value in times.items():
            print(f"{time_type}: {time_value}")

        # Get file age
        age = get_file_age(test_file)
        if age:
            print(f"File age: {age:.1f} seconds ({age/3600:.1f} hours)")

    print("\nTest completed!")
