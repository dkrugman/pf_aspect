import os
import logging
import exifread
import pyvips
import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

class ProcessImages:
    LOG_FILE = "resize_log.txt"
    MAX_WORKERS = 4  # Number of threads

    def __init__(self, model):
        self.__logger = logging.getLogger(__name__)
        self.model = model
        self.db_file = os.path.expanduser(model.get_model_config()['db_file'])
        aspect_conf = model.get_aspect_config()
        self.target_width = aspect_conf.get('width')
        self.target_height = aspect_conf.get('height')
        self.input_folder = Path(os.path.expanduser(aspect_conf.get('import_dir')))
        self.picture_dir = Path(os.path.expanduser(model.get_model_config()['pic_dir'])).parent
        self.JPEG_XL = aspect_conf.get('JPEG_XL') 
        # === Ensure output subfolders exist ===
        for orientation in ["Landscape", "Portrait", "Square"]:
            (self.picture_dir / orientation).mkdir(parents=True, exist_ok=True)

    # === EXIF orientation parser ===
    def get_exif_corrected_dimensions(self, filepath):
        try:
            with open(filepath, 'rb') as f:
                tags = exifread.process_file(f, stop_tag='Image Orientation', details=False)
                orientation = tags.get('Image Orientation', None)
                rotated = orientation and 'Rotated' in str(orientation)
        except Exception:
            rotated = False

        try:
            image = pyvips.Image.new_from_file(str(filepath), access="sequential")
            width, height = image.width, image.height
            if rotated:
                width, height = height, width
            return width, height
        except Exception as e:
            logging.error(f"Failed to read image dimensions: {filepath} ({e})")
            return None, None

    # === Image processing ===
    def process_image(self, file):
        try:
            scaled_file = self.classify_and_scale(file)
            if scaled_file is None:
                self.__logger.warning(f"Failed to classify and scale {file.name}")
                return
                
            processed_file = self.smart_crop(scaled_file)
            if processed_file is None:
                self.__logger.warning(f"Failed to smart crop {file.name}")
                return
                
            # Add to database
            try:
                self.add_to_db(processed_file)
                self.__logger.info(f"Successfully processed {file.name}")
            except Exception as e:
                self.__logger.error(f"Failed to add {file.name} to database: {e}")
                return
                
        except Exception as e:
            self.__logger.error(f"Failed to process {file.name}: {e}")
        finally:
            # Always try to delete the original file, regardless of processing success
            try:
                file.unlink()
                self.__logger.info(f"Deleted imported file: {file}")
            except Exception as e:
                self.__logger.error(f"Failed to delete {file}: {e}")

    def classify_and_scale(self, file):
        try:
            width, height = self.get_exif_corrected_dimensions(file)
            if width is None:
                self.__logger.warning(f"Could not get dimensions for {file.name}")
                return None

            # Classify + scale
            if width > height:
                category = "Landscape"
                scale = max(self.target_width / width, self.target_height / height)
                logging.info(f"LANDSCAPE Scaled: {scale:.0%}")
            elif height > width:
                category = "Portrait"
                scale = max(self.target_width / height, self.target_height / width)
                logging.info(f"PORTRAIT Scaled: {scale:.0%}")
            else:
                category = "Square"
                scale = max(self.target_width / width, self.target_height / height)

            image = pyvips.Image.new_from_file(str(file), access="sequential")
            resized = image.resize(scale)

            output_file = self.picture_dir / category / file.name
            resized.write_to_file(str(output_file), Q=100 )

            # Calculate crop loss
            if category == "Landscape":
                h_crop = resized.width - 2894
                v_crop = resized.height - 2160
                loss = h_crop/2894
            elif category == "Portrait":
                h_crop = resized.width - 2160
                v_crop = resized.height - 2894
                loss = v_crop/2160
            else:
                h_crop = resized.width -2894
                v_crop = resized.height - 2160
                loss = (h_crop/2894)*100

            self.__logger.info(f"{file.name}: {category}. {scale:.0%} - h crop: {h_crop/2} v crop: {v_crop/2} - LOSS: {loss:.0%}")
            self.__logger.debug(f"{file.name} → {category}, {resized.width}x{resized.height} → {output_file.name}")

            return output_file

        except Exception as e:
            self.__logger.error(f"Failed to classify and scale {file.name}: {e}")
            return None

    def smart_crop(self, file):
        if file is None:
            self.__logger.warning("smart_crop called with None file")
            return None
            
        try:
            self.__logger.info(f"Smart cropping {file.name}")
            # Currently just returns the file, but could add actual cropping logic here
            return file
        except Exception as e:
            self.__logger.error(f"Failed to smart crop {file.name}: {e}")
            return None

    def add_to_db(self, file):
        self.__logger.info(f"Adding {file.name} to database")
        # Get the image cache from the model
        image_cache = self.model.get_image_cache()
        # Convert Path object to string and insert into database
        file_path = str(file)
        image_cache.insert_file(file_path)
        


        # === Batch run with parallel threads ===
    async def process_images(self):
        self.__logger.info("Processing images...")
        self.__logger.info(f"Input folder: {self.input_folder}")
        files = list(self.input_folder.glob("*.[jJpP][pPnN]*"))
        if not files:
            self.__logger.warning("No images found in input folder.")
            return

        self.__logger.debug(f"Processing {len(files)} files with {self.MAX_WORKERS} threads...")

        sem = asyncio.Semaphore(self.MAX_WORKERS)

        async def sem_task(file):
            async with sem:
                await asyncio.to_thread(self.process_image, file)

        tasks = [sem_task(file) for file in files]
        await asyncio.gather(*tasks)

        self.__logger.info("Done.")