import asyncio
import logging
import os
import sqlite3
from pathlib import Path

import pyvips

from .config import DB_JRNL_MODE
from .file_utils import parse_filename_metadata
from .interface_http import EXTENSIONS


class ProcessImages:
    LOG_FILE = "resize_log.txt"
    MAX_WORKERS = 3  # Number of threads - reduced to prevent database locking

    def __init__(self, model):
        self.__logger = logging.getLogger(__name__)
        self.model = model
        self.db_file = os.path.expanduser(model.get_model_config()["db_file"])
        aspect_conf = model.get_aspect_config()
        self.target_width = aspect_conf.get("width")
        self.target_height = aspect_conf.get("height")
        self.square_image = aspect_conf.get("square_image", "Landscape")  # Orientation for square images
        self.input_folder = Path(os.path.expanduser(aspect_conf.get("import_dir")))
        self.picture_dir = Path(os.path.expanduser(model.get_model_config()["pic_dir"])).parent
        self.JPEG_XL = aspect_conf.get("JPEG_XL")
        self.smart_crop_enabled = aspect_conf.get("smart_crop", True)  # Enable smart cropping by default
        self.resampling_kernel = aspect_conf.get("resampling_kernel", "LANCZOS")  # Resampling kernel for image scaling
        self.__file_locks = {}  # Persistent file locks to prevent duplicate processing

        # Log smart cropping configuration
        if self.smart_crop_enabled:
            self.__logger.info("Smart cropping enabled - will try attention-based cropping with edge-based fallback")
        else:
            self.__logger.info("Smart cropping disabled - using center cropping")

        # === Ensure output subfolders exist ===
        for orientation in ["Landscape", "Portrait", "Square"]:
            (self.picture_dir / orientation).mkdir(parents=True, exist_ok=True)

        self.__db = sqlite3.connect(self.db_file, check_same_thread=False, timeout=30.0)
        # Configure main connection
        self.__db.execute(f"PRAGMA journal_mode={DB_JRNL_MODE}")
        self.__db.execute("PRAGMA synchronous=NORMAL")
        self.__db.execute("PRAGMA foreign_keys=ON")
        self.__db.execute("PRAGMA busy_timeout=30000")
        self.__db.execute("PRAGMA temp_store=MEMORY")
        self.__db.execute("PRAGMA mmap_size=30000000000")
        self.__db.execute("PRAGMA cache_size=10000")
        self.__logger.debug_detailed("ProcessImages database connection established")

        # Additional database configuration for better concurrency
        self.__db.execute("PRAGMA locking_mode=EXCLUSIVE")
        self.__db.execute("PRAGMA cache_size=-64000")  # 64MB cache

    def _get_pyvips_kernel(self):
        """Convert resampling kernel string to pyvips kernel."""
        kernel_map = {
            "NEAREST": "nearest",
            "BILINEAR": "linear",
            "BICUBIC": "cubic",
            "LANCZOS": "lanczos3",
            "BOX": "box",
            "HAMMING": "hamming",
        }
        return kernel_map.get(self.resampling_kernel.upper(), "lanczos3")  # Default to lanczos3 if unknown

    # === Image processing ===
    async def process_image(self, file_path):
        """Process a single image asynchronously.

        Args:
            file_path: Path to the image file (str or Path object)
        """
        # Convert string path to Path object if needed
        if isinstance(file_path, str):
            file_path = Path(file_path)

        self.__logger.debug(f"Starting processing of {file_path.name}")

        try:
            # Get image
            image = pyvips.Image.new_from_file(str(file_path), access="sequential")

            # Classify and scale image
            scaled_image, category = self.classify_and_scale(image, file_path)
            if scaled_image is None:
                self.__logger.warning(f"Failed to classify and scale {file_path.name}")
                return

            # Crop image
            processed_image, loss = self.smart_crop(scaled_image, file_path, category)
            if processed_image is None:
                self.__logger.warning(f"Failed to smart crop {file_path.name}")
                return

            # Save image (convert HEIC to JPEG for better compatibility)
            original_name = file_path.name
            if file_path.suffix.lower() in [".heic", ".heif"]:
                # Convert HEIC to JPEG
                jpeg_name = file_path.stem + ".jpg"
                output_file = self.picture_dir / category / jpeg_name
                self.__logger.info(f"Converting HEIC to JPEG: {original_name} -> {jpeg_name}")
            else:
                output_file = self.picture_dir / category / original_name

            try:
                if file_path.suffix.lower() in [".heic", ".heif"]:
                    # Convert pyvips image to PIL for JPEG saving
                    self.__logger.debug("Converting pyvips image to PIL for JPEG format")
                    # Convert pyvips to numpy array then to PIL
                    import numpy as np
                    from PIL import Image

                    # Get image data as numpy array
                    np_array = np.ndarray(
                        buffer=processed_image.write_to_memory(),
                        dtype=np.uint8,
                        shape=[processed_image.height, processed_image.width, processed_image.bands],
                    )

                    # Convert to PIL Image and save as JPEG
                    pil_image = Image.fromarray(np_array)
                    pil_image.save(str(output_file), "JPEG", quality=95, optimize=True)
                    self.__logger.info(f"Successfully saved HEIC as JPEG: {output_file}")
                else:
                    # Save in original format using pyvips
                    processed_image.write_to_file(str(output_file), Q=100)
            except Exception as e:
                self.__logger.error(f"Failed to save {original_name}")
                self.__logger.error(f"Save error details: {e}")
                self.__logger.error(f"Save error type: {type(e)}")
                raise

            # Add to database
            try:
                with self.__db:
                    self.add_to_db(output_file)
                self.__logger.debug(f"Successfully processed {file_path.name}")
            except Exception as e:
                self.__logger.error(f"Failed to add {file_path.name} to database: {e}")

            # Delete original file after successful database addition
            try:
                # Check if file still exists before trying to delete
                if file_path.exists():
                    file_path.unlink()
                    self.__logger.debug(f"Deleted imported file: {file_path}")
                else:
                    self.__logger.debug_detailed(f"File {file_path.name} was already deleted by another worker")
            except Exception as e:
                self.__logger.error(f"Failed to delete {file_path.name}: {e}")

        except Exception as e:
            self.__logger.error(f"Failed to process {file_path.name}: {e}")

    def classify_and_scale(self, image, file):
        try:
            width, height = image.width, image.height
            if width is None:
                self.__logger.warning(f"Could not get dimensions for {file.name}")
                return None

            if width > height:
                category = "Landscape"
                scale = min(self.target_width / width, self.target_height / height)
                logging.info(f"{file.name}: LANDSCAPE Scaled: {scale:.0%}")
            elif height > width:
                category = "Portrait"
                scale = min(self.target_width / height, self.target_height / width)
                logging.info(f"{file.name}: PORTRAIT Scaled: {scale:.0%}")
            else:
                category = "Square"
                scale = min(self.target_width / width, self.target_height / height)
                logging.info(f"{file.name}: SQUARE Scaled: {scale:.0%}")

            scaled_image = image.resize(scale)
            self.__logger.debug(
                f"{file.name}: Original: {image.width}x{image.height} --> "
                f"Resized: {scaled_image.width}x{scaled_image.height}"
            )

            return scaled_image, category

        except Exception as e:
            self.__logger.error(f"Failed to classify and scale {file.name}: {e}")
            self.__logger.error(f"Exception type: {type(e)}")

    def smart_crop(self, scaled_image, file, category):
        """
        Smart crop the image to the target dimensions.
        """
        # PLACEHOLDER - TODO: Implement smart cropping
        # This resizes the image to fit with letterboxing (if needed).

        if scaled_image is None:
            self.__logger.warning("smart_crop called with no image")
            return None

        try:
            self.__logger.debug(f"Smart cropping {file.name}")
            # Calculate crop loss
            if category == "Portrait" or self.square_image == "Portrait":
                v_crop = scaled_image.height - self.target_width
                loss = v_crop / self.target_height
            else:  # Landscape is default
                h_crop = scaled_image.width - self.target_width
                loss = h_crop / self.target_width
            self.__logger.debug(f"CENTER CROP LOSS WOULD BE: {loss:.0%}")

            # Load the image with pyvips
            image = pyvips.Image.new_from_file(str(file), access="sequential")

            # Use min to ensure both dimensions fit, max to fill & crop as needed
            scale = min(self.target_width / image.width, self.target_height / image.height)

            # Only scale if needed (allow small tolerance for rounding)
            if abs(scale - 1.0) > 0.02:
                self.__logger.debug_detailed(
                    f"Scaling {file.name} by {scale:.2f} to fit within bounds using {self.resampling_kernel} kernel"
                )
                kernel = self._get_pyvips_kernel()
                scaled_image = image.resize(scale, kernel=kernel)
            else:
                self.__logger.debug_detailed(f"No scaling needed for {file.name}, already fits perfectly")
                # Return the original image object (no scaling needed)
                return image, loss

            self.__logger.debug_detailed(f"Smart crop completed: {file.name}")
            return scaled_image, loss

        except Exception as e:
            self.__logger.error(f"Failed to smart crop {file.name}: {e}")
            return None

    def add_to_db(self, file):
        self.__logger.debug(f"Adding {file.name} to database")
        # Get the image cache from the model
        image_cache = self.model.get_image_cache()
        # Convert Path object to string and insert into database
        filename = str(file)
        source, playlist = self.parse_filename(filename)
        image_cache.insert_file(filename, source=source, playlist=playlist)

    def parse_filename(self, filename):
        """Parse filename to extract source and playlist using shared utility."""
        # This is a bit of a hack, but the idea behind the import folder and processing
        # is that any image can be dropped into the import folder and it will get processed.
        # This figures our the source and playlist from the filename created in the import process.
        configured_sources = self.model.get_aspect_config().get("sources", {})
        return parse_filename_metadata(filename, configured_sources)

    # === Batch run with parallel threads ===
    async def process_images(self):
        # self.__logger.debug("Processing images...")
        self.__logger.debug_detailed(f"Input folder: {self.input_folder}")
        # Use extensions defined in interface_http for consistency
        files = [f for f in self.input_folder.iterdir() if f.suffix.lower() in EXTENSIONS]
        if not files:
            self.__logger.debug_detailed("No images found in input folder.")
            return

        self.__logger.debug_detailed(f"Processing {len(files)} files with {self.MAX_WORKERS} threads...")

        sem = asyncio.Semaphore(self.MAX_WORKERS)

        async def sem_task(file):
            # Check if file is already being processed
            if file in self.__file_locks:
                self.__logger.debug_verbose(f"File {file.name} is already being processed, skipping duplicate")
                return

            # Get or create lock for this specific file
            if file not in self.__file_locks:
                self.__file_locks[file] = asyncio.Lock()

            self.__logger.debug_detailed(f"Starting to process file: {file.name}")

            try:
                async with self.__file_locks[file]:  # Ensure only one task processes this file
                    async with sem:
                        self.__logger.debug_verbose(
                            f"File lock acquired, processing: {file.name} (size: {file.stat().st_size} bytes)"
                        )
                        await self.process_image(file)
            except Exception as e:
                self.__logger.error(f"Error processing {file.name}: {e}")
            finally:
                # Clean up the lock after processing (even if there was an error)
                if file in self.__file_locks:
                    del self.__file_locks[file]
                    self.__logger.debug_detailed(f"Completed processing file: {file.name}")

        tasks = [sem_task(file) for file in files]
        await asyncio.gather(*tasks)

        self.__logger.debug_detailed("Process Images Done.")

    # Legacy alias for backwards compatibility
    async def process_single_image_async(self, file_path):
        """Legacy method - use process_image() instead."""
        await self.process_image(file_path)

    def cleanup(self):
        """Clean up database connection."""
        try:
            if hasattr(self, "__db") and self.__db:
                self.__db.close()
                self.__logger.debug_detailed("ProcessImages database connection closed")
        except Exception as e:
            self.__logger.warning(f"Error closing ProcessImages database: {e}")
