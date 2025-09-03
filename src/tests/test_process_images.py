#!/usr/bin/env python3
"""
Test script to verify ProcessImages class database connection.
"""

import os
import sys

# Add the current directory to Python path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from process_images import ProcessImages

    print("✓ ProcessImages class imported successfully")
except ImportError as e:
    print(f"✗ Failed to import ProcessImages: {e}")
    sys.exit(1)


# Mock model class for testing
class MockModel:
    def get_model_config(self):
        return {"db_file": "test_db.db"}

    def get_aspect_config(self):
        return {"width": 1920, "height": 1080, "import_dir": "/tmp/test_import", "JPEG_XL": False}

    def get_image_cache(self):
        return None


def test_process_images_initialization():
    """Test that ProcessImages can be instantiated without errors."""
    try:
        # Create a mock model
        mock_model = MockModel()

        # Try to instantiate ProcessImages
        process_images = ProcessImages(mock_model)
        print("✓ ProcessImages instantiated successfully")

        # Check if database connection exists
        if hasattr(process_images, "_ProcessImages__db"):
            print("✓ Database connection attribute exists")
        else:
            print("✗ Database connection attribute missing")
            return False

        # Test cleanup
        process_images.cleanup()
        print("✓ Cleanup method executed successfully")

        return True

    except Exception as e:
        print(f"✗ Failed to instantiate ProcessImages: {e}")
        return False


def main():
    """Main test function."""
    print("Testing ProcessImages class...")
    print("=" * 50)

    success = test_process_images_initialization()

    print("=" * 50)
    if success:
        print("✓ All tests passed!")
    else:
        print("✗ Some tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
