import hashlib
import logging
import os
import re
import shutil
import unicodedata
from ast import literal_eval
from datetime import datetime, timedelta

import pytz
import yaml

from upload.onedrive_utils import ONEDRIVE_COMMANDS_FOLDER, RESPONSE_EXTENSION


logger = logging.getLogger("QueueRuntime")

TERMINAL_LOG_EXTENSION = ".yml"
DELETE_DEADLINE = timedelta(days=7)
CAMERAS_FOR_HARDDISK_SAVE = "EAST,SOUTH,WEST,NORTH,TOP"
JOB_COMMAND_SUMMARY_KEYS = [
    "starttime",
    "endtime",
    "cameras",
    "videoname",
    "concat",
    "trim",
    "youtube_upload",
    "official",
]
ALLOWED_WORKER_ARGUMENT_KEYS = {
    "server",
    "username",
    "password",
    "starttime",
    "endtime",
    "folders",
    "debug",
    "videoformat",
    "downloads",
    "frames",
    "force",
    "skipseconds",
    "seconds",
    "days",
    "skipdownload",
    "allrecordings",
    "cameras",
    "localtimefilenames",
    "yesterday",
    "ffmpeg",
    "forcetranscoding",
    "photos",
    "mock",
    "videoname",
    "concat",
    "trim",
    "ui",
}


class ResponseParseError(Exception):
    def __init__(
        self,
        response_path,
        response_hash,
        responder,
        original_exception,
        raw_response,
        recovered_fields=None,
    ):
        self.response_path = response_path
        self.response_hash = response_hash
        self.responder = responder
        self.original_exception = original_exception
        self.raw_response = raw_response
        self.recovered_fields = recovered_fields or {}
        super().__init__("Failed to parse response '{}'".format(response_path))


def clean_input_string(input_str: str) -> str:
    normalized = unicodedata.normalize("NFKC", input_str)
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
    safe_str = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_str)
    return safe_str.strip("_")


def is_dst(dt, timezone="Europe/Prague"):
    timezone = pytz.timezone(timezone)
    timezone_aware_date = timezone.localize(dt, is_dst=None)
    return timezone_aware_date.tzinfo._dst.seconds != 0


def parse_time(time_str):
    parts = time_str.split(":")
    parsed_time = [0, 0, 0]
    for index, value in enumerate(parts):
        parsed_time[index] = int(value)
    return "{:02d}:{:02d}:{:02d}".format(parsed_time[0], parsed_time[1], parsed_time[2])


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


def compute_response_hash(raw_bytes):
    return hashlib.sha256(raw_bytes).hexdigest()


def get_response_hash_from_file(filepath):
    with open(filepath, "rb") as fl:
        return compute_response_hash(fl.read())


def load_response_file(filepath):
    with open(filepath, "rb") as fl:
        raw_bytes = fl.read()

    response_hash = compute_response_hash(raw_bytes)
    try:
        raw_response = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raw_response = raw_bytes.decode("utf-8", errors="replace")
        recovered_fields = extract_raw_response_fields(raw_response)
        raise ResponseParseError(
            response_path=filepath,
            response_hash=response_hash,
            responder=recovered_fields.get("responder"),
            original_exception=exc,
            raw_response=raw_response,
            recovered_fields=recovered_fields,
        ) from exc

    return raw_response, response_hash


def parse_response_text(raw_response):
    command = {
        "concat": None,
        "trim": None,
    }

    for line in raw_response.splitlines():
        key, value = line.strip().split("?")
        key = key.lower()

        if key == "upload":
            key = "youtube_upload"

        if key.endswith("time"):
            date, time_value = value.split("T")
            time_value = parse_time(time_value)
            value = "{}T{}".format(date, time_value)

            dt = datetime.fromisoformat(value)
            if is_dst(dt):
                dt = dt - timedelta(hours=1)
                logger.debug("DST detected, changing time to %s", dt.isoformat())
            else:
                logger.debug("No DST detected, keeping time as %s", dt.isoformat())
            value = dt.isoformat()
        elif key == "cameras":
            value = parse_cameras(value)
        elif key == "videoname":
            value = clean_input_string(value)
        elif key == "official":
            value = value != "" and ("Ano" in parse_cameras(value))

        command[key] = value

    return command


def form_choice_to_bool(value):
    if value is None:
        return False
    return str(value).strip().lower() == "ano"


def parse_response_file(filepath):
    raw_response, response_hash = load_response_file(filepath)
    recovered_fields = extract_raw_response_fields(raw_response)

    try:
        command = parse_response_text(raw_response)
    except Exception as exc:
        raise ResponseParseError(
            response_path=filepath,
            response_hash=response_hash,
            responder=recovered_fields.get("responder"),
            original_exception=exc,
            raw_response=raw_response,
            recovered_fields=recovered_fields,
        ) from exc

    responder = command.get("responder") or recovered_fields.get("responder")
    videoname = command.get("videoname") or recovered_fields.get("videoname") or response_hash

    return {
        "response_hash": response_hash,
        "source_resp_name": os.path.basename(filepath),
        "source_resp_path": filepath,
        "raw_response": raw_response,
        "responder": responder,
        "videoname": videoname,
        "command": command,
        "youtube_upload": form_choice_to_bool(command.get("youtube_upload")),
        "harddisk_save": bool(command.get("official")),
        "recovered_fields": recovered_fields,
    }


