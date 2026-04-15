import sys
import os
import unicodedata
import re
import shutil
from ast import literal_eval
import yaml
from datetime import timedelta, datetime
import pytz
import logging

# Has to be hardcoded because of non-unicode chars
ONEDRIVE_COMMANDS_FOLDER="C:\\Users\\SKV Server\\OneDrive - TJ Sokol Královské Vinohrady\\HikLoad_commands"
ONEDRIVE_UPLOADS_FOLDER="C:\\Users\\SKV Server\\OneDrive - TJ Sokol Královské Vinohrady\\HikLoad_uploads"
HARDDISK_PATH="D:\\Floorball_SKV_data\\automatic_save"

RESPONSE_EXTENSION=".resp"
ARGUMENTS_EXTENSION=".yml"
PARSED_KEYWORDS = [
    "starttime",
    "endtime", 
    "cameras",
    "videoname",
    "youtube_upload",
]
# PROCESS_DEADLINE=timedelta(seconds=100)
DELETE_DEADLINE=timedelta(days=7)

CAMERA_TRANSLATION = {
    "EAST": "101",
    "SOUTH": "201",
    "WEST": "301",
    "NORTH": "401",
    "TOP": "501",
    "GYM": "601",
}

logger = logging.getLogger("OnedriveUtils")


class ResponseParseError(Exception):
    def __init__(self, response_path, processed_path, responder, original_exception, recovered_fields=None):
        self.response_path = response_path
        self.processed_path = processed_path
        self.responder = responder
        self.original_exception = original_exception
        self.recovered_fields = recovered_fields or {}
        super().__init__("Failed to parse response '{}'".format(response_path))


def clean_input_string(input_str: str) -> str:
    # Normalize text to ASCII (č -> c)
    normalized = unicodedata.normalize('NFKC', input_str)
    ascii_str = normalized.encode('ascii', 'ignore').decode('ascii')

    # Replace any unsafe chars with underscore
    safe_str = re.sub(r'[^A-Za-z0-9_-]+', '_', ascii_str)
    
    # Remove trailing ans leading "_"
    safe_str = safe_str.strip("_")
    
    return safe_str

def is_dst(dt, timezone="Europe/Prague"):
    timezone = pytz.timezone(timezone)
    timezone_aware_date = timezone.localize(dt, is_dst=None)
    return timezone_aware_date.tzinfo._dst.seconds != 0


def argname_from_response(response_name, response_extension=RESPONSE_EXTENSION, arguments_extension=ARGUMENTS_EXTENSION):
    dirname, basename = os.path.split(response_name)
    basename = basename.replace(" ", "_").replace("/", "_").replace("\\", "_").replace(response_extension, arguments_extension)
    return os.path.join(dirname, basename)


def parse_time(time_str):
    splitted = time_str.split(":")
    time = [0, 0, 0]
    for i, str_s in enumerate(splitted):
        time[i] = int(str_s)
    return "{:02d}:{:02d}:{:02d}".format(time[0], time[1], time[2])


def parse_cameras(cameras_arr):
    return ",".join(literal_eval(cameras_arr))


def extract_raw_response_fields(raw_response: str):
    recovered_fields = {}
    for line in raw_response.splitlines():
        if "?" not in line:
            continue
        key, value = line.strip().split("?", 1)
        if key == "":
            continue
        recovered_fields[key.lower()] = value
    return recovered_fields


def parse_onedrive_response(
    filepath,
):
    out_filepath = argname_from_response(filepath)
    with open(filepath, "r", encoding='utf-8') as fl_in:
        raw_response = fl_in.read()

    recovered_fields = extract_raw_response_fields(raw_response)

    try:
        args_dict = {
            # "skipdownload": None,
            "concat": None,
            "trim": None,
        }
        for line in raw_response.splitlines():
            key, value = line.strip().split("?")
            key = key.lower()

            if key.endswith("time"):
                # Parse for missing zeros
                date, time = value.split("T")
                time = parse_time(time)
                value = "{}T{}".format(date, time)
                
                # If DST (daylight savings time), recompute
                dt = datetime.fromisoformat(value)
                if is_dst(dt):
                    dt = dt - timedelta(hours=1)
                    logger.debug("DST detected, changing time to {:s}".format(dt.isoformat()))
                else:
                    logger.debug("No DST detected, keeping time as {:s}".format(dt.isoformat()))
                value = dt.isoformat()

            elif key == "cameras":
                value = parse_cameras(value)
            elif key == "upload" and value != "":
                value = literal_eval(value)
            elif key == "videoname":
                value = clean_input_string(value)
            elif key == "official":
                value = value != '' and ("Ano" in parse_cameras(value))

            args_dict[key] = value

        with open(out_filepath, "w", encoding='utf-8') as fl_out:
            yaml.safe_dump(args_dict, fl_out, indent=2)
    except Exception as e:
        failure_dict = {
            "parse_failed": True,
            "parse_error": str(e),
            "raw_response": raw_response,
        }
        for key, value in recovered_fields.items():
            if key not in failure_dict:
                failure_dict[key] = value

        with open(out_filepath, "w", encoding='utf-8') as fl_out:
            yaml.safe_dump(failure_dict, fl_out, indent=2)

        raise ResponseParseError(
            response_path=filepath,
            processed_path=out_filepath,
            responder=recovered_fields.get("responder"),
            original_exception=e,
            recovered_fields=recovered_fields,
        ) from e

    return out_filepath


