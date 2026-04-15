import argparse
import logging
import os
import signal
import sys
import uuid
from datetime import datetime, timezone

import yaml

from hikload.app_logging import configure_logging
from hikload.command_queue import (
    ResponseParseError,
    build_job_record_from_response,
    build_parse_failed_terminal_record,
    build_terminal_record_from_job,
    cleanup_old_terminal_logs,
    get_response_hash_from_file,
    job_to_argv,
    list_response_files,
    promote_temp_terminal_log,
    terminal_log_exists,
)
from hikload.download import parse_args, run
from hikload.eta_estimator import (
    DEFAULT_RUNTIME_PER_10MIN,
    ETA_SAMPLE_WINDOW,
    build_eta_range_minutes,
    estimate_job_minutes,
    estimate_jobs_minutes,
    load_eta_stats,
    summarize_camera_coefficients,
    update_eta_stats_from_successful_job,
)
from hikload.runtime_queue import (
    WORKER_MAX_RUNTIME_SECONDS,
    acquire_supervisor_lock,
    active_job_exists,
    claim_next_queued_job,
    claim_queued_job,
    clear_worker_state,
    count_jobs,
    create_temp_yaml,
    discard_temp_file,
    ensure_state_dirs,
    list_jobs,
    promote_temp_job_to_queue,
    read_worker_state,
    release_supervisor_lock,
    remove_job,
    touch_worker_state,
    utcnow,
    utcnow_iso,
    update_job,
    write_runtime_summary,
    write_worker_state,
)
from hikload.task_scheduler import (
    WORKER_TASK_NAME,
    get_task_info,
    is_task_active,
    trigger_task,
)
from hikload.send_email import (
    send_failure_email,
    send_no_recordings_email,
    send_parse_failure_email,
    send_registered_email,
    send_report_email,
    send_success_email,
)
from upload.onedrive_utils import (
    CAMERA_TRANSLATION,
    ONEDRIVE_COMMANDS_FOLDER,
    ONEDRIVE_UPLOADS_FOLDER,
    cleanup_old_files,
    copy_file_to_harddisk,
    upload_to_onedrive,
)


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSWORDS_PATH = os.path.join(PROJECT_ROOT, "passwords", "passwords.yml")
SUPERVISOR_INTERVAL_SECONDS = 60
WORKER_TIMEOUT_THRESHOLD_SECONDS = WORKER_MAX_RUNTIME_SECONDS - SUPERVISOR_INTERVAL_SECONDS


