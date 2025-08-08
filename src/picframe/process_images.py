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
        scaled_file = self.classify_and_scale(file)
        processed_file = self.smart_crop(scaled_file)
        if processed_file is not None:
            try:
                self.add_to_db(file)
            #    file.unlink()
             #   self.__logger.info(f"Deleted imported file: {file}")
            except Exception as e:
                self.__logger.error(f"Failed to add {file} to database: {e}")
    

    def classify_and_scale(self, file):
        try:
            width, height = self.get_exif_corrected_dimensions(file)
            if width is None:
                return

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

            output_file = self.picture_dir / category / (file.stem + ".jpg")
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
            logging.error(f"Failed: {file.name} ({e})")

    def smart_crop(self, file):
        logging.info(f"Smart cropping {file.name}")
        return file

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