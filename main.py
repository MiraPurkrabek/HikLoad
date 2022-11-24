import logging
import logging.config
import signal
import sys
import os
import yaml

from hikload.download import run, parse_args
from hikload.__main__ import main_ui
from upload.onedrive_utils import parse_responses_and_return_latest, argfile_to_argstr, cleanup_old_files, upload_to_onedrive, CAMERA_TRANSLATION


config_path = "logging_config.yml"
with open(config_path, "r") as f:
    config_data = yaml.safe_load(f.read())
    logging.config.dictConfig(config_data)
logger = logging.getLogger("HikLoadHandler")

# Macros
ROOT = os.path.dirname(os.path.abspath(__file__))


def main(): 
    logger.debug("\n")
    logger.debug("Starting the main script")
    
    DIR_PATH = os.path.dirname(os.path.realpath(__file__))
    os.chdir(DIR_PATH)

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    
    cleanup_old_files()
    cleanup_old_files(folder="Downloads")
    logger.debug("Old files cleaned up")
    
    if len(sys.argv) < 2 :
        arg_str = argfile_to_argstr(parse_responses_and_return_latest())
        if arg_str is None:
            logger.info("No new file to process")
            return
        else:
            sys.argv.extend(arg_str.split())
    args = parse_args()

    logger.debug("Args parsed")
    # Load default password from the file
    try:
        with open(os.path.join("passwords", "passwords.yml"), "r") as pass_file:
            default_passwords = yaml.safe_load(pass_file)
    except FileNotFoundError:
        default_passwords = None
    logger.debug("Passwords loaded")
    
    if args.server == "" or args.server is None:
        assert default_passwords is not None, "No server specified and couln't load the passwords.yml file"
        args.server = default_passwords["HikServer"]["address"]
    if args.username == ""or args.username is None:
        assert default_passwords is not None, "No username specified and couln't load the passwords.yml file"
        args.username = default_passwords["HikServer"]["user"]
    if args.password == "" or args.password is None:
        assert default_passwords is not None, "No password specified and couln't load the passwords.yml file"
        args.password = default_passwords["HikServer"]["password"]
    
    logger.debug("Passwords (and server) checked")

    # Translate cameras
    if args.cameras is not None:
        for i, camera in enumerate(args.cameras):
            if camera.upper() in CAMERA_TRANSLATION.keys():
                args.cameras[i] = CAMERA_TRANSLATION[camera.upper()]

    logger.info("Running the downloading session")
    logger.info(args)

    try:
        output_filenames = run(args)
    except KeyboardInterrupt:
        logging.info("Exited by user")
        sys.exit(0)

    for filename in output_filenames:
        upload_to_onedrive(filename)

if __name__ == "__main__":
    main()