def parse_entrypoint_args(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--role", choices=["supervisor", "worker"], default="supervisor")
    parser.add_argument("--job-id")
    parser.add_argument("--worker-instance-id")
    parsed, extras = parser.parse_known_args(argv)

    if parsed.role == "worker":
        if extras:
            parser.error("Unexpected extra arguments for worker mode: {}".format(" ".join(extras)))
    elif extras:
        parser.error("This entrypoint now runs only in supervisor mode by default.")

    return parsed


def setup_role(role):
    os.chdir(PROJECT_ROOT)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    ensure_state_dirs()
    configure_logging(role)
    logger_name = "HikLoadSupervisor" if role == "supervisor" else "HikLoadWorker"
    return logging.getLogger(logger_name)


def safe_cleanup_folder(logger, folder):
    if not os.path.isdir(folder):
        logger.debug("Skipping cleanup for missing folder '%s'", folder)
        return
    cleanup_old_files(folder=folder)


def load_default_passwords():
    try:
        with open(PASSWORDS_PATH, "r", encoding="utf-8") as pass_file:
            return yaml.safe_load(pass_file)
    except FileNotFoundError:
        return None


def apply_default_connection_args(args):
    default_passwords = load_default_passwords()
    if args.server in ("", None):
        assert default_passwords is not None, "No server specified and couldn't load passwords.yml"
        args.server = default_passwords["HikServer"]["address"]
    if args.username in ("", None):
        assert default_passwords is not None, "No username specified and couldn't load passwords.yml"
        args.username = default_passwords["HikServer"]["user"]
    if args.password in ("", None):
        assert default_passwords is not None, "No password specified and couldn't load passwords.yml"
        args.password = default_passwords["HikServer"]["password"]


def translate_cameras(args):
    if args.cameras is None:
        return
    for index, camera in enumerate(args.cameras):
        if camera.upper() in CAMERA_TRANSLATION:
            args.cameras[index] = CAMERA_TRANSLATION[camera.upper()]


def normalize_output_paths(output_filenames, downloads_folder):
    paths = []
    for filename in output_filenames:
        if os.path.isabs(filename):
            paths.append(filename)
        else:
            paths.append(os.path.join(downloads_folder, filename))
    return paths


def estimate_registration_eta(logger, job, worker_task_active):
    eta_stats = load_eta_stats()
    queued_jobs = list_jobs("queued")
    running_jobs = list_jobs("running") if worker_task_active else []

    queued_estimate = estimate_jobs_minutes(queued_jobs + running_jobs, eta_stats)
    job_estimate = estimate_job_minutes(job, eta_stats)
    total_minutes = queued_estimate["total_minutes"] + job_estimate["total_minutes"] 
    eta_range = build_eta_range_minutes(total_minutes)

    logger.info(
        "ETA estimate for new job '%s': ahead=%.2f min, current_job=%.2f min, total=%.2f min, range=%s-%s min",
        job.get("job_id"),
        queued_estimate["total_minutes"],
        job_estimate["total_minutes"],
        total_minutes,
        eta_range[0],
        eta_range[1],
    )
    logger.info(
        "ETA job details for '%s': duration=%.2f min, effective_cameras=%s",
        job.get("job_id"),
        job_estimate["duration_minutes"],
        job_estimate["cameras"],
    )
    if queued_estimate["default_cameras"] or job_estimate["default_cameras"]:
        logger.info(
            "ETA used default runtime coefficient %.2f min/10min for cameras: ahead=%s, current_job=%s",
            DEFAULT_RUNTIME_PER_10MIN,
            queued_estimate["default_cameras"],
            job_estimate["default_cameras"],
        )

    return {
        "range_minutes": eta_range,
        "queued_estimate": queued_estimate,
        "job_estimate": job_estimate,
        "total_minutes": total_minutes,
        "stats": eta_stats,
    }


def build_worker_summary(worker_state):
    if worker_state is None:
        return None
    return {
        "worker_instance_id": worker_state.get("worker_instance_id"),
        "started_at": worker_state.get("started_at"),
        "heartbeat_at": worker_state.get("heartbeat_at"),
        "status": worker_state.get("status"),
        "current_job_id": worker_state.get("current_job_id"),
        "trigger_source": worker_state.get("trigger_source"),
    }


def consume_response_file(response_path):
    os.remove(response_path)


def finalize_parse_failure(logger, parse_error):
    finished_at = utcnow_iso()
    terminal_record = build_parse_failed_terminal_record(parse_error, finished_at=finished_at)
    temp_path = create_temp_yaml(terminal_record, prefix="{}.parse_failed".format(parse_error.response_hash))
    try:
        consume_response_file(parse_error.response_path)
    except Exception:
        discard_temp_file(temp_path)
        logger.exception("Failed to delete malformed response '%s'; leaving it for a later retry", parse_error.response_path)
        return False

    promote_temp_terminal_log(
        temp_path,
        parse_error.response_hash,
        commands_folder=ONEDRIVE_COMMANDS_FOLDER,
    )
    if parse_error.responder:
        send_parse_failure_email(to=parse_error.responder)
    return True


def register_response(logger, response_path, worker_task_active):
    queued_at = utcnow_iso()
    try:
        job = build_job_record_from_response(response_path, queued_at=queued_at)
    except ResponseParseError as parse_error:
        logger.warning("Response '%s' could not be parsed: %s", response_path, parse_error.original_exception)
        if finalize_parse_failure(logger, parse_error):
            return "parse_failed"
        return "pending"

    temp_path = create_temp_yaml(job, prefix="{}.queued".format(job["job_id"]))
    try:
        consume_response_file(response_path)
    except Exception:
        discard_temp_file(temp_path)
        logger.exception("Failed to delete response '%s'; rolling back queue registration", response_path)
        return "pending"

    eta_context = estimate_registration_eta(logger, job, worker_task_active=worker_task_active)
    eta_range = eta_context["range_minutes"]
    promote_temp_job_to_queue(temp_path, job["job_id"])
    if job.get("responder"):
        send_registered_email(
            to=job["responder"],
            video_name=job.get("videoname"),
            eta_range_minutes=eta_range,
            command=job.get("command"),
            harddisk_save=job.get("harddisk_save", False),
        )

    job["notifications"]["registered_email_sent_at"] = utcnow_iso()
    update_job(job)
    logger.info("Registered job '%s' from response '%s'", job["job_id"], response_path)
    return "registered"


def parse_runtime_timestamp(value):
    if not value:
        return None

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_failure_email_body(job, terminal_status):
    video_name = job.get("videoname") or "unknown video"
    if terminal_status == "worker_timeout":
        return (
            "Processing of your video '{:s}' exceeded the maximum allowed time and was stopped. "
            "Please try again. If the problem persists, contact support."
        ).format(video_name)

    return (
        "Processing of your video '{:s}' stopped unexpectedly before completion. "
        "Please try again. If the problem persists, contact support."
    ).format(video_name)


def classify_stale_running_job(job):
    started_at = parse_runtime_timestamp(job.get("started_at"))
    if started_at is None:
        return (
            "worker_crash_or_missing",
            "Worker stopped unexpectedly before finishing the job",
            "Worker task stopped without a valid started_at timestamp",
        )

    elapsed_seconds = (utcnow() - started_at).total_seconds()
    if elapsed_seconds >= WORKER_TIMEOUT_THRESHOLD_SECONDS:
        return (
            "worker_timeout",
            "Worker task exceeded the configured Task Scheduler execution time limit",
            "Worker task did not finish within the configured 4 hour Task Scheduler limit",
        )

    return (
        "worker_crash_or_missing",
        "Worker stopped unexpectedly before finishing the job",
        "Worker task stopped before the configured timeout was reached",
    )


def build_task_summary(task_info):
    return {
        "task_name": task_info.get("TaskName"),
        "task_path": task_info.get("TaskPath"),
        "exists": task_info.get("Exists"),
        "state": task_info.get("State"),
        "last_task_result": task_info.get("LastTaskResult"),
        "last_run_time": task_info.get("LastRunTime"),
        "next_run_time": task_info.get("NextRunTime"),
    }


def query_worker_task_info(logger):
    task_info = get_task_info(WORKER_TASK_NAME)
    if not task_info.get("Exists"):
        logger.error("Worker task '%s' is missing or misconfigured", WORKER_TASK_NAME)
        return task_info

    if is_task_active(task_info):
        logger.info(
            "Worker task '%s' queried as %s (last result: %s)",
            WORKER_TASK_NAME,
            task_info.get("State"),
            task_info.get("LastTaskResult"),
        )
    else:
        logger.info(
            "Worker task '%s' queried as NotRunning (state=%s, last result=%s)",
            WORKER_TASK_NAME,
            task_info.get("State"),
            task_info.get("LastTaskResult"),
        )
    return task_info


def reconcile_stale_running_job(logger, job, worker_state):
    terminal_status, result_message, error_summary = classify_stale_running_job(job)
    logger.warning(
        "Stale running job '%s' is being reconciled as %s",
        job.get("job_id"),
        terminal_status,
    )
    terminal_record = build_terminal_record_from_job(
        job,
        status=terminal_status,
        finished_at=utcnow_iso(),
        result_message=result_message,
        error_summary=error_summary,
        worker_summary=build_worker_summary(worker_state),
    )
    write_terminal_log_from_record(terminal_record)

    if job.get("responder"):
        send_failure_email(
            to=job["responder"],
            video_name=job.get("videoname"),
            body=build_failure_email_body(job, terminal_status),
        )

    remove_job(job["job_id"])


def reconcile_stale_running_jobs(logger, worker_state):
    running_jobs = list_jobs("running")
    if not running_jobs:
        if worker_state:
            logger.info("Worker task is not active and there is no running job; clearing stale worker runtime state")
            clear_worker_state(expected_instance_id=worker_state.get("worker_instance_id"))
        return []

    for job in running_jobs:
        reconcile_stale_running_job(logger, job, worker_state)

    if worker_state:
        clear_worker_state(expected_instance_id=worker_state.get("worker_instance_id"))
    else:
        clear_worker_state()

    return running_jobs


def write_terminal_log_from_record(record):
    temp_path = create_temp_yaml(record, prefix="{}.terminal".format(record["job_id"]))
    promote_temp_terminal_log(
        temp_path,
        record["response_hash"],
        commands_folder=ONEDRIVE_COMMANDS_FOLDER,
    )


def request_worker_task_run(logger):
    logger.info("Worker task trigger requested for '%s'", WORKER_TASK_NAME)
    trigger_task(WORKER_TASK_NAME)


def build_eta_status_summary(worker_task_active):
    eta_stats = load_eta_stats()
    active_queue_jobs = list_jobs("queued")
    if worker_task_active:
        active_queue_jobs += list_jobs("running")

    queue_estimate = estimate_jobs_minutes(active_queue_jobs, eta_stats)
    return {
        "sample_window": ETA_SAMPLE_WINDOW,
        "default_runtime_per_10min": DEFAULT_RUNTIME_PER_10MIN,
        "camera_coefficients": summarize_camera_coefficients(eta_stats),
        "queue_estimated_minutes": round(queue_estimate["total_minutes"], 2),
        "default_cameras_used": queue_estimate["default_cameras"],
    }


def run_supervisor():
    logger = setup_role("supervisor")
    logger.info("Supervisor tick started")

    lock_state = acquire_supervisor_lock()
    if lock_state is None:
        logger.info("Another supervisor instance is already running; exiting this tick")
        return

    registered_jobs = 0
    parse_failures = 0
    worker_task_info = None
    worker_runtime_state = None
    try:
        cleanup_old_terminal_logs()
        safe_cleanup_folder(logger, os.path.join(PROJECT_ROOT, "Downloads"))
        safe_cleanup_folder(logger, ONEDRIVE_UPLOADS_FOLDER)

        worker_task_info = query_worker_task_info(logger)
        worker_task_active = worker_task_info.get("Exists") and is_task_active(worker_task_info)

        for response_path in list_response_files():
            try:
                response_hash = get_response_hash_from_file(response_path)
            except FileNotFoundError:
                logger.warning("Response '%s' disappeared before it could be imported", response_path)
                continue
            if active_job_exists(response_hash) or terminal_log_exists(response_hash):
                logger.info("Skipping duplicate response '%s' because job '%s' is already known", response_path, response_hash)
                continue

            try:
                intake_status = register_response(
                    logger,
                    response_path=response_path,
                    worker_task_active=worker_task_active,
                )
            except Exception:
                logger.exception("Unexpected failure while importing response '%s'", response_path)
                raise

            if intake_status == "registered":
                registered_jobs += 1
            elif intake_status == "parse_failed":
                parse_failures += 1

        worker_task_info = query_worker_task_info(logger)
        worker_task_active = worker_task_info.get("Exists") and is_task_active(worker_task_info)
        worker_runtime_state = read_worker_state()

        if worker_task_active:
            logger.info("Worker task is active; stale running-job reconciliation is skipped for this tick")
        else:
            running_jobs = list_jobs("running")
            if running_jobs:
                reconcile_stale_running_jobs(logger, worker_runtime_state)
            elif worker_runtime_state:
                logger.info("Worker task is not active and there is no running job; clearing stale worker runtime state")
                clear_worker_state(expected_instance_id=worker_runtime_state.get("worker_instance_id"))
            worker_runtime_state = read_worker_state()

        queued_jobs = count_jobs("queued")
        if queued_jobs > 0:
            if not worker_task_info.get("Exists"):
                logger.error(
                    "Queued jobs are waiting, but Worker task '%s' is missing or misconfigured",
                    WORKER_TASK_NAME,
                )
                send_report_email(role="supervisor")
            elif worker_task_active:
                logger.info("Worker task '%s' is already active; not requesting another run", WORKER_TASK_NAME)
            else:
                request_worker_task_run(logger)
                worker_task_info = query_worker_task_info(logger)
                worker_task_active = worker_task_info.get("Exists") and is_task_active(worker_task_info)

        worker_runtime_state = read_worker_state()

        write_runtime_summary(
            {
                "checked_at": utcnow_iso(),
                "supervisor_run_id": lock_state["run_id"],
                "queued_jobs": count_jobs("queued"),
                "running_jobs": count_jobs("running"),
                "registered_jobs_this_tick": registered_jobs,
                "parse_failures_this_tick": parse_failures,
                "worker_task_name": WORKER_TASK_NAME,
                "worker_task": build_task_summary(worker_task_info or {"TaskName": WORKER_TASK_NAME}),
                "worker_runtime": build_worker_summary(worker_runtime_state),
                "eta": build_eta_status_summary(worker_task_active),
            }
        )
        logger.info("Supervisor tick finished\n")
    except Exception:
        logger.exception("Supervisor tick failed")
        send_report_email(role="supervisor")
        raise
    finally:
        release_supervisor_lock(lock_state)


def prepare_worker_state(worker_instance_id, trigger_source, current_job_id=None):
    current_time = utcnow_iso()
    state = {
        "worker_instance_id": worker_instance_id,
        "started_at": current_time,
        "heartbeat_at": current_time,
        "status": "starting",
        "current_job_id": current_job_id,
        "trigger_source": trigger_source,
    }
    write_worker_state(state)
    return state


def build_args_from_job(job):
    logger = logging.getLogger("HikLoadWorker")
    argv = job_to_argv(job)
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        logger.error(
            "Argument parsing failed for job '%s' with argv=%s and exit code=%s",
            job.get("job_id"),
            argv,
            exc.code,
        )
        raise RuntimeError(
            "Argument parsing failed for job '{}' with argv {}".format(job.get("job_id"), argv)
        ) from exc
    apply_default_connection_args(args)
    translate_cameras(args)
    return args


def execute_job(job, worker_instance_id):
    logger = logging.getLogger("HikLoadWorker")
    touch_worker_state(expected_instance_id=worker_instance_id, heartbeat_at=utcnow_iso(), status="building_args")
    args = build_args_from_job(job)

    logger.info("Running queued job '%s'", job["job_id"])
    logger.info(args)

    touch_worker_state(expected_instance_id=worker_instance_id, heartbeat_at=utcnow_iso(), status="running")
    try:
        run_result = run(args, include_metrics=True)
    except Exception as exc:
        logger.exception("Heavy video workflow failed for job '%s'", job["job_id"])
        return {
            "status": "failure",
            "result_message": "Video processing failed",
            "error_summary": str(exc),
            "send_report": True,
        }

    output_filenames = run_result.get("output_filenames") or []
    camera_processing_seconds = run_result.get("camera_processing_seconds") or {}
    if camera_processing_seconds:
        logger.info(
            "Per-camera processing seconds for job '%s': %s",
            job["job_id"],
            {camera: round(seconds, 2) for camera, seconds in camera_processing_seconds.items()},
        )

    if len(output_filenames) == 0:
        return {
            "status": "no_recordings",
            "result_message": "No recordings were found for the requested time range",
            "error_summary": None,
            "send_report": False,
        }

    output_paths = normalize_output_paths(output_filenames, args.downloads)
    for output_path in output_paths:
        touch_worker_state(expected_instance_id=worker_instance_id, heartbeat_at=utcnow_iso(), status="uploading")
        try:
            if job.get("harddisk_save"):
                copy_file_to_harddisk(output_path)
            upload_to_onedrive(output_path)
        except Exception as exc:
            logger.exception("Post-processing failed for '%s'", output_path)
            return {
                "status": "failure",
                "result_message": "Video upload failed",
                "error_summary": str(exc),
                "send_report": True,
            }

        if job.get("youtube_upload"):
            logger.warning("Upload to YouTube is disabled")

    return {
        "status": "success",
        "result_message": "Video processing and upload finished successfully",
        "error_summary": None,
        "send_report": False,
        "camera_processing_seconds": camera_processing_seconds,
    }


def send_final_email(job, outcome):
    responder = job.get("responder")
    if not responder:
        return

    if outcome["status"] == "success":
        send_success_email(to=responder, video_name=job.get("videoname"))
    elif outcome["status"] == "no_recordings":
        send_no_recordings_email(to=responder, video_name=job.get("videoname"))
    else:
        send_failure_email(to=responder, video_name=job.get("videoname"))


def run_worker(job_id=None, worker_instance_id=None):
    logger = setup_role("worker")
    worker_instance_id = worker_instance_id or str(uuid.uuid4())
    trigger_source = "explicit_job_id" if job_id else "auto_claim"

    if job_id:
        logger.info("Worker instance '%s' started for requested job '%s'", worker_instance_id, job_id)
    else:
        logger.info("Worker instance '%s' started in auto-claim mode", worker_instance_id)
        logger.info("Worker instance '%s' is auto-claiming the oldest queued job", worker_instance_id)

    should_clear_worker_state = False
    running_job = None
    try:
        prepare_worker_state(worker_instance_id, trigger_source, current_job_id=job_id)
        logger.info("Worker instance '%s' wrote initial runtime state", worker_instance_id)
        worker_started_at = utcnow_iso()
        if job_id:
            running_job = claim_queued_job(
                job_id=job_id,
                worker_instance_id=worker_instance_id,
                worker_started_at=worker_started_at,
            )
        else:
            running_job = claim_next_queued_job(
                worker_instance_id=worker_instance_id,
                worker_started_at=worker_started_at,
            )

        if running_job is None:
            if job_id:
                logger.warning("Queued job '%s' no longer exists; worker will exit", job_id)
            else:
                logger.info("Worker instance '%s' found no queued job to claim and will exit", worker_instance_id)
            should_clear_worker_state = True
            return

        job_id = running_job["job_id"]
        touch_worker_state(
            expected_instance_id=worker_instance_id,
            heartbeat_at=utcnow_iso(),
            status="claimed",
            current_job_id=job_id,
        )
        logger.info("Worker instance '%s' claimed job '%s'", worker_instance_id, job_id)

        outcome = execute_job(running_job, worker_instance_id)
        finished_at = utcnow_iso()
        terminal_record = build_terminal_record_from_job(
            running_job,
            status=outcome["status"],
            finished_at=finished_at,
            result_message=outcome["result_message"],
            error_summary=outcome["error_summary"],
            worker_summary=build_worker_summary(read_worker_state()),
        )
        write_terminal_log_from_record(terminal_record)
        if outcome["status"] == "success":
            try:
                update_eta_stats_from_successful_job(
                    running_job,
                    finished_at,
                    camera_processing_seconds=outcome.get("camera_processing_seconds"),
                )
            except Exception:
                logger.exception("Failed to update ETA statistics for successful job '%s'", job_id)
        send_final_email(running_job, outcome)

        remove_job(job_id)
        should_clear_worker_state = True

        if outcome["send_report"]:
            send_report_email(role="worker")
    except Exception:
        if running_job is None:
            logger.exception(
                "Worker instance '%s' crashed before it could claim job '%s'",
                worker_instance_id,
                job_id,
            )
        else:
            logger.exception(
                "Worker instance '%s' crashed while processing job '%s'",
                worker_instance_id,
                job_id,
            )
        send_report_email(role="worker")
        raise
    finally:
        if should_clear_worker_state:
            clear_worker_state(expected_instance_id=worker_instance_id)

    logger.info("Worker instance '%s' finished job '%s'\n", worker_instance_id, job_id)


def main(argv=None):
    entry_args = parse_entrypoint_args(sys.argv[1:] if argv is None else argv)
    if entry_args.role == "worker":
        run_worker(entry_args.job_id, entry_args.worker_instance_id)
    else:
        run_supervisor()
