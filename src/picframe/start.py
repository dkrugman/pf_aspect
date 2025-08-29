import sys, os, logging, locale, argparse, asyncio, signal
from shutil import copytree
from . import model, viewer_display, controller, __version__

# Global imports and setup

PICFRAME_DATA_DIR = 'picframe_data'

def copy_files(pkgdir, dest, target):
    try:
        fullpath = os.path.join(pkgdir,  target)
        destination = os.path.join(dest,  PICFRAME_DATA_DIR)
        destination = os.path.join(destination,  target)
        copytree(fullpath,  destination)
    except Exception:
        raise

def create_config(root):
    fullpath_root = os.path.join(root,  PICFRAME_DATA_DIR)
    fullpath = os.path.join(fullpath_root, 'config')
    source = os.path.join(fullpath, 'configuration_example.yaml')
    destination = os.path.join(fullpath, 'configuration.yaml')

    try:
        with open(source, "r") as file:
            filedata = file.read()

        print("This will configure ", destination)
        print("To keep default, just hit enter")

        # replace all paths with selected picframe_data path
        filedata = filedata.replace("~/picframe_data", fullpath_root)

        # pic_dir
        pic_dir = input("Enter picture directory [~/Pictures]: ")
        if pic_dir == "":
            pic_dir = "~/Pictures"  # convert to absolute path too for work-around on RPi4 running as root
        pic_dir = os.path.expanduser(pic_dir)
        filedata = filedata.replace("~/Pictures", pic_dir)

        # deleted_pictures
        deleted_pictures = input("Enter picture directory [~/DeletedPictures]: ")
        if deleted_pictures == "":
            deleted_pictures = "~/DeletedPictures"
        deleted_pictures = os.path.expanduser(deleted_pictures)
        filedata = filedata.replace("~/DeletedPictures", deleted_pictures)

        # locale
        lan, enc = locale.getlocale()
        if not lan:
            (lan, enc) = ("en_US", "utf8")
        param = input("Enter locale [" + lan + "." + enc + "]: ") or (lan + "." + enc)
        filedata = filedata.replace("en_US.utf8", param)

        with open(destination, "w") as file:
            file.write(filedata)
    except Exception:
        raise

def check_packages(packages):
    for package in packages:
        try:
            if package == 'paho.mqtt':
                import paho.mqtt
                print(package, ': ', paho.mqtt.__version__)
            elif package == 'ninepatch':
                import ninepatch  # noqa: F401
                print(package, ': installed, but no version info')
            else:
                print(package, ': ', __import__(package).__version__)
        except ImportError:
            print(package, ': Not found!')

# Global signal handler function
def picframe_signal_handler(signum, controller_ref):
    """Asyncio-compatible signal handler."""
    logger = logging.getLogger(__name__)
    # Access the controller instance
    if controller_ref[0]:
        # Stop the main loop first
        controller_ref[0].keep_looping = False
        
        try:
            logger.info("Calling controller.stop() from signal handler...")
            controller_ref[0].stop()
            logger.info("Controller stop() completed from signal handler")
            # Mark that stop has been called to prevent duplicate calls
            controller_ref[0]._stop_called = True
            
        except Exception as e:
            logger.error(f"Error in signal handler: {e}")
    else:
        logger.warning("No controller instance available for signal handling")

def setup_signal_handlers(loop, controller_ref):
    """Set up asyncio signal handlers for graceful shutdown."""
    # Register the signal handlers with controller reference
    loop.add_signal_handler(signal.SIGTERM, lambda: picframe_signal_handler(signal.SIGTERM, controller_ref))
    loop.add_signal_handler(signal.SIGINT, lambda: picframe_signal_handler(signal.SIGINT, controller_ref))

async def run_picframe_app(args=None):
    """Main picframe application function."""
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s")
    logger = logging.getLogger(__name__)
    logger.info('starting %s', sys.argv)
    
    # === Suppress logs from external libraries ===
    for noisy_logger in ['pyvips', 'urllib3', 'PIL', 'chardet', 'requests', 'exifread', 'pi3d', 'pi3lib', 'iptcinfo']:
        logging.getLogger(noisy_logger).setLevel(logging.ERROR)
    
    # Parse arguments if not provided
    if args is None:
        parser = argparse.ArgumentParser()
        group = parser.add_mutually_exclusive_group()
        group.add_argument("-i", "--initialize",
                           help="creates standard file structure for picframe in destination directory",
                           metavar=('DESTINATION_DIRECTORY'))
        group.add_argument("-v", "--version", help="print version information",
                           action="store_true")
        group.add_argument("configfile", nargs='?', help="/path/to/configuration.yaml")
        args = parser.parse_args()
    
    # Handle special arguments
    if args.initialize:
        if os.geteuid() == 0:
            print("Don't run the initialize step with sudo. It might put the files in the wrong place!")
            return
        pkgdir = sys.modules['picframe'].__path__[0]
        try:
            dest = os.path.abspath(os.path.expanduser(args.initialize))
            copy_files(pkgdir, dest, 'html')
            copy_files(pkgdir, dest, 'config')
            copy_files(pkgdir, dest, 'data')
            create_config(dest)
            print('created {}/picframe_data'.format(dest))
        except Exception as e:
            print("Can't copy files to: ", args.initialize, ". Reason: ", e)
        return
    elif args.version:
        print("picframe version: ", __version__)
        print("\nChecking required packages......")  # TODO update list of packages
        required_packages = ['PIL',
                             'pi3d',
                             'yaml',
                             'paho.mqtt',
                             'iptcinfo3',
                             'numpy',
                             'ninepatch',
                             'pi_heif',
                             'defusedxml',
                             'vlc']
        check_packages(required_packages)
        return

    # Initialize model and controller
    if args.configfile:
        m = model.Model(args.configfile)
    else:
        m = model.Model()

    v = viewer_display.ViewerDisplay(m.get_viewer_config())
    c = controller.Controller(m, v)
    
    await c.start()
    # Set up signal handlers AFTER everything is initialized
    controller_ref = [c]  # Use list for mutable reference
    loop = asyncio.get_running_loop()
    setup_signal_handlers(loop, controller_ref)
    
    try:
        while c.keep_looping:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Main loop Cancelled")
        raise
    except KeyboardInterrupt:
        logger.info("Main loop interrupted by KeyboardInterrupt")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}")
    finally:
        # Only call stop() if it hasn't been called already by the signal handler
        if not getattr(c, '_stop_called', False):
            maybe = c.stop()
            if asyncio.iscoroutine(maybe):
                await maybe
        else:
            logger.info("Controller already stopped by signal handler, skipping duplicate stop call")

async def main():
    """Simple main function that calls the app."""
    await run_picframe_app()

if __name__ == "__main__":
    print("Starting picframe from start.py...")
    asyncio.run(main())