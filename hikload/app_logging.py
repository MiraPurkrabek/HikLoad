import copy
import logging.config
import os

import yaml


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGGING_CONFIG_PATH = os.path.join(PROJECT_ROOT, "logging_config.yml")
LOGS_ROOT = os.path.join(PROJECT_ROOT, "logs")

ROLE_LOGGER_NAMES = [
    "HikLoadSupervisor",
    "HikLoadWorker",
    "QueueRuntime",
    "OnedriveUtils",
    "EmailSender",
    "VideoHandler",
    "YoutubeUpload",
    "hikload",
]

ROLE_FILE_NAMES = {
    "supervisor": {
        "latest": os.path.join("logs", "supervisor", "latest.log"),
        "rotating": os.path.join("logs", "supervisor", "HikLoadSupervisor.log"),
    },
    "worker": {
        "latest": os.path.join("logs", "worker", "latest.log"),
        "rotating": os.path.join("logs", "worker", "HikLoadWorker.log"),
    },
}


def ensure_log_dirs():
    os.makedirs(os.path.join(LOGS_ROOT, "supervisor"), exist_ok=True)
    os.makedirs(os.path.join(LOGS_ROOT, "worker"), exist_ok=True)


def get_latest_log_path(role: str) -> str:
    role = role.lower()
    return os.path.join(PROJECT_ROOT, ROLE_FILE_NAMES[role]["latest"])


def configure_logging(role: str):
    role = role.lower()
    ensure_log_dirs()

    with open(LOGGING_CONFIG_PATH, "r", encoding="utf-8") as fl:
        config = yaml.safe_load(fl)

    config = copy.deepcopy(config)

    latest_handler_name = f"{role}_logfile"
    rotating_handler_name = f"{role}_rotatingfile"
    role_handlers = [rotating_handler_name, latest_handler_name]

    for logger_name in ROLE_LOGGER_NAMES:
        if logger_name in config.get("loggers", {}):
            config["loggers"][logger_name]["handlers"] = role_handlers

    logging.config.dictConfig(config)