def build_job_record_from_response(filepath, queued_at):
    parsed = parse_response_file(filepath)
    response_hash = parsed["response_hash"]

    return {
        "job_id": response_hash,
        "response_hash": response_hash,
        "source_resp_name": parsed["source_resp_name"],
        "queued_at": queued_at,
        "status": "queued",
        "responder": parsed["responder"],
        "videoname": parsed["videoname"],
        "command": parsed["command"],
        "youtube_upload": parsed["youtube_upload"],
        "harddisk_save": parsed["harddisk_save"],
        "raw_response": parsed["raw_response"],
        "notifications": {
            "registered_email_sent_at": None,
            "final_email_sent_at": None,
        },
    }


def build_parse_failed_terminal_record(error, finished_at):
    recovered_fields = error.recovered_fields or {}
    return {
        "job_id": error.response_hash,
        "response_hash": error.response_hash,
        "source_resp_name": os.path.basename(error.response_path),
        "status": "parse_failed",
        "responder": error.responder,
        "videoname": recovered_fields.get("videoname"),
        "command_summary": {
            key: value
            for key, value in recovered_fields.items()
            if key in JOB_COMMAND_SUMMARY_KEYS
        },
        "queued_at": None,
        "started_at": None,
        "finished_at": finished_at,
        "result_message": "Command parsing failed",
        "error_summary": str(error.original_exception),
    }


def build_terminal_record_from_job(
    job,
    status,
    finished_at,
    result_message=None,
    error_summary=None,
    worker_summary=None,
):
    return {
        "job_id": job["job_id"],
        "response_hash": job["response_hash"],
        "source_resp_name": job.get("source_resp_name"),
        "status": status,
        "responder": job.get("responder"),
        "videoname": job.get("videoname"),
        "command_summary": {
            key: value
            for key, value in (job.get("command") or {}).items()
            if key in JOB_COMMAND_SUMMARY_KEYS
        },
        "queued_at": job.get("queued_at"),
        "started_at": job.get("started_at"),
        "finished_at": finished_at,
        "result_message": result_message,
        "error_summary": error_summary,
        "worker": worker_summary,
    }


def terminal_log_path(response_hash, commands_folder=ONEDRIVE_COMMANDS_FOLDER):
    return os.path.join(commands_folder, "{}{}".format(response_hash, TERMINAL_LOG_EXTENSION))


def terminal_log_exists(response_hash, commands_folder=ONEDRIVE_COMMANDS_FOLDER):
    return os.path.exists(terminal_log_path(response_hash, commands_folder=commands_folder))


def write_terminal_log(record, commands_folder=ONEDRIVE_COMMANDS_FOLDER):
    os.makedirs(commands_folder, exist_ok=True)
    path = terminal_log_path(record["response_hash"], commands_folder=commands_folder)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as fl:
        yaml.safe_dump(record, fl, indent=2, sort_keys=False, allow_unicode=True)
    os.replace(temp_path, path)
    return path


def promote_temp_terminal_log(temp_path, response_hash, commands_folder=ONEDRIVE_COMMANDS_FOLDER):
    os.makedirs(commands_folder, exist_ok=True)
    path = terminal_log_path(response_hash, commands_folder=commands_folder)
    shutil.copyfile(temp_path, path)
    try:
        os.remove(temp_path)
    except OSError:
        logger.warning("Failed to delete temporary terminal log '%s' after promotion", temp_path)
    return path


def cleanup_old_terminal_logs(commands_folder=ONEDRIVE_COMMANDS_FOLDER, deadline=DELETE_DEADLINE):
    if not os.path.isdir(commands_folder):
        return

    now = datetime.now()
    for filename in os.listdir(commands_folder):
        path = os.path.join(commands_folder, filename)
        if not os.path.isfile(path) or not path.endswith(TERMINAL_LOG_EXTENSION):
            continue
        mod_time = datetime.fromtimestamp(os.path.getmtime(path))
        if mod_time < (now - deadline):
            os.remove(path)


def list_response_files(commands_folder=ONEDRIVE_COMMANDS_FOLDER):
    if not os.path.isdir(commands_folder):
        return []

    responses = []
    for filename in os.listdir(commands_folder):
        path = os.path.join(commands_folder, filename)
        if os.path.isfile(path) and path.endswith(RESPONSE_EXTENSION):
            responses.append(path)

    responses.sort(key=lambda path: (os.path.getmtime(path), os.path.basename(path)))
    return responses


def job_to_argv(job):
    argv = []
    command = dict(job.get("command") or {})
    harddisk_save = bool(job.get("harddisk_save"))

    for key, value in command.items():
        normalized_key = key.lower()
        if normalized_key not in ALLOWED_WORKER_ARGUMENT_KEYS:
            logger.debug(
                "Skipping non-CLI job field '%s' while building argv for job '%s'",
                normalized_key,
                job.get("job_id"),
            )
            continue
        if normalized_key in ("youtube_upload", "official"):
            continue
        if normalized_key == "cameras" and harddisk_save:
            value = CAMERAS_FOR_HARDDISK_SAVE

        if value is None:
            argv.append("--{}".format(normalized_key))
        elif value != "":
            argv.extend(["--{}".format(normalized_key), str(value)])

    return argv
