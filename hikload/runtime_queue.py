import logging
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone

import yaml


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_ROOT = os.path.join(PROJECT_ROOT, "state")
QUEUE_ROOT = os.path.join(STATE_ROOT, "queue")
QUEUED_ROOT = os.path.join(QUEUE_ROOT, "queued")
RUNNING_ROOT = os.path.join(QUEUE_ROOT, "running")
RUNTIME_ROOT = os.path.join(STATE_ROOT, "runtime")
TMP_ROOT = os.path.join(STATE_ROOT, "tmp")

SUPERVISOR_LOCK_FILENAME = "supervisor.lock.yml"
WORKER_STATE_FILENAME = "worker.yml"
RUNTIME_STATUS_FILENAME = "status.yml"
WORKER_MAX_RUNTIME_SECONDS = 4 * 60 * 60

logger = logging.getLogger("QueueRuntime")


def utcnow():
    return datetime.now(timezone.utc)


def utcnow_iso():
    return utcnow().isoformat()


def ensure_state_dirs(state_root=STATE_ROOT):
    os.makedirs(get_queued_root(state_root), exist_ok=True)
    os.makedirs(get_running_root(state_root), exist_ok=True)
    os.makedirs(get_runtime_root(state_root), exist_ok=True)
    os.makedirs(get_tmp_root(state_root), exist_ok=True)


def normalize_state_root(state_root=STATE_ROOT):
    return os.path.abspath(state_root)


def get_queue_root(state_root=STATE_ROOT):
    return os.path.join(normalize_state_root(state_root), "queue")


def get_queued_root(state_root=STATE_ROOT):
    return os.path.join(get_queue_root(state_root), "queued")


def get_running_root(state_root=STATE_ROOT):
    return os.path.join(get_queue_root(state_root), "running")


def get_runtime_root(state_root=STATE_ROOT):
    return os.path.join(normalize_state_root(state_root), "runtime")


def get_tmp_root(state_root=STATE_ROOT):
    return os.path.join(normalize_state_root(state_root), "tmp")


def get_supervisor_lock_path(state_root=STATE_ROOT):
    return os.path.join(get_runtime_root(state_root), SUPERVISOR_LOCK_FILENAME)


def get_worker_state_path(state_root=STATE_ROOT):
    return os.path.join(get_runtime_root(state_root), WORKER_STATE_FILENAME)


def get_runtime_status_path(state_root=STATE_ROOT):
    return os.path.join(get_runtime_root(state_root), RUNTIME_STATUS_FILENAME)


def get_job_path(job_id, status, state_root=STATE_ROOT):
    if status == "queued":
        folder = get_queued_root(state_root)
    elif status == "running":
        folder = get_running_root(state_root)
    else:
        raise ValueError("Unsupported job status '{}'".format(status))
    return os.path.join(folder, "{}.yml".format(job_id))


