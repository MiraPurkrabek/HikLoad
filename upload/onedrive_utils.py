import sys
import os
import shutil
from ast import literal_eval
import yaml
from datetime import timedelta, datetime
import pytz
import logging

# Has to be hardcoded because of non-unicode chars
ONEDRIVE_COMMANDS_FOLDER="D:\\OneDrive - TJ Sokol Královské Vinohrady\\HikLoad_commands"
ONEDRIVE_UPLOADS_FOLDER="D:\\OneDrive - TJ Sokol Královské Vinohrady\\HikLoad_uploads"

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
}

logger = logging.getLogger("OnedriveUtils")


def is_dst(dt, timezone="Europe/Prague"):
    timezone = pytz.timezone(timezone)
    timezone_aware_date = timezone.localize(dt, is_dst=None)
    return timezone_aware_date.tzinfo._dst.seconds != 0


def argname_from_response(response_name, response_extension=RESPONSE_EXTENSION, arguments_extension=ARGUMENTS_EXTENSION):
    dirname, basename = os.path.split(response_name)
    basename = basename.replace(" ", "_").replace(response_extension, arguments_extension)
    return os.path.join(dirname, basename)


def parse_time(time_str):
    splitted = time_str.split(":")
    time = [0, 0, 0]
    for i, str_s in enumerate(splitted):
        time[i] = int(str_s)
    return "{:02d}:{:02d}:{:02d}".format(time[0], time[1], time[2])


def parse_cameras(cameras_arr):
    return ",".join(literal_eval(cameras_arr))


def parse_onedrive_response(
    filepath,
):
    with open(filepath, "r") as fl_in:
        args_dict = {
            # "skipdownload": None,
            "concat": None,
            "trim": None,
        }
        for line in fl_in.readlines():
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
                value = value.replace(" ", "_")

            args_dict[key] = value
        
        out_filepath = argname_from_response(filepath)
        with open(out_filepath, "w") as fl_out:
            yaml.safe_dump(args_dict, fl_out, indent=2)
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
        latest = parse_onedrive_response(response)

        if remove_processed:
            os.remove(response)
        
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


def argfile_to_argstr(filepath, arguments_extension=ARGUMENTS_EXTENSION):
    logger.debug("Translating argfile to argstr")
    if filepath is None:
        return None, False
    assert filepath.endswith(arguments_extension)

    out_str = ""
    youtube_upload = False
    with open(filepath, "r") as fl:
        args_dict = yaml.safe_load(fl)
        for key, value in args_dict.items():
            key = key.lower()
            if key in PARSED_KEYWORDS:
                if key == "cameras":
                    out_str += "--{}={} ".format(key, value)
                elif key == "youtube_upload":
                   youtube_upload = value.lower() == "Ano".lower()
                else:
                    out_str += "--{} {} ".format(key, value)
            elif value is None:
                out_str += "--{} ".format(key)

    return out_str, youtube_upload


def cleanup_old_files(
    folder=ONEDRIVE_COMMANDS_FOLDER,
    deadline=DELETE_DEADLINE
):
    logger.debug("Cleaning up old files in folder '{:s}' with deadline {}".format(folder, deadline))
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


if __name__ == "__main__":
    latest = parse_responses_and_return_latest()
    print(argfile_to_argstr(latest))
    