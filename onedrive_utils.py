import sys
import os
from ast import literal_eval
import yaml
from datetime import timedelta, datetime
import logging

# Has to be hardcoded because of non-unicode chars
ONEDRIVE_FOLDER="D:\\OneDrive - TJ Sokol Královské Vinohrady\\HikLoad_commands"

RESPONSE_EXTENSION=".resp"
ARGUMENTS_EXTENSION=".yml"
PARSED_KEYWORDS = [
    "starttime",
    "endtime", 
    "cameras",
    "videoname",
]
# PROCESS_DEADLINE=timedelta(seconds=100)
DELETE_DEADLINE=timedelta(days=7)

logger = logging.getLogger("HikLoadHandler")

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
    response_extension=RESPONSE_EXTENSION,
    arguments_extension=ARGUMENTS_EXTENSION,
):
    with open(filepath, "r") as fl_in:
        logger.info("Opened: {}".format(filepath))
        args_dict = {
            "skipdownload": None,
            # "concat": None,
            # "trim": None,
        }
        for line in fl_in.readlines():
            key, value = line.strip().split("?")
            key = key.lower()

            if key.endswith("time"):
                date, time = value.split("T")
                time = parse_time(time)
                value = "{}T{}".format(date, time)
            elif key == "cameras":
                value = parse_cameras(value)
            elif key == "upload" and value != "":
                value = literal_eval(value)

            args_dict[key] = value
        
        out_filepath = argname_from_response(filepath)
        with open(out_filepath, "w") as fl_out:
            yaml.safe_dump(args_dict, fl_out, indent=2)
    logger.info("Parsed: {}".format(filepath))
    return out_filepath


def parse_responses_and_return_latest(
    onedrive_folder=ONEDRIVE_FOLDER,
    extension=RESPONSE_EXTENSION,
):
    # Get unparsed responses files
    responses = []
    for f in os.listdir(onedrive_folder):
        f_path = os.path.join(onedrive_folder, f)
        if os.path.isfile(f_path) and f_path.endswith(extension):
            responses.append(f_path)
    
    responses.sort(key=lambda x: os.path.getmtime(x))

    latest = None
    for response in responses:
        latest = parse_onedrive_response(response)
        os.remove(response)
        
        # Only parse one response at a time
        break
        
    # latest = get_latest_arguments(onedrive_folder)
    if latest is None:
        return None

    logger.info("Latest file: {}".format(latest))

    # deadline = datetime.now() - PROCESS_DEADLINE
    # mod_time = datetime.fromtimestamp(os.path.getmtime(latest))
    # logger.info("Latest mod_time: {}".format(mod_time))
    # logger.info("Deadline: {}".format(deadline))

    # if mod_time < deadline:
    #     return None
    # else:
    return latest


def _get_latest_arguments(
    folder=ONEDRIVE_FOLDER,
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
    if filepath is None:
        return None
    assert filepath.endswith(arguments_extension)

    out_str = ""
    with open(filepath, "r") as fl:
        args_dict = yaml.safe_load(fl)
        for key, value in args_dict.items():
            key = key.lower()
            if key in PARSED_KEYWORDS:
                if key == "cameras":
                    out_str += "--{}={} ".format(key, value)
                else:
                    out_str += "--{} {} ".format(key, value)
            elif value is None:
                out_str += "--{} ".format(key)

    return out_str


def cleanup_old_files(
    folder=ONEDRIVE_FOLDER,
    deadline=DELETE_DEADLINE
):
    for f in os.listdir(folder):
        f_path = os.path.join(folder, f)
        if os.path.isfile(f_path):
            mod_time = datetime.fromtimestamp(os.path.getmtime(f_path))
            if mod_time < (datetime.now() - deadline):
                os.remove(f_path) 


if __name__ == "__main__":
    latest = parse_responses_and_return_latest()
    print(argfile_to_argstr(latest))
    