def _safe_yaml_load(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fl:
        data = yaml.safe_load(fl)
    return data or {}


def atomic_write_yaml(path, data):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fl:
            yaml.safe_dump(data, fl, indent=2, sort_keys=False, allow_unicode=True)
        shutil.copyfile(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                logger.warning("Failed to delete temporary YAML artifact '%s' after a write failure", temp_path)
        raise
    else:
        try:
            os.remove(temp_path)
        except OSError:
            logger.warning("Failed to delete temporary YAML artifact '%s' after writing '%s'", temp_path, path)


def create_temp_yaml(data, prefix, state_root=STATE_ROOT):
    ensure_state_dirs(state_root)
    fd, temp_path = tempfile.mkstemp(
        prefix=prefix + ".",
        suffix=".tmp.yml",
        dir=get_tmp_root(state_root),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fl:
            yaml.safe_dump(data, fl, indent=2, sort_keys=False, allow_unicode=True)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    return temp_path


def discard_temp_file(path):
    if path and os.path.exists(path):
        os.remove(path)


def list_jobs(status, state_root=STATE_ROOT):
    if status == "queued":
        folder = get_queued_root(state_root)
    elif status == "running":
        folder = get_running_root(state_root)
    else:
        raise ValueError("Unsupported job status '{}'".format(status))

    if not os.path.isdir(folder):
        return []

    jobs = []
    for name in os.listdir(folder):
        if not name.endswith(".yml"):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        job = _safe_yaml_load(path)
        if not job:
            continue
        jobs.append(job)

    sort_key = "queued_at" if status == "queued" else "started_at"
    jobs.sort(key=lambda job: (job.get(sort_key) or "", job.get("job_id") or ""))
    return jobs


def count_jobs(status, state_root=STATE_ROOT):
    return len(list_jobs(status=status, state_root=state_root))


def get_oldest_queued_job(state_root=STATE_ROOT):
    jobs = list_jobs(status="queued", state_root=state_root)
    if not jobs:
        return None
    return jobs[0]


def get_oldest_queued_job_id(state_root=STATE_ROOT):
    job = get_oldest_queued_job(state_root=state_root)
    if job is None:
        return None
    return job.get("job_id")


def get_oldest_running_job(state_root=STATE_ROOT):
    jobs = list_jobs(status="running", state_root=state_root)
    if not jobs:
        return None
    return jobs[0]


def active_job_exists(job_id, state_root=STATE_ROOT):
    return queued_job_exists(job_id, state_root=state_root) or running_job_exists(job_id, state_root=state_root)


def queued_job_exists(job_id, state_root=STATE_ROOT):
    return os.path.exists(get_job_path(job_id, "queued", state_root=state_root))


def running_job_exists(job_id, state_root=STATE_ROOT):
    return os.path.exists(get_job_path(job_id, "running", state_root=state_root))


def load_job(job_id, state_root=STATE_ROOT):
    for status in ("running", "queued"):
        path = get_job_path(job_id, status, state_root=state_root)
        if os.path.exists(path):
            job = _safe_yaml_load(path)
            if job:
                return job
    return None


def promote_temp_job_to_queue(temp_path, job_id, state_root=STATE_ROOT):
    ensure_state_dirs(state_root)
    destination = get_job_path(job_id, "queued", state_root=state_root)
    shutil.copyfile(temp_path, destination)
    try:
        os.remove(temp_path)
    except OSError:
        logger.warning("Failed to delete temporary queue artifact '%s' after promotion", temp_path)
    return destination


def claim_queued_job(job_id, worker_instance_id, worker_started_at, state_root=STATE_ROOT):
    queued_path = get_job_path(job_id, "queued", state_root=state_root)
    running_path = get_job_path(job_id, "running", state_root=state_root)
    if not os.path.exists(queued_path):
        return None

    job = _safe_yaml_load(queued_path)
    if not job:
        raise RuntimeError("Queued job '{}' is empty or unreadable".format(job_id))

    shutil.copyfile(queued_path, running_path)
    try:
        os.remove(queued_path)
    except OSError:
        logger.warning("Failed to delete queued job '%s' after moving it to running", queued_path)

    job["status"] = "running"
    job["worker_instance_id"] = worker_instance_id
    job["worker_started_at"] = worker_started_at
    job["heartbeat_at"] = worker_started_at
    job["started_at"] = worker_started_at
    job["max_runtime_seconds"] = WORKER_MAX_RUNTIME_SECONDS

    atomic_write_yaml(running_path, job)
    return job


def claim_next_queued_job(worker_instance_id, worker_started_at, state_root=STATE_ROOT):
    job_id = get_oldest_queued_job_id(state_root=state_root)
    if job_id is None:
        return None
    return claim_queued_job(
        job_id=job_id,
        worker_instance_id=worker_instance_id,
        worker_started_at=worker_started_at,
        state_root=state_root,
    )


def update_job(job, state_root=STATE_ROOT):
    job_id = job["job_id"]
    status = job["status"]
    path = get_job_path(job_id, status, state_root=state_root)
    atomic_write_yaml(path, job)


def remove_job(job_id, state_root=STATE_ROOT):
    removed = False
    for status in ("queued", "running"):
        path = get_job_path(job_id, status, state_root=state_root)
        if os.path.exists(path):
            os.remove(path)
            removed = True
    return removed


def is_process_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def build_process_identity(run_id=None):
    pid = os.getpid()
    started_at = utcnow_iso()
    return {
        "run_id": run_id or str(uuid.uuid4()),
        "pid": pid,
        "started_at": started_at,
    }


def acquire_supervisor_lock(state_root=STATE_ROOT):
    ensure_state_dirs(state_root)
    lock_path = get_supervisor_lock_path(state_root)
    lock_state = build_process_identity()

    for _ in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fl:
                yaml.safe_dump(lock_state, fl, indent=2, sort_keys=False, allow_unicode=True)
            return lock_state
        except FileExistsError:
            existing = _safe_yaml_load(lock_path)
            if existing and is_process_alive(existing.get("pid")):
                return None

            try:
                os.remove(lock_path)
            except FileNotFoundError:
                continue
            except OSError:
                logger.exception("Failed to remove stale supervisor lock")
                return None

    return None


def release_supervisor_lock(lock_state, state_root=STATE_ROOT):
    lock_path = get_supervisor_lock_path(state_root)
    existing = _safe_yaml_load(lock_path)
    if existing and existing.get("run_id") == lock_state.get("run_id"):
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            return


def read_worker_state(state_root=STATE_ROOT):
    return _safe_yaml_load(get_worker_state_path(state_root))


def write_worker_state(state, state_root=STATE_ROOT):
    ensure_state_dirs(state_root)
    atomic_write_yaml(get_worker_state_path(state_root), state)


def touch_worker_state(expected_instance_id=None, state_root=STATE_ROOT, **updates):
    state = read_worker_state(state_root=state_root) or {}
    if expected_instance_id is not None and state.get("worker_instance_id") not in (None, expected_instance_id):
        logger.warning(
            "Refusing to update worker state for instance '%s' because current state belongs to '%s'",
            expected_instance_id,
            state.get("worker_instance_id"),
        )
        return state

    state.update(updates)
    write_worker_state(state, state_root=state_root)
    return state


def clear_worker_state(expected_instance_id=None, state_root=STATE_ROOT):
    path = get_worker_state_path(state_root)
    if not os.path.exists(path):
        return

    if expected_instance_id is not None:
        state = _safe_yaml_load(path)
        if state and state.get("worker_instance_id") != expected_instance_id:
            logger.warning(
                "Refusing to clear worker state for instance '%s' because current state belongs to '%s'",
                expected_instance_id,
                state.get("worker_instance_id"),
            )
            return

    try:
        os.remove(path)
    except FileNotFoundError:
        return


def write_runtime_summary(summary, state_root=STATE_ROOT):
    ensure_state_dirs(state_root)
    atomic_write_yaml(get_runtime_status_path(state_root), summary)