def parse_responses_and_return_latest(
    ONEDRIVE_COMMANDS_FOLDER=ONEDRIVE_COMMANDS_FOLDER,
    extension=RESPONSE_EXTENSION,
    remove_processed=True,
):
    logger.debug("Parsing responses")
    
    # Get unparsed responses files
    responses = []
    for f in os.listdir(ONEDRIVE_COMMANDS_FOLDER):
        f_path = os.path.join(ONEDRIVE_COMMANDS_FOLDER, f)
        if os.path.isfile(f_path) and f_path.endswith(extension):
            responses.append(f_path)
    
    responses.sort(key=lambda x: os.path.getmtime(x))

    latest = None
    for response in responses:
        parse_exception = None
        remove_exception = None
        try:
            latest = parse_onedrive_response(response)
        except Exception as e:
            parse_exception = e
        finally:
            if remove_processed and os.path.exists(response):
                try:
                    os.remove(response)
                except Exception as e:
                    remove_exception = e
        
        if parse_exception is not None:
            if remove_exception is not None:
                logger.error("Failed to remove processed response '%s': %s", response, remove_exception)
            raise parse_exception
        if remove_exception is not None:
            raise remove_exception

        # Only parse one response at a time
        break
        
    # latest = get_latest_arguments(ONEDRIVE_COMMANDS_FOLDER)
    if latest is None:
        return None

    # deadline = datetime.now() - PROCESS_DEADLINE
    # mod_time = datetime.fromtimestamp(os.path.getmtime(latest))
    # logger.info("Latest mod_time: {}".format(mod_time))
    # logger.info("Deadline: {}".format(deadline))

    # if mod_time < deadline:
    #     return None
    # else:
    return latest


def _get_latest_arguments(
    folder=ONEDRIVE_COMMANDS_FOLDER,
    arguments_extension=ARGUMENTS_EXTENSION
):
    # Sort by modification date (newest first)
    files = []
    for f in os.listdir(folder):
        f_path = os.path.join(folder, f)
        if os.path.isfile(f_path) and f_path.endswith(arguments_extension):
            files.append(f_path)
    files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    if len(files) == 0:
        return None
    else:
        return files[0]


def argfile_to_argdict(filepath, arguments_extension=ARGUMENTS_EXTENSION):
    logger.debug("Translating argfile to argdict")
    if filepath is None:
        return None, False
    assert filepath.endswith(arguments_extension)

    args_dict = {}
    with open(filepath, "r") as fl:
        args_dict = yaml.safe_load(fl)
        
    return args_dict
        
def argfile_to_argstr(filepath, arguments_extension=ARGUMENTS_EXTENSION):
    logger.debug("Translating argfile to argstr")
    if filepath is None:
        return None, False, False
    assert filepath.endswith(arguments_extension)

    out_str = ""
    youtube_upload = False
    harddisk_save = False
    with open(filepath, "r") as fl:
        args_dict = yaml.safe_load(fl)
        
        # Need to know if harddisk save before processing cameras
        if "official" in args_dict.keys():
            harddisk_save = args_dict["official"]
        
        for key, value in args_dict.items():
            key = key.lower()
            if key in PARSED_KEYWORDS:
                if key == "cameras":
                    
                    if harddisk_save:
                        # Save all cameras
                        value = "EAST,SOUTH,WEST,NORTH,TOP"
                        # value = parse_cameras(CAMERA_TRANSLATION.keys())
                    
                    out_str += "--{}={} ".format(key, value)
                elif key == "youtube_upload":
                   youtube_upload = value.lower() == "Ano".lower()
                else:
                    out_str += "--{} {} ".format(key, value)
            elif value is None:
                out_str += "--{} ".format(key)

    return out_str, youtube_upload, harddisk_save


def cleanup_old_files(
    folder=ONEDRIVE_COMMANDS_FOLDER,
    deadline=DELETE_DEADLINE
):
    logger.debug("Cleaning up old files in folder '{:s}' with deadline {}".format(folder, deadline))
    if not os.path.isdir(folder):
        return
    for f in os.listdir(folder):
        f_path = os.path.join(folder, f)
        if os.path.isfile(f_path):
            mod_time = datetime.fromtimestamp(os.path.getmtime(f_path))
            if mod_time < (datetime.now() - deadline):
                os.remove(f_path) 


def upload_to_onedrive(file_path):
    logger.debug("Uploading '{:s}' to OneDrive".format(file_path))
    cleanup_old_files(folder=ONEDRIVE_UPLOADS_FOLDER)
    _, new_name = os.path.split(file_path)
    
    # Translate channel IDs to camera names
    for camera_name, cid in CAMERA_TRANSLATION.items():
        new_name = new_name.replace("_"+cid, "_"+camera_name[0])

    # Copy to OneDrive folder
    dst = os.path.join(
        ONEDRIVE_UPLOADS_FOLDER, new_name
    )
    shutil.move(file_path, dst)
    

def copy_file_to_harddisk(file_path):
    logger.debug("Copying '{:s}' to external hard disk".format(file_path))
    today_date = datetime.today().strftime('%Y-%m-%d')
    new_folder = os.path.join(HARDDISK_PATH, today_date)
    os.makedirs(new_folder, exist_ok=True)
    
    new_name = os.path.basename(file_path)
    # Translate channel IDs to camera names
    for camera_name, cid in CAMERA_TRANSLATION.items():
        new_name = new_name.replace("_"+cid, "_"+camera_name[0])
    dst_path = os.path.join(new_folder, new_name)
    
    shutil.copyfile(file_path, dst_path)    


if __name__ == "__main__":
    latest = parse_responses_and_return_latest()
    print(argfile_to_argstr(latest))
    
