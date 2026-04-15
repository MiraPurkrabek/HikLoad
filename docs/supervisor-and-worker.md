# Supervisor and Worker Architecture for HikLoad

## Document status

- This document describes a proposed architecture.
- Parts of the architecture are now implemented in a Task Scheduler-driven form.
- It is intentionally broader than an implementation checklist.
- It is written for developers and operators who will design, implement, deploy, and maintain the system.
- It does not prescribe exact code.
- It does prescribe architectural responsibilities, invariants, failure handling, and operational behavior.
- Where multiple reasonable solutions exist, this document names them explicitly.
- Where the best solution depends on later implementation constraints, the document keeps the topic open and explains the tradeoffs.
- The architecture is designed around the current HikLoad deployment model:
- Microsoft Forms produces command responses.
- OneDrive sync makes `.resp` files appear on the machine.
- A scheduled task launches HikLoad periodically.
- HikLoad parses commands, downloads video, uploads outputs, and sends emails.
- The proposed architecture changes process structure without changing the essential user-facing purpose of the application.

## Current implemented scheduler model

- The currently implemented design uses two Microsoft Task Scheduler tasks:
- `HikLoad Supervisor`
- `HikLoad Worker`
- Only `HikLoad Supervisor` is time-triggered.
- `HikLoad Worker` is on-demand only and has no periodic trigger of its own.
- The Supervisor no longer launches the Worker with `subprocess.Popen(...)`.
- Instead, the Supervisor queries Task Scheduler for the current state of `HikLoad Worker` and requests `schtasks /run /tn "HikLoad Worker"` only when:
- queued work exists, and
- the Worker task is not already active.
- The Worker task is configured in Task Scheduler with:
- `If the task is already running`: `Do not start a new instance`
- `Stop the task if it runs longer than`: `4 hours`
- `If the running task does not end when requested, force it to stop`: enabled
- In this implemented model, Task Scheduler owns:
- Worker lifetime isolation from the Supervisor task
- Worker overlap prevention
- the hard 4-hour execution limit
- In this implemented model, the Supervisor owns:
- response intake
- queue registration
- parse-failure handling
- user registration emails with ETA
- stale local `running` job reconciliation after the Worker task is no longer active
- triggering the on-demand Worker task when queued work exists
- Operators should therefore think of the system as:
- one timer-driven Supervisor task
- one callable Worker task
- not as one scheduled task that directly spawns a child process

## Current Task Scheduler registration

### Task names

- `HikLoad Supervisor`
- `HikLoad Worker`

### `HikLoad Supervisor`

- Purpose: periodic intake, queue maintenance, and Worker triggering
- Trigger: every 1 minute
- Action: `C:\appl\HikLoad\run_main.bat`
- Windows account: the same account currently used by HikLoad and OneDrive on the server
- Settings:
- `Allow task to be run on demand`: enabled
- `If the task is already running`: `Do not start a new instance`
- Conditions:
- mirror the existing working deployment so OneDrive/profile access remains unchanged

### `HikLoad Worker`

- Purpose: on-demand heavy processing of one queued job
- Trigger: none required
- Action: `C:\appl\HikLoad\run_worker.bat`
- Windows account: the same account as `HikLoad Supervisor`
- Settings:
- `Allow task to be run on demand`: enabled
- `If the task is already running`: `Do not start a new instance`
- `Stop the task if it runs longer than`: `4 hours`
- `If the running task does not end when requested, force it to stop`: enabled
- Conditions:
- mirror Supervisor unless there is a deliberate operational reason not to

### Current Worker trigger rule

- On each Supervisor tick, the Supervisor queries the state of `HikLoad Worker`.
- If the Worker task is `Running` or `Queued`, the Supervisor does not request another run.
- If the Worker task is not active and the local queue is non-empty, the Supervisor requests a single on-demand run of `HikLoad Worker`.
- The Worker then auto-claims the oldest queued local job and processes exactly one job before exiting.

## Audience

- Developers extending `main.py`, the upload helpers, and the email pipeline.
- Developers responsible for queue logic, process management, and robustness.
- Operators maintaining Microsoft Task Scheduler.
- Future maintainers who may not remember why the system was split into distinct roles.
- Anyone debugging production incidents involving stuck queue items, duplicate work, or missing emails.

## Why this document exists

- The existing script combines intake and heavy processing in one run.
- That design is simple, but it hides queue state from users.
- It also prevents timely registration of new requests while a long job is already running.
- The proposed change is risky because it introduces process supervision, durable queue state, and lifecycle management.
- Architectural mistakes here can create duplicate workers, invisible stuck jobs, or email spam.
- The goal of this document is to make the design explicit enough that later implementation decisions stay coherent.

## Scope of this document

- This document covers:
- The big-picture process model.
- Functional and non-functional requirements.
- Single-entrypoint design.
- Relationship with Microsoft Task Scheduler.
- Queue management.
- `.resp` and `.yml` lifecycle.
- Supervisor design.
- Worker design.
- Worker hang protection.
- Logging and human readability.
- User notification strategy.
- Integration with the existing codebase.
- Failure safety and restart behavior.
- Testing.
- Migration and rollout concerns.
- Open questions and architecture alternatives.

## Non-goals of this document

- This document does not define Python function signatures.
- This document does not define exact CLI flags or parser syntax.
- This document does not define low-level inter-process APIs.
- This document does not provide source code.
- This document does not lock the system into a specific storage backend if later experience shows a better fit.
- This document does not require multi-worker parallel download execution in the first version.

## Table of contents

- Big picture / Overall concept
- Requirements
- Single entrypoint
- How to connect / how the architecture relates to Microsoft Task Scheduler
- How to prevent multiple processes running in parallel, hanging, and overloading the system
- How to handle the queue, `.resp`, `.yml`, and how to process commands to avoid duplicates
- How to design the supervisor so that it registers new commands as soon as possible
- Exact design of the supervisor
- Exact design of the worker
- How to kill a worker if it hangs too long, or at least how not to run more workers while one is already running
- Logging
- How the new architecture relates to existing code
- Testing
- Additional architecture aspects and open questions

---

## Big picture / Overall concept

### Problem statement

- Today a single script performs both intake and heavy processing.
- Intake means:
- Discovering new `.resp` files.
- Parsing them.
- Translating them into internal arguments.
- Sending parse-failure notifications if needed.
- Heavy processing means:
- Connecting to the NVR.
- Searching for recordings.
- Downloading video.
- Running concatenation and trimming where needed.
- Uploading results.
- Sending final user emails.
- Because intake and heavy processing live in one run, any long-running job blocks later requests from even being acknowledged.

### User-visible pain points

- A user submits a form and receives the Microsoft Forms confirmation.
- If the command is valid and the queue is short, the user eventually receives the "video ready" email.
- If the command is valid but there are long-running jobs ahead, the user sees nothing for a long time.
- If the NVR is unhealthy, the user may also see nothing for a long time.
- The system currently lacks a durable, user-visible idea of "registered", "queued", "running", or "delayed".

### Desired end state

- New commands should be acknowledged soon after they appear on disk.
- A user should not need to infer queue progress from silence.
- Heavy work should remain sequential by default.
- Intake should continue every minute even while a worker is busy.
- Only one Task Scheduler task should be time-triggered.
- A second Worker task may exist as an on-demand callable endpoint.
- The system should avoid duplicate processing.
- The system should fail safely when parsing fails, when the worker crashes, or when the NVR is unavailable.

### Proposed conceptual split

- The application is split into two internal roles:
- Supervisor.
- Worker.
- Both roles live behind one entrypoint.
- Microsoft Task Scheduler launches only the entrypoint.
- The entrypoint defaults to supervisor mode when launched by the scheduler.
- The supervisor performs short, frequent, lightweight tasks.
- The worker performs slow, sequential, heavy tasks.

### Core process model

- Every minute, Microsoft Task Scheduler launches HikLoad.
- HikLoad runs as supervisor.
- The supervisor scans for new `.resp` files.
- The supervisor pre-processes them into durable queue items.
- The supervisor sends "registered / queued" notifications when appropriate.
- The supervisor checks whether a healthy worker already exists.
- If a healthy worker does not exist and the queue is non-empty, the supervisor spawns one worker.
- The supervisor exits quickly.
- The worker drains the queue sequentially.
- The worker exits when the queue is empty or when a controlled idle policy says it should exit.

### Why this architecture fits the current deployment

- The current operational model already expects scheduled polling every minute.
- The current system already uses files as the intake mechanism.
- The current system already has email and logging behavior.
- The new architecture preserves those facts.
- The new architecture only reorganizes responsibilities so that queue intake is decoupled from long-running video operations.

### Fundamental design principle

- Intake must always be cheap.
- Heavy work must never block future intake.
- Scheduling must be simple for operators.
- Process coordination must be explicit rather than accidental.
- Files and logs must explain the current system state without requiring code-level debugging.

### State model from a user perspective

- `received`:
- Sent by Microsoft Forms.
- Outside HikLoad.
- `registered`:
- HikLoad has seen the request and converted it into a durable queue item.
- `queued`:
- The request is valid but not yet running.
- `running`:
- The worker has claimed the request and is executing it.
- `delayed`:
- The request is valid but will take noticeably longer than expected.
- `ready`:
- The request completed successfully.
- `failed`:
- The request failed during execution.
- `parse_failed`:
- The request could not be turned into a valid job.

### Explicit end-user workflow

- From the user point of view, the intended end-to-end flow is:
- User submits Microsoft Form.
- Microsoft Forms sends its own confirmation email.
- HikLoad supervisor parses the synced response.
- If parsing fails:
- HikLoad sends a parse-failure email.
- Processing stops for that command.
- If parsing succeeds:
- HikLoad sends a "command registered" email with a rough ETA based on the current queue.
- The command later begins processing without a separate "started processing" email in version one.
- If downloading, processing, or upload preparation fails during worker execution:
- HikLoad sends a failure email.
- Processing stops for that command.
- If processing and upload complete successfully:
- HikLoad sends a success email that the video is available.

### Important user-experience clarification

- The "command registered" email is the first HikLoad-owned acknowledgement that the system itself accepted the request.
- There is intentionally no separate "processing started" email in version one.
- The user therefore sees:
- Forms confirmation.
- Either parse failure or HikLoad registration.
- Either final failure or final success.
- This is an intentional attempt to improve transparency without overloading the user with too many intermediate notifications.

### State model from a system perspective

- `raw inbound response`:
- A `.resp` file exists in the OneDrive intake folder.
- `stabilized inbound response`:
- The file is considered complete and safe to read.
- `processed response archive`:
- The `.resp` has been consumed once and transformed into a durable internal record.
- `queued job`:
- The job is waiting for the worker.
- `leased / running job`:
- A worker owns the job.
- `completed job`:
- The job finished successfully.
- `failed job`:
- The job finished unsuccessfully.
- `stalled job`:
- The job appears to have been abandoned or its worker heartbeat is stale.

### Suggested first-version philosophy

- Keep one worker only.
- Keep queue ordering FIFO by default.
- Prioritize correctness, observability, and recoverability over throughput.
- Leave true parallel heavy processing as a future option.
- Use the new architecture first to improve transparency and robustness.

---

## Requirements

### Functional requirements

- The system must detect new form response files within approximately one minute.
- The system must parse or safely fail each response exactly once.
- The system must prevent the same response from being enqueued multiple times under normal operation.
- The system must queue valid jobs durably so that they survive process restarts.
- The system must execute heavy work sequentially by default.
- The system must send a final "ready" email on success.
- The system must send an execution failure email on heavy-work failure.
- The system must send a parse-failure email if the command cannot be parsed and a user address is recoverable.
- The system must be able to send a "registered / queued" notification when parsing succeeds.
- The system must estimate wait time or service band for queued jobs.
- The system must allow the supervisor to continue registering new jobs while a worker is already busy.

### Operational requirements

- Only one Microsoft Task Scheduler task should be time-triggered.
- A second Worker task may exist, but only as an on-demand callable task.
- The scheduled setup should remain simple to understand and support.
- The system should not require operators to remember multiple independently timed components.
- The system should be restartable without manual queue repair in common failure cases.
- The queue state should be inspectable from files or simple logs.
- A developer should be able to determine the current queue contents without attaching a debugger.

### Reliability requirements

- The system must not lose a valid request silently.
- The system must not process one request many times because of normal restarts.
- The system must not spawn an unlimited number of workers.
- The system must not assume parent process exit will clean up child workers.
- The system must detect stale worker state.
- The system must fail safe when the NVR is unavailable.
- The system must degrade gracefully when email sending fails.
- The system must survive abrupt termination of either supervisor or worker.

### Performance requirements

- The supervisor should usually complete in a few seconds.
- The supervisor should not spend minutes performing heavy work.
- Queue registration should not require connecting to the NVR.
- Queue ETA calculation should remain lightweight.
- Worker start-up should not be expensive enough to noticeably slow queue throughput.
- Log writing should not become a bottleneck.

### User experience requirements

- A user should understand whether HikLoad has seen the request.
- A user should understand whether the request is waiting or actively running.
- The system should not overwhelm users with too many emails.
- The system should avoid precise promises it cannot keep.
- The system should speak in reliable states rather than optimistic guesses.
- The system should retain final result notifications as the most important user-facing event.

### Maintainability requirements

- The architecture should be understandable from the file layout and log layout.
- The system should clearly separate intake concerns from heavy processing concerns.
- The system should allow future evolution to multiple workers if that is ever desired.
- The architecture should not make the current download logic impossible to reuse.
- The queue and process model should be testable without the NVR.

### Security and safety requirements

- Queue state should not require storing credentials inside each job file.
- Secrets should remain in the existing password/config system or equivalent secure storage.
- Job metadata should not accidentally leak more sensitive content than needed.
- File operations should be atomic where possible.
- The system should not trust partially written OneDrive files.

### Compatibility requirements

- The new architecture should remain compatible with the current OneDrive-based command intake.
- The new architecture should remain compatible with the existing email-sending flow.
- The existing download logic should be reused as much as practical.
- Existing CLI-based manual use should remain possible, even if the internal modes become richer.

### Failure-mode requirements

- A malformed `.resp` must become an archived processed artifact, not remain forever in the inbound folder.
- A crash while converting `.resp` into a queue item must be observable.
- A crash while processing a running job must not cause silent queue corruption.
- A stale worker must not block the queue forever.
- A stale lock must not block future supervisors forever.

### Optional but highly desirable requirements

- A lightweight operator status report.
- Historical runtime data for ETA calibration.
- Per-job logs.
- Rate-limited developer alerts for repeated infrastructure failures.
- Optional delayed emails only when waiting exceeds a threshold.

---

## Single entrypoint

### Architectural goal

- The system should expose one operational entrypoint.
- That entrypoint should be what Microsoft Task Scheduler launches.
- That entrypoint should be what operators think of as "HikLoad".
- Internal role split must not force operators to manage multiple scheduled tasks.

### Why one entrypoint matters

- One scheduled task is easier to audit.
- One scheduled task is easier to document.
- One scheduled task reduces operational drift.
- One scheduled task reduces the chance that intake and heavy processing get out of sync.
- One entrypoint keeps ownership clear.

### Conceptual entrypoint modes

- `supervisor` mode:
- Fast.
- Scheduler-facing.
- Responsible for intake and worker orchestration.
- `worker` mode:
- Slow.
- Internal.
- Responsible for claiming and executing jobs.
- Optional future internal modes:
- `repair`.
- `status`.
- `requeue`.
- `doctor`.
- These are useful but not required for the first implementation.

### Recommended default behavior

- When invoked with no explicit internal mode, the application should run as supervisor.
- The batch file and Task Scheduler should continue to call the default entrypoint.
- The supervisor should spawn the worker by invoking the same entrypoint with an internal worker mode.

### Why not use two top-level scripts

- Two top-level scripts create operational coupling that people can forget.
- Two top-level scripts invite configuration drift.
- Two top-level scripts make documentation longer and more fragile.
- Two top-level scripts increase the chance that only one gets updated during deployment.

### Why not use two scheduled tasks with one entrypoint

- This still creates hidden operational coupling.
- It still requires separate schedules, separate multiple-instance policies, and separate monitoring.
- The proposed architecture is specifically meant to avoid that.

### Responsibilities of the entrypoint dispatcher

- Parse internal mode.
- Initialize logging according to mode.
- Initialize common configuration.
- Route to supervisor or worker logic.
- Ensure mode-specific logs are clearly separated.
- Fail loudly if the mode is unknown.

### Operator mental model

- "The scheduled task launches HikLoad every minute."
- "HikLoad always performs intake."
- "HikLoad ensures one worker exists when needed."
- "Heavy processing is an internal concern, not an extra scheduled job."

### Important invariant

- The scheduled entrypoint must remain lightweight.
- If the default entrypoint starts doing heavy work directly, the architecture collapses back into the old failure mode.

### Relationship to `run_main.bat`

- The batch file can remain simple.
- The batch file should keep calling the default entrypoint.
- The batch file does not need to know about worker mode unless the worker is intentionally launched through the same file.
- Even if the worker is launched indirectly, the operator-facing contract remains unchanged:
- One batch file.
- One scheduled task.

### Possible internal invocation patterns

- Pattern A:
- Supervisor directly spawns the Python entrypoint in worker mode.
- Pattern B:
- Supervisor spawns the same batch file with an internal argument.
- Pattern C:
- Supervisor spawns a detached helper that calls the entrypoint in worker mode.
- The best choice depends on process detachment and logging convenience.
- The architecture does not require a specific low-level choice.

### Recommended conceptual choice

- Use the same application entrypoint for both roles.
- Keep the worker internal and intentionally undocumented for end users.
- Keep the top-level operational story simple.

---

## How to connect / how the architecture relates to Microsoft Task Scheduler

### Role of Microsoft Task Scheduler

- Task Scheduler is the periodic trigger.
- Task Scheduler is not the queue.
- Task Scheduler is not the worker state manager.
- Task Scheduler should not be the only protection against duplicate workers.
- Task Scheduler should not be the only recovery mechanism after a crash.

### Current scheduling model

- The current design launches HikLoad every minute.
- If the prior run is still active, no new run begins.
- That behavior currently serializes the entire application, including intake.

### New scheduling model

- Task Scheduler still launches HikLoad every minute.
- The default HikLoad process is now the supervisor.
- The supervisor should normally exit quickly.
- The worker may continue independently after the supervisor exits.

### Key implication

- The scheduled task should be understood as a periodic supervisory tick.
- It is not the long-running worker itself.
- Therefore the scheduled task and the worker must have separate lifecycles even if they share the same executable.

### Task Scheduler configuration topics that matter

- Trigger frequency.
- Multiple-instance policy.
- User account under which the task runs.
- "Run whether user is logged on or not" behavior.
- Whether the task launches hidden or visible console windows.
- Whether the task kills the process if it exceeds a time limit.
- Working directory and environment assumptions.

### Multiple-instance policy

- This deserves explicit design discussion.
- If Task Scheduler is configured to refuse new instances while one scheduled run is still active, a hung supervisor can block future supervisory ticks.
- If Task Scheduler is configured to queue later instances, a hung supervisor can create scheduler backlog.
- If Task Scheduler is configured to allow parallel launches, internal locking becomes essential.

### Recommended philosophy for scheduler policy

- The scheduler should not be trusted as the sole concurrency control.
- Internal application locks should be authoritative.
- This allows the architecture to remain safe even if Task Scheduler behavior changes or an instance hangs.

### Strong recommendation

- The application should self-serialize the supervisor role.
- Whether the scheduler itself blocks overlap should be treated as a secondary safety layer, not the primary one.

### Parent-child lifetime concern

- The supervisor may spawn a worker.
- The supervisor will then usually exit.
- The architecture must not assume that ending the supervisor automatically ends the worker.
- The architecture must treat worker lifetime as independent once spawned.

### Why this matters on Windows

- Windows process lifetimes do not automatically provide the semantics the architecture needs.
- A parent process exiting does not inherently provide safe cleanup, heartbeat, or reaping of child work.
- Therefore explicit lock files, PID files, and heartbeat records are required.
- Later supervisor runs must treat the worker as an independently managed process, not as a child they can only influence while the original parent is alive.
- The architecture assumes all HikLoad supervisor and worker processes run under the same Windows account.
- Under that assumption, a later supervisor run can open and terminate the worker process if policy requires it.

### Detached worker model

- The worker should be launched so that it can outlive the scheduled supervisor tick.
- The worker should be launched so that it does not require the supervisor process to remain alive.
- The worker should be launched so that it can be detected by later supervisor runs.
- The worker should be launched so that it does not create user-visible console noise if the deployment expects a background process.

### Time budgets

- Supervisor budget:
- Typically seconds.
- Worker budget:
- Potentially minutes or hours.
- Because those budgets differ so much, Microsoft Task Scheduler should only be responsible for the short budget directly.

### What Task Scheduler should not do

- It should not be the durable job queue.
- It should not be the heavy-work retry controller.
- It should not be the only stale-worker detector.
- It should not directly encode business logic for queue ordering or user communication.

### What Task Scheduler should do

- Launch the supervisor every minute.
- Provide a stable service account and environment.
- Optionally restart the scheduled task on certain failures.
- Provide event history useful to operators.
- Ensure the same service account is used consistently so that later supervisor runs can inspect and, if necessary, terminate earlier worker processes.

### Operational recommendation

- Document the scheduled task inside the repository.
- Document which executable, arguments, and working directory are expected.
- Document which account runs the task.
- Document the recommended multiple-instance policy and why it was chosen.
- Document what operators should do if the task history shows repeated launch failures.

### Open architecture question

- Whether to prefer:
- Scheduler allows parallel launches and app-level supervisor lock rejects extras.
- Or scheduler disallows overlap and app-level supervisor lock is only a backup.
- The more fail-safe option is usually to let the app own serialization explicitly.
- This avoids complete intake starvation if a prior scheduled run hangs unexpectedly.

---

## How to prevent multiple processes running in parallel, hanging, and overloading the system

### Distinct process categories

- Supervisor processes.
- Worker processes.
- Optional future job-runner subprocesses.
- Each category needs different controls.

### Supervisor concurrency goal

- At most one healthy supervisor should perform work at a time.
- Extra scheduled launches should detect that another healthy supervisor is active and exit quickly.

### Worker concurrency goal

- At most one healthy worker should process heavy jobs at a time in version one.
- Future parallel processing must be an explicit configuration change, not an accidental bug.

### Why internal locking is mandatory

- Task Scheduler alone is not enough.
- Parent-child relationships alone are not enough.
- PID presence alone is not enough.
- Internal application-visible state is needed so that new supervisors can reason about what is already running.

### Supervisor lock

- The supervisor should acquire a lock at the start of its run.
- If another healthy supervisor holds it, the new supervisor should exit quickly.
- The lock should include:
- PID.
- Start time.
- Machine-local unique run identifier.
- Last heartbeat time.
- The lock should not be trusted blindly.
- A later supervisor must be able to detect staleness.

### Worker lock

- The worker should create a separate worker-state record.
- That record should include:
- PID.
- Start time.
- Process creation or start identity information precise enough to distinguish PID reuse.
- Worker instance identifier.
- Last heartbeat.
- Current job identifier if any.
- Current phase if any.
- Max allowed wall-clock runtime.

### Why PID alone is unsafe

- PIDs can be reused.
- A stale file pointing to an old PID can accidentally match a newer unrelated process.
- Therefore worker identity should include both PID and process birth context.
- A supervisor must never kill a process based on PID alone.
- Before terminating a worker, the supervisor should verify:
- PID.
- Recorded worker instance identifier.
- Recorded `started_at` or equivalent creation-time value.
- Same-account execution context assumptions.

### Heartbeat

- Heartbeat is the main liveness signal.
- Supervisor heartbeat can be simple because the supervisor is short-lived.
- Worker heartbeat is crucial because heavy work may take a long time.
- The heartbeat should update on a regular cadence that is short enough to detect hangs but not so short that it thrashes disk.
- Heartbeat should not be the only stale criterion.
- The first-version architecture also imposes a hard worker wall-clock runtime ceiling.

### Worker overload prevention

- The system should not spawn more workers merely because the queue grows.
- Queue length alone must not override the configured worker limit.
- Future parallel processing, if introduced, should be controlled by an explicit `max_workers` policy.

### Strong invariant

- A healthy worker must be unique.
- A second worker should not appear unless:
- The configured worker count is increased.
- Or a stale worker has been explicitly declared dead and abandoned.

### Why "hanging processes" need architectural treatment

- A hung worker is worse than a slow worker.
- A slow worker still progresses eventually.
- A hung worker blocks throughput and may mislead users about ETA.
- If a hung worker is not detectable, the supervisor may stop spawning new workers forever.

### Stale worker detection

- If the worker heartbeat is missing or too old, the worker should be considered suspicious.
- Suspicion does not automatically mean immediate kill.
- There should be at least two conceptual states:
- `healthy`.
- `stale`.
- An optional third state is useful:
- `orphaned`.
- Orphaned means the state record exists but the process no longer appears to be alive.

### Overload of the NVR

- Multiple worker protection is not only about the local machine.
- It is also about protecting the NVR.
- Parallel heavy video operations can overload:
- The NVR.
- The network.
- Local disk.
- FFmpeg.
- The architecture should therefore default to one worker unless measured evidence supports more.

### Supervisor duration protection

- The supervisor should not accidentally become long-running.
- If the supervisor spends too long on queue scanning, parsing, or notification, that is an architecture bug.
- Time budgets for the supervisor should be observable in logs.
- Long supervisors should generate operator warnings.

### Optional future extra safety layer

- A global application state file can record:
- Current supervisor id.
- Current worker id.
- Queue counts.
- Last successful supervisor tick.
- Last successful worker heartbeat.
- This is not required, but it makes operations clearer.

### Recommended approach

- Use explicit process-role state files or equivalent local persistent records.
- Keep supervisor and worker locks separate.
- Use heartbeats and process metadata together.
- Never infer safety from scheduler behavior alone.
- Treat worker termination as an application-controlled operation performed by later supervisor runs under the same Windows account.

---

## How to handle the queue, `.resp`, `.yml`, and how to process commands to avoid duplicates

### Fundamental queue principle

- The OneDrive `.resp` folder is an intake source.
- It should not remain the durable active queue.
- Active queue state should move into a local, application-owned queue structure.
- This prevents the heavy processing logic from depending on OneDrive latency or reappearance behavior.

### Why not keep the active queue in OneDrive

- OneDrive synchronization can be delayed.
- OneDrive can briefly expose partially synchronized files.
- OneDrive may recreate deleted files under conflict scenarios.
- OneDrive is not designed to be a transactional queue.
- Queue state mixed with sync state becomes hard to reason about.

### Recommended conceptual separation

- OneDrive folder:
- Source of newly arrived `.resp` files only.
- Local queue storage:
- Source of truth for jobs once imported.
- Local archive storage:
- Historical record of consumed responses and completed jobs.

### High-level artifact lifecycle

- External response arrives as `.resp`.
- Supervisor waits until the file is stable enough to trust.
- Supervisor ingests the `.resp`.
- Supervisor writes an internal durable record.
- Supervisor removes or archives the original `.resp`.
- Worker processes the internal durable record.
- Final queue state is written locally.

### One important architectural decision

- Decide whether `.yml` remains:
- Only a parsed argument file.
- Or the full durable job record.
- Both are possible.
- The second option is more expressive.

### Option A: `.yml` as parsed command only

- The `.yml` contains canonical command arguments.
- Queue status lives elsewhere.
- This keeps command representation simple.
- It requires separate state tracking for queued/running/finished metadata.

### Option B: `.yml` as full job record

- The `.yml` contains:
- Canonical command fields.
- Queue metadata.
- Status fields.
- Timing fields.
- Notification markers.
- Error summaries.
- This centralizes job state into one human-readable artifact.

### Recommended conceptual choice

- Treat the durable `.yml` as the job record, not merely a parsed argument file.
- The parsed command should be one section of that record.
- Operational state should be another section.
- This produces one human-readable artifact per job.

### Why this is useful

- Operators can inspect one file and understand what happened.
- Developers can debug queue state without joining multiple data sources.
- Per-job logging can reference the same job id.

### Suggested conceptual job record contents

- Job id.
- Source response fingerprint.
- Source filename.
- Responder address if known.
- Canonical parsed command.
- Raw response text or archive reference.
- Parse status.
- Queue status.
- Timestamps for enqueue, claim, start, finish.
- Worker instance id that claimed it.
- Worker PID and worker `started_at` snapshot at claim time if helpful for later diagnostics.
- Notification markers.
- Error summaries.
- Optional ETA snapshot at registration time.

### Queue state layout options

- Option 1:
- Separate directories for `queued`, `running`, `completed`, `failed`, and `parse_failed`.
- Option 2:
- One jobs directory with status field inside each job file.
- Option 3:
- SQLite or other database table with job state.

### File-based queue advantages

- Easy to inspect.
- Easy to back up.
- Natural fit with current file-driven system.
- Good enough for one worker.
- Easy to archive raw responses and parsed artifacts together.

### File-based queue disadvantages

- Requires careful atomic rename strategy.
- Can get messy if too many job files accumulate in one folder.
- Needs explicit locking discipline.
- Harder to query historically than a database.

### Database-based queue advantages

- Easier state transitions.
- Easier dedup queries.
- Easier historical analytics for ETA.
- Easier future multi-worker claims.

### Database-based queue disadvantages

- Adds operational complexity.
- Harder for non-developers to inspect.
- Less aligned with the current architecture style.
- May be overkill for the first version.

### Recommended first-version queue backend

- Prefer a file-based queue with careful atomic operations.
- Keep the possibility of future migration to SQLite open.
- Design job state transitions so they conceptually map to either backend later.

### Duplicate-prevention principle

- The system must prevent accidental duplicate intake.
- It must also avoid incorrectly suppressing intentional duplicate user requests.
- Therefore deduplication should operate on response identity rather than semantic command equality.

### Good dedup signal candidates

- Source response filename.
- Source response content hash.
- Source file modification time.
- Intake timestamp.
- Combination of the above.

### Recommended dedup identity

- Generate a response fingerprint from content plus contextual metadata.
- Store that fingerprint in the durable job record.
- Record that the fingerprint has been consumed.
- If the exact same inbound response reappears, the supervisor should recognize it as already imported.

### Important caution

- Two users may legitimately submit semantically identical commands.
- Do not deduplicate only by normalized command fields.
- Deduplicate by source response identity, not by business intent.

### Handling partially written `.resp` files

- OneDrive may expose a file before it is stable.
- Intake must not parse a file the instant it appears if its content may still change.
- The supervisor should use a file-stability rule.

### File stability rule options

- Age threshold:
- Only ingest files older than some minimum age.
- Double-scan stability:
- Only ingest files whose size and modification time are unchanged across two scans.
- Temporary naming contract:
- Ingest only after a marker rename indicates file completeness.
- The third option depends on external systems and is usually not available.

### Recommended stability approach

- Use a simple stability check that does not depend on external behavior.
- For example:
- File is older than a small threshold.
- Or file size and modification time are stable across ticks.
- The exact method is implementation detail.
- The architectural requirement is clear:
- Never trust a file that may still be mid-sync.

### Intake success path

- Read stable `.resp`.
- Attempt parse.
- Build canonical command fields.
- Build durable job record.
- Persist the job record atomically.
- Mark original response as consumed.
- Enqueue job.
- Send queue-registration email if policy allows.

### Intake parse-failure path

- Read stable `.resp`.
- Attempt parse.
- Recover safe metadata if possible.
- Persist failure artifact atomically.
- Mark original response as consumed.
- Send parse-failure email if responder is known.
- Send developer crash or debug information according to policy.

### Why atomic persistence matters

- If the process crashes after reading `.resp` but before writing the durable job artifact, the command may be lost or retried ambiguously.
- If the process crashes after writing the durable artifact but before marking `.resp` consumed, duplicate intake may occur.
- Therefore the order and atomicity of file moves and writes matter.

### Conceptual atomicity pattern

- Write temporary job artifact.
- Flush it.
- Rename it atomically into the queue.
- Only then remove or move the original `.resp`.
- If removal fails, keep a clear reconciliation path.

### Archiving the original `.resp`

- There are two main choices:
- Delete the original `.resp` after successful import.
- Move it to a local archive folder.
- Deletion is simpler.
- Local archival is better for forensics.

### Recommended archival approach

- Preserve enough information locally for audit.
- This can be:
- Full raw response text embedded in the job record.
- Or a local archive copy.
- Because parse-failure analysis is important, retaining raw content somewhere local is highly desirable.

### Queue ordering

- Default recommendation:
- FIFO by intake completion time.
- This is fair, understandable, and easy to explain to users.
- Alternative strategies:
- Prioritize official or urgent jobs.
- Prioritize short jobs for better average wait.
- Prioritize older jobs only after a delay threshold.
- These policies complicate user expectations.
- FIFO is the safest default.

### Claiming jobs

- The worker must claim a job explicitly.
- Claiming should be durable.
- A claimed job should not still look queued.
- If file-based directories are used, moving a job from `queued` to `running` is conceptually strong.

### Running job record

- The running job should record:
- Worker instance id.
- Claim time.
- Last heartbeat.
- Current phase.
- Retry count if retries are supported.

### Completion path

- On success, move or mark job as completed.
- On failure, move or mark job as failed.
- On parse failure, mark separately from execution failure.
- Keep final error summaries short and human-readable.

### Job retention

- Completed and failed jobs should not remain forever in the active queue folders.
- Archive retention should be configurable.
- Operator-inspectable history is valuable.
- Unbounded growth is not.

### Optional auxiliary index

- Even in a file-based design, a lightweight summary index can be useful.
- It can list:
- Queue counts.
- Oldest queued job.
- Current running job.
- Last successful worker heartbeat.
- This is optional but helpful for operator dashboards and quick diagnostics.

---

## How to design the supervisor so that it registers new commands as soon as possible

### Fundamental supervisor objective

- The supervisor exists to make intake timely and cheap.
- It should run every minute.
- It should not wait for heavy jobs to finish.
- It should register new commands as soon as those commands are stable on disk.

### Registration means

- The command was discovered.
- The command was parsed successfully.
- A durable job record was created.
- The raw response will not be parsed again.
- The user can now be informed that HikLoad has accepted the request into its own queue.

### Important distinction

- Registration is not the same as execution.
- Registration does not mean the NVR was contacted.
- Registration does not mean the job started.
- Registration does mean the job is no longer waiting invisibly in the inbound folder.

### Why registration must be fast

- User trust improves immediately when HikLoad can acknowledge the request.
- Queue ETA only becomes meaningful after registration.
- Parse failures should be communicated quickly.
- Intake must continue even during long-running jobs.

### What the supervisor should do every minute

- Start.
- Acquire supervisor lock.
- Initialize supervisor log context.
- Inspect inbound `.resp` files.
- Decide which files are stable enough to consume.
- For each stable new file:
- Parse and create a durable job record or a durable parse-failure artifact.
- Send user and developer notifications according to status.
- Update queue state.
- Inspect worker health.
- Spawn a worker if needed.
- Write summary status.
- Exit.

### What the supervisor must not do

- Connect to the NVR for heavy operations.
- Download or upload any video.
- Run FFmpeg-heavy actions.
- Hold a long-lived process role.
- Spend so much time on one tick that later ticks pile up behind it.

### Registration latency target

- Since Task Scheduler runs every minute, the practical target is:
- A new stable `.resp` should usually be registered within one scheduler interval.
- If a stability rule intentionally waits one extra cycle, that should be documented.

### Pre-processing responsibilities

- Canonicalize command fields.
- Validate required fields.
- Normalize camera names or ids as needed.
- Normalize date/time representation.
- Sanitize display names such as `videoname`.
- Compute response fingerprint.
- Compute job id.
- Compute an estimated job class or cost band for ETA.
- Record initial queue position.

### ETA generation at registration time

- ETA does not need to be exact.
- ETA does need to be defensible.
- The supervisor can estimate:
- Immediate if no other jobs are queued and no worker is busy.
- Short wait if few jobs are ahead.
- Long wait if queue is deep or the current running job is large.

### Sources for ETA

- Queue length.
- Whether a worker is already running.
- Current running job age.
- Predicted runtime class of the new job.
- Historical average durations by job class.

### Safe messaging style for ETA

- Use ranges or bands.
- Prefer:
- "likely within 10 to 20 minutes".
- "likely within 1 to 2 hours".
- Avoid:
- "ready in 63 minutes".

### Registration email policy recommendation

- Send one registration or queued email after successful parse.
- Do not send repeated per-minute updates.
- Consider sending a delayed update only if the queue becomes materially longer than promised or a threshold is exceeded.

### Interaction with long-running worker

- The entire point of the architecture is that the supervisor can perform registration while the worker is still running.
- This means that queue items arriving during a long current job still get acknowledged promptly.

### Constraint on supervisor complexity

- The supervisor must remain conceptually simple.
- If too many business rules are loaded into the supervisor, it becomes another long-running, failure-prone component.
- Heavy, risky, or blocking operations belong in the worker or in a child runner, not in the supervisor.

### Fail-safe supervisor behavior

- If the supervisor cannot parse a new `.resp`, it should still consume it into a failure artifact.
- If the supervisor cannot send an email, the durable job state should still reflect what happened.
- If the supervisor cannot spawn a worker, queued jobs should remain durable and later ticks should retry.

### Recommended budget

- The supervisor should be able to process a modest batch of new `.resp` files in one minute without stress.
- If the arrival rate becomes high enough that this is not true, the architecture may need batching optimizations or a more durable storage backend.

---

## What is the exact design of supervisor

### Supervisor responsibilities

- Own inbound `.resp` discovery.
- Own intake-side deduplication.
- Own stable import into the queue.
- Own queue-registration notifications.
- Own worker existence checks.
- Own worker spawn decision.
- Own high-level queue summary updates.
- Own stale-worker detection trigger.

### Supervisor start sequence

- Process starts in default mode.
- Load common configuration.
- Select supervisor logging configuration.
- Acquire supervisor lock.
- If lock is unavailable but healthy, log and exit.
- If lock is stale, repair or replace according to policy.
- Load current worker state.
- Load current queue state.
- If a worker exists, compute both heartbeat age and wall-clock runtime from the recorded `started_at` value.

### Supervisor input surface

- OneDrive inbound `.resp` files.
- Local queue directories or job records.
- Local worker state and heartbeat files.
- Existing configuration files.
- Optional historical runtime metrics.

### Supervisor output surface

- New queued job records.
- New parse-failure records.
- Registration emails.
- Worker spawn request.
- Queue summary state.
- Supervisor logs.

### Supervisor algorithmic phases

#### Phase 1: environment sanity

- Ensure required folders exist.
- Ensure log folders exist.
- Ensure queue folders exist.
- Ensure archive folders exist.
- Validate that the application has write permissions.
- Validate that the inbound folder is reachable.

#### Phase 2: queue and worker snapshot

- Read current worker state.
- Determine whether a healthy worker exists.
- Count queued, running, completed, and failed jobs if needed for ETA and logging.
- Identify any stale running jobs.
- Identify whether the worker has exceeded the configured hard wall-clock runtime, initially recommended as 4 hours.

#### Phase 3: inbound scan

- Enumerate `.resp` files.
- Ignore non-response artifacts.
- Sort inbound responses in a stable deterministic order.
- Filter out files that are not yet stable enough to trust.
- Filter out files whose fingerprint was already consumed if dedup memory exists.

#### Phase 4: intake processing

- For each eligible `.resp`:
- Attempt parse.
- Recover safe metadata if parse fails.
- Write durable job or failure artifact.
- Archive or remove inbound response.
- Send notifications consistent with the resulting state.
- Update any intake summary index.

#### Phase 5: worker management

- Re-evaluate queue depth after intake.
- If queue is empty, do not spawn worker.
- If queue is non-empty and worker is healthy, do nothing.
- If queue is non-empty and worker is absent, spawn worker.
- If queue is non-empty and worker is stale, follow stale-worker policy before deciding whether to spawn a replacement.
- If queue is non-empty and the worker exceeds the hard runtime ceiling, follow forced-termination policy before deciding whether to spawn a replacement.

#### Phase 6: housekeeping

- Write summary status.
- Release supervisor lock.
- Exit.

### Supervisor invariants

- It never performs heavy video work.
- It never leaves a successfully imported `.resp` in the inbound folder.
- It never creates more than one worker without explicit configuration allowing it.
- It can be run repeatedly without harming the queue.

### Worker spawn policy

- Spawn only if:
- At least one queued job exists.
- No healthy worker exists.
- No stale-worker quarantine rule forbids restart yet.
- No still-running worker process survives identity verification after a forced-timeout decision.

### What counts as a healthy worker

- Worker state exists.
- PID appears alive.
- Worker instance id matches the state record.
- Heartbeat is younger than the configured stale threshold.
- Wall-clock runtime is below the configured hard limit.

### What counts as a stale worker

- Heartbeat older than threshold.
- Or PID missing while state says running.
- Or worker state incomplete in a way that indicates interrupted lifecycle.
- Or wall-clock runtime exceeds the configured hard limit, initially recommended as 4 hours.

### Supervisor behavior when worker is stale

- Mark the worker as stale in logs and summary state.
- Decide whether to:
- Spawn a replacement immediately.
- Wait one more tick.
- Require manual operator intervention.
- The first version should prefer safety and explicitness.
- If killing stale processes is difficult, at minimum do not spawn duplicate workers while ambiguity remains.
- For the revised first-version policy, the supervisor may forcibly terminate a worker that exceeds the hard 4-hour runtime ceiling after verifying the recorded identity fields.

### Suggested policy for first version

- If the worker heartbeat is stale but the process still appears alive and the worker has not yet crossed the hard runtime ceiling, do not immediately spawn a replacement.
- Mark the system degraded.
- Log clearly.
- Send developer alert if repeated.
- If the worker heartbeat is stale and the process is gone, requeue any orphaned running job after policy checks and spawn a new worker.
- If the worker is still alive but has exceeded the hard wall-clock runtime ceiling of 4 hours, the next supervisor run should verify PID plus `started_at` plus worker instance id, terminate the process under the same Windows account, record the forced stop, reconcile the running job, and then allow a replacement worker to start on a later or the same tick depending on implementation safety.

### Supervisor and ETA ownership

- ETA should be computed by the supervisor at registration time.
- The worker may refine runtime data, but it should not own initial user registration messaging.

### Optional supervisor summary artifact

- A human-readable summary file can be written each tick.
- It can include:
- Last supervisor run time.
- Number of new `.resp` files seen.
- Number of queued jobs.
- Current worker state.
- Oldest queued job age.
- Last error summary.

### Why a summary artifact helps

- Logs are chronological.
- State summaries are current.
- Operators often need the current truth more than they need the entire history.

### Supervisor failure handling

- If the supervisor crashes mid-intake, the next tick should be able to reconcile partially written artifacts.
- If the supervisor crashes before worker spawn, the next tick should still be able to spawn a worker later.
- If the supervisor crashes after worker spawn, the worker should continue.
- If one supervisor crashes before applying a forced-timeout kill, a later supervisor should still be able to enforce the timeout because worker control is based on durable identity metadata rather than parent-child process linkage.

### Optional future extension

- The supervisor can later emit metrics suitable for dashboards.
- This is outside the first version but aligns well with the architecture.

---

## What is the exact design of worker

### Worker responsibilities

- Claim queued jobs.
- Perform heavy processing sequentially.
- Update job state durably.
- Maintain heartbeat while alive.
- Send final user notifications.
- Write clear operational logs.
- Exit when work is done or policy says to stop.

### Worker start sequence

- Process starts in worker mode.
- Select worker logging configuration.
- Acquire worker role record.
- Validate that no conflicting healthy worker already exists.
- If conflict exists, log and exit safely.
- Enter queue-processing loop.

### Worker loop model choices

- Choice A:
- Process exactly one job and exit.
- Choice B:
- Drain the queue until empty and then exit.
- Choice C:
- Stay alive indefinitely and sleep while idle.

### Evaluation of loop choices

- One-job worker:
- Simple.
- Frequent respawn overhead.
- More scheduler/spawn churn.
- Drain-until-empty worker:
- Good balance.
- Efficient for bursts.
- Naturally exits once the queue clears.
- Always-on worker:
- More service-like.
- More complex lifecycle.
- Less aligned with periodic scheduler-driven model.

### Recommended worker loop

- Drain the queue until empty.
- Exit once no queued jobs remain.
- This keeps the worker long-lived enough to be efficient.
- It also keeps lifecycle simple because idle workers do not linger forever.

### Worker queue claim sequence

- Inspect queue for the oldest eligible queued job.
- Atomically claim it.
- Record worker instance id.
- Move or mark job as running.
- Open or associate per-job log context.
- Update heartbeat.

### Worker execution phases

- Phase 1:
- Prepare environment for this job.
- Phase 2:
- Invoke the existing HikLoad heavy-processing logic.
- Phase 3:
- Upload outputs and final delivery actions.
- Phase 4:
- Mark job success or failure.
- Phase 5:
- Send user notification.
- Phase 6:
- Archive or clean up temporary local files.

### Worker and existing code reuse

- The existing `run(args)` flow can remain the core heavy-processing engine.
- The worker should wrap it in queue-aware lifecycle management.
- The worker should not duplicate business logic already present in the current download path unless necessary.

### Worker state record should include

- Worker instance id.
- PID.
- Start time.
- A precise `started_at` value suitable for later identity verification.
- Last heartbeat.
- Current job id.
- Current phase.
- Counters such as number of jobs processed in this session if useful.
- Configured hard runtime ceiling for this worker instance.

### Worker heartbeat behavior

- Update heartbeat at startup.
- Update heartbeat before claiming a job.
- Update heartbeat while a job is running.
- Update heartbeat after finishing a job.
- Update heartbeat before exit.
- Heartbeat updates do not remove the hard runtime ceiling.
- Even a healthy heartbeat does not allow a worker to exceed the configured maximum total runtime indefinitely.

### Important design challenge

- Some heavy operations may block for a long time.
- If the worker only updates heartbeat between phases, a long blocked operation can look like a hang.
- This affects stall detection.

### Possible heartbeat strategies

- Strategy A:
- Heartbeat only at phase boundaries.
- Simpler.
- Less precise.
- Strategy B:
- Independent heartbeat ticker while the worker is alive.
- More robust.
- Requires careful implementation.
- Strategy C:
- Per-job runner subprocess monitored by the worker.
- Strongest isolation.
- Most complex.

### Worker failure classes

- Parse-related failures should not reach the worker.
- Execution failures:
- NVR connection failure.
- No recordings found.
- Download failure.
- FFmpeg failure.
- OneDrive upload failure.
- Hard disk copy failure if enabled.
- Email failure.

### Worker status outcomes

- `success`.
- `failed`.
- `no_recordings`.
- `stalled` or `abandoned`.
- Exact taxonomy can vary, but it must remain human-readable.

### Notification ownership

- The worker owns:
- Success emails.
- Execution-failure emails.
- No-recordings emails.
- Optional delayed-progress emails if this is later added at runtime rather than at registration.
- The supervisor owns:
- Registration/queued emails.
- Parse-failure emails.

### Worker and server-down behavior

- If the NVR is down, the worker may fail quickly or after timeout.
- The architecture should decide whether:
- To fail the current job immediately.
- To retry it with backoff.
- To mark a broader "server unhealthy" condition and keep later jobs queued.
- This is an important policy choice.

### Recommended first-version behavior for NVR unavailability

- Do not let one silently hung connection hide the problem forever.
- Prefer explicit timeout and controlled failure or retry.
- The worker should surface infrastructure health to logs and developer alerts.
- Whether user jobs fail fast or remain queued is an open product decision.

### Queue drain behavior

- After completing one job, the worker should immediately check for the next queued job.
- If one exists, claim it.
- If none exists, exit cleanly.

### Why worker exit on empty is useful

- It reduces the chance of forgotten long-idle processes.
- It fits the current scheduled world better than an always-on daemon.
- It allows later supervisor ticks to make fresh spawn decisions.

### Worker crash handling

- If the worker crashes while no job is running, a later supervisor tick can simply spawn a new worker when needed.
- If the worker crashes during a job, the running job must be reconciled.
- The queue design must represent "job claimed by dead worker".

### Reconciliation of interrupted running jobs

- Options:
- Immediately mark failed.
- Requeue automatically.
- Leave for manual intervention.
- Retry up to a configured count.
- The safest first version depends on how idempotent heavy processing is.

### Conservative recommendation

- Mark interrupted running jobs as stalled first.
- Require explicit policy to move them back to queued or failed.
- This prevents silent duplicate processing if the old worker is actually still doing something.

### Optional runner subprocess architecture

- Supervisor spawns worker.
- Worker claims job.
- Worker spawns isolated per-job runner.
- Worker monitors runner and maintains queue state.
- Benefits:
- Easier hard timeout enforcement.
- Easier kill of a hung job without killing the queue coordinator.
- Clearer per-job isolation.
- Costs:
- More process complexity.
- More IPC or state-sharing needs.
- More logging coordination.

### Recommendation on runner subprocesses

- Keep it optional for the first implementation unless hangs inside heavy job execution are already common enough to justify the extra layer.
- If hangs are a major concern from the beginning, the runner model may be worth it.

---

## How to kill worker if it hangs too long or at least how to not run more workers if the one is already running

### Hanging is not the same as slow

- Slow workers are expected.
- Hanging workers are pathological.
- The system must distinguish "long valid job" from "lost or blocked process".

### Detection inputs

- Heartbeat age.
- Current phase age.
- Current job age.
- Process existence.
- Optional subprocess existence.
- Worker wall-clock runtime since recorded `started_at`.
- Worker identity verification fields such as worker instance id and creation-time metadata.

### Timeout classes

- Soft timeout:
- Warn only.
- Hard timeout:
- Declare worker stale.
- Hard wall-clock worker timeout:
- Force-stop the worker after a maximum total runtime, initially 4 hours.
- Job timeout:
- Current job has exceeded expected upper bound.
- Infrastructure timeout:
- NVR or upload dependencies have not responded in acceptable time.

### Basic stall policy

- If heartbeat is fresh, do not assume a hang even if the job is long.
- If heartbeat is stale and process is gone, treat as dead.
- If heartbeat is stale and process still exists, treat as suspicious.
- If the worker wall-clock runtime exceeds 4 hours, treat it as timed out even if heartbeat is still fresh.

### Minimal safe behavior

- Do not spawn a new worker while a suspicious existing worker may still be active.
- Mark the queue as blocked.
- Log loudly.
- Notify developer if necessary.
- Preserve the running job state for later intervention.
- A timed-out worker should be handled more strongly than a merely suspicious worker because the timeout policy is intentional and deterministic.

### More aggressive behavior

- If worker heartbeat is stale beyond a second threshold, or if the worker exceeds the hard 4-hour wall-clock limit, kill the worker and replace it.
- This is more operationally convenient.
- It is also more dangerous if the worker is not actually hung or if the timeout threshold is too strict for real workloads.

### Why killing is risky

- The worker may be in the middle of file writes.
- The worker may be in the middle of upload.
- The worker may be contacting the NVR or FFmpeg in a way that leaves partial outputs.
- Killing a process is operationally useful but must be paired with careful queue-state recovery.

### Safer kill architecture

- Use a worker as coordinator and a child runner per job.
- Kill only the runner if the job times out.
- Keep the worker alive to update queue state and move the job to failed or stalled.

### Simpler first-version alternative

- Auto-kill only on one explicit rule:
- total worker runtime exceeds 4 hours.
- For sub-threshold stale heartbeat conditions, detect and log first.
- Refuse to spawn a replacement while ambiguity remains.
- Surface the problem clearly in logs and summary state.
- Provide a manual operational procedure for safe cleanup.

### Manual recovery procedure should exist

- Identify worker instance id.
- Check whether the process is truly still alive.
- Verify PID and `started_at` still match the recorded worker state.
- Check whether upload or download files are still changing.
- Terminate the stuck process if necessary.
- Reconcile the running job according to documented policy.
- Restart by letting the next supervisor tick spawn a fresh worker.

### Future automation levels

- Level 1:
- Detect and log.
- Level 2:
- Detect and page or email developer.
- Level 3:
- Detect and auto-stop timed-out or stale process.
- Level 4:
- Detect, stop, repair queue, and restart automatically.
- Level 1 or 2 is safest initially.
- The revised first-version policy intentionally adopts a narrow piece of Level 3 for the hard 4-hour worker timeout only.

### Avoiding duplicate workers during stale ambiguity

- If a worker is suspicious but not proven dead, the system should not start another by default.
- This avoids two workers operating on the same local storage or NVR at once.

### Worker watchdog ownership

- The supervisor can perform watchdog checks every minute.
- Optionally the worker can self-monitor phases and log "I may be stuck".
- The supervisor remains the authoritative process for cross-run decisions.
- Because all HikLoad processes run under the same Windows account, any later supervisor tick may terminate the worker after identity verification even if that supervisor did not originally spawn it.

### Time thresholds should be configurable

- Heartbeat stale threshold.
- Job overdue threshold.
- Max total worker age.
- Max queue wait before delayed email.
- These thresholds should be documented and not hard-coded silently.
- The initial recommended value for max total worker age is 4 hours.

### First-version timeout decision

- Version one should enable one explicit hard-stop rule.
- If a worker runs longer than 4 hours in wall-clock time, the next supervisor tick should attempt to terminate it.
- This termination should only occur after verifying the stored worker identity fields rather than trusting PID alone.
- Heartbeat-based stale detection below the 4-hour ceiling should remain primarily diagnostic unless later operational experience proves that more aggressive handling is safe.

---

## Logging

### Logging goals

- Human-readable first.
- Chronological.
- Easy to grep.
- Clear about which process wrote each line.
- Clear about which job a line belongs to.
- Clear about whether the event is supervisor-side or worker-side.

### Current situation

- Logging currently writes to `logs/latest.log` and `logs/HikLoad.log`.
- Different application components share those files.
- That is workable for a single-process system.
- It becomes noisy in a multi-role architecture.

### Logging redesign principle

- Separate logs by role.
- Optionally separate logs by job.
- Keep a concise summary view for operators.

### Recommended log categories

- Supervisor log.
- Worker lifecycle log.
- Per-job log or job-specific log segment.
- Optional alert or incident log.

### Suggested directory layout

- `logs/supervisor/`.
- `logs/worker/`.
- `logs/jobs/`.
- `logs/archive/` if historical compression is desired.

### Logging fields needed for forced worker termination

- Worker instance id.
- Worker PID.
- Worker `started_at`.
- Supervisor run id that decided to terminate the worker.
- Reason for termination:
- heartbeat stale.
- hard runtime exceeded.
- manual intervention.
- Identity verification outcome before the kill attempt.
- Kill attempt result and follow-up queue reconciliation result.

### Recommended supervisor logging characteristics

- One line per major event.
- Minimal noise.
- Strong emphasis on:
- inbound discovery.
- parse success.
- parse failure.
- queue counts.
- worker spawn decisions.
- stale worker detection.
- Each line should include:
- timestamp.
- role.
- level.
- job id or response fingerprint if relevant.
- concise message.

### Recommended worker logging characteristics

- More detailed than supervisor log.
- Still structured for human scanning.
- Strong emphasis on:
- job claim.
- job phase transitions.
- external dependency failures.
- retries.
- completion.

### Per-job logs

- Highly recommended.
- One job log makes support much easier.
- A user complaint usually maps to one job id.
- A job log should contain all events specific to that request.

### Relation between role logs and per-job logs

- Supervisor and worker logs tell the operational story of the system.
- Per-job logs tell the story of one request.
- Both are useful.

### Human-readable formatting principles

- Avoid JSON-only logs as the primary format if operators routinely open the files directly.
- Plain text with consistent fields is easier to read quickly.
- If machine parsing is desired later, add structured fields in a disciplined textual format.

### Fields that should appear often

- Timestamp.
- Role.
- PID or worker instance id.
- Job id.
- Response fingerprint or source filename when relevant.
- Phase.
- Queue position if relevant.
- Severity.

### Example event categories

- `SUPERVISOR_START`.
- `RESP_DISCOVERED`.
- `RESP_STABLE`.
- `PARSE_OK`.
- `PARSE_FAILED`.
- `JOB_ENQUEUED`.
- `QUEUE_EMAIL_SENT`.
- `WORKER_HEALTHY`.
- `WORKER_SPAWNED`.
- `WORKER_STALE`.
- `JOB_CLAIMED`.
- `JOB_RUNNING`.
- `JOB_UPLOAD_STARTED`.
- `JOB_SUCCESS`.
- `JOB_FAILURE`.
- `NO_RECORDINGS`.
- `DELAY_NOTICE_SENT`.

### Rotation and retention

- Role logs should rotate.
- Per-job logs can be retained alongside job artifacts for a retention period.
- Rotation policy should match expected usage patterns:
- frequent small supervisor logs.
- less frequent but larger worker logs.

### `latest.log` semantics in new architecture

- One global `latest.log` becomes ambiguous.
- Better options:
- `logs/supervisor/latest.log`.
- `logs/worker/latest.log`.
- Optional `logs/current-status.log`.

### Summary log or status file

- A summary status file is often better than a summary log.
- It should show the current truth:
- last supervisor run.
- worker alive or stale.
- queue depth.
- current running job.

### Logging and failures

- Email failures should be logged.
- Notification suppression decisions should be logged.
- Queue repair actions should be logged.
- Stale-lock cleanup actions should be logged.

### Logging and operator sanity

- Avoid duplicating every message into every log.
- Avoid burying queue state in low-level library noise.
- Keep third-party library debug noise out of the main supervisor log unless truly needed.

### Relationship to existing logging config

- The existing centralized logging config is a good base.
- It will need role separation.
- It may need multiple file handlers.
- It may need context-specific logger names or filter rules.

### Open question

- Whether to maintain one combined rotating historical log in addition to separated role logs.
- Combined logs help long incident reconstruction.
- Separated logs help day-to-day operations.
- The architecture can support both if implemented carefully.

---

## User notifications and communication design

### Communication objective

- Inform users enough that they trust the system.
- Do not overwhelm them.
- Make each email correspond to a meaningful state transition.

### Canonical user-visible email sequence

- The canonical version-one user-visible sequence should be documented and preserved during implementation:
- User submits Microsoft Form.
- Microsoft Forms sends confirmation email.
- HikLoad parses the response.
- If parsing fails:
- HikLoad sends parse-failure email and stops.
- If parsing succeeds:
- HikLoad sends "command registered" email with rough ETA.
- HikLoad later starts worker processing with no additional "processing started" email.
- If downloading or processing fails:
- HikLoad sends failure email and stops.
- If processing and upload succeed:
- HikLoad sends success email that the video is available.

### Why this sequence matters

- It defines the user contract independently from internal architecture.
- Supervisor and worker refactors must not accidentally introduce extra email noise without an explicit product decision.
- Operator debugging should always be able to map internal states back to this visible sequence.

### Existing user-visible emails

- Microsoft Forms confirmation.
- HikLoad success email.
- HikLoad execution failure email.
- HikLoad no-recordings email.
- HikLoad parse-failure email.

### Missing state today

- "HikLoad has seen and accepted your request."
- "Your request is waiting in queue."
- "Your request is delayed because the queue is long or infrastructure is unhealthy."

### Recommended email states

- Registration / queued.
- Optional delayed.
- Ready.
- Execution failed.
- No recordings.
- Parse failed.

### Registration email purpose

- Confirm that HikLoad itself has accepted the request.
- Distinguish Microsoft Forms receipt from HikLoad queue acceptance.
- Give the user an initial ETA band or wait estimate.
- Serve as the only planned intermediate user email between parse success and the final outcome in version one.

### Registration email contents

- Friendly acknowledgement.
- Short job summary:
- video name if available.
- requested time range.
- maybe camera summary.
- Queue position or rough category.
- Estimated completion band.
- Clear note that the estimate is approximate.

### Delayed email purpose

- Send only when silence would be misleading.
- Examples:
- Queue is much longer than usual.
- Worker is blocked on infrastructure issue.
- Job exceeded a certain wait threshold.

### Delayed email policy

- Do not send by default for every job.
- Send only if threshold exceeded.
- Send at most once or a very small bounded number of times per job.
- Avoid periodic reminder spam.

### Ready email

- Keep existing semantics.
- It remains the most important success message.

### Failure email

- Keep existing semantics for heavy-processing failure.
- Consider including whether the job failed before or after contacting the NVR.
- Keep the user message short and actionable.

### Parse-failure email

- Already implemented.
- It belongs to intake, not heavy processing.
- It should remain distinct from execution failure.

### No-recordings email

- This is not a system failure.
- It is a valid execution outcome.
- It should remain distinct from infrastructure or parser failure.

### ETA calculation strategies

- Strategy 1:
- Queue-length-only heuristic.
- Strategy 2:
- Queue length plus job complexity bands.
- Strategy 3:
- Historical average runtime by job class.
- Strategy 4:
- Historical percentile estimates by job class.

### Recommended ETA strategy for first version

- Use a simple job complexity model plus queue depth.
- Keep bands broad.
- Refine later using historical data.

### Useful complexity factors

- Requested duration.
- Number of cameras.
- Whether concat is enabled.
- Whether trim is enabled.
- Whether upload/hard-disk copy is enabled.
- Whether previous current job is already long-running.

### Communication style recommendation

- Avoid exact minute promises.
- Prefer wording like:
- "likely within 10 to 20 minutes".
- "likely within about 1 hour".
- "queue is long; completion may take 2 to 3 hours".

### Notification suppression rules

- Do not send registration email again if the same job is re-seen due to recovery.
- Do not send delayed email if the job already completed.
- Do not send both delayed and failure emails at nearly the same time unless the timing truly justifies it.

### Developer notifications

- Developer notifications should remain more detailed.
- They should not mirror every user registration email.
- They should focus on:
- parse failures.
- stale worker.
- repeated infrastructure failure.
- queue blockage.

### Important queue-health communication option

- If the NVR is broadly down, it may be misleading to tell every user only that they are "queued".
- A delayed or infrastructure-degraded message may be more honest after a threshold.

### Open communication question

- Should the user receive a separate "running" email when the worker actually starts the job?
- Benefits:
- More transparency.
- Drawbacks:
- More email volume.
- The first version explicitly does not send it because the intended user-visible sequence is:
- Forms confirmation.
- Parse failure or command registered.
- Final failure or final success.

---

## How to wire / how the new architecture relates to existing code

### Current code shape

- `main.py` currently:
- loads logging.
- cleans old files.
- finds and parses the next response.
- turns the parsed `.yml` into CLI args.
- calls the heavy `run(args)` path.
- uploads output.
- sends final emails.
- `run_main.bat` currently launches `python main.py`.
- `upload/onedrive_utils.py` currently owns response parsing and OneDrive-related helpers.
- `hikload/send_email.py` currently owns email helper functions.
- `hikload/download.py` currently owns the heavy download execution logic.

### New process-control responsibilities to add around existing code

- The current codebase has no durable concept of worker identity across scheduled runs.
- The new architecture must add:
- worker state persistence.
- worker `started_at` recording.
- supervisor-side process lookup and verification.
- supervisor-side process termination path for timed-out workers.
- queue reconciliation after forced termination.

### Architectural reuse strategy

- Preserve the heavy download logic as much as possible.
- Refactor the current monolithic `main.py` role into:
- dispatcher.
- supervisor flow.
- worker flow.
- Keep existing email helpers.
- Extend them with new queue/registration notifications where needed.
- Keep response parsing in the intake side.

### Likely refactoring boundary

- Intake-specific responsibilities should move out of the current heavy-work execution path.
- The worker should receive an already durable job record or equivalent canonical command bundle.
- The worker should not need to know about raw `.resp` files.

### Suggested conceptual module split

- Entry dispatcher module.
- Supervisor module.
- Worker module.
- Queue/state module.
- Intake parser module.
- Existing heavy download module reused with minimal changes.
- Notification module extending current email helpers.

### Relation to current parse logic

- The recent parse-failure work is directly useful.
- It already moves the system toward explicit intake outcomes.
- That logic should become part of the supervisor-side import pipeline.

### Relation to current `.yml` behavior

- Today `.yml` is essentially a parsed argument artifact.
- In the new architecture, that concept can evolve into the durable job record.
- If the team prefers smaller refactoring steps, it can remain a parsed command file first and be wrapped by additional job metadata later.

### Relation to current logging config

- The current logging configuration can be reused conceptually.
- It should be expanded for role separation and possibly per-job logs.
- The loggers should reflect supervisor vs worker roles clearly.

### Relation to current upload behavior

- Upload remains part of worker execution.
- Supervisor should never perform upload.

### Relation to current cleanup behavior

- Cleanup of old queue artifacts becomes more important.
- The current cleanup approach should be reviewed so it does not accidentally remove active queue files or running-job logs.
- Queue-aware retention policies are needed.

### Relation to manual CLI usage

- Manual direct execution of the heavy path should remain possible.
- The internal worker mode should not break existing user-facing CLI semantics.
- The system may need to clearly separate:
- public CLI use.
- internal queue-driven use.

### Migration-friendly implementation idea

- Step 1:
- Introduce dispatcher and internal modes.
- Step 2:
- Implement supervisor-side import while still invoking the existing heavy path for a single job.
- Step 3:
- Add worker loop.
- Step 4:
- Add proper queue state and notifications.
- This staged approach reduces risk.

### Principle to protect during wiring

- The heavy business logic should be wrapped, not rewritten, unless there is a proven reason.
- Queueing architecture should add structure around the existing behavior rather than exploding it into unrelated fragments.

---

## Failure safety and fail-safe behavior

### Why fail-safe design is critical

- The new architecture introduces more moving parts.
- More moving parts mean more possible half-complete states.
- Therefore every state transition must be designed for interruption and restart.

### Key failure classes

- Supervisor crash during intake.
- Worker crash during job execution.
- Supervisor kills the wrong process because worker identity verification was insufficient.
- Email send failure.
- Queue artifact write failure.
- Inbound file removal failure.
- OneDrive folder unavailable.
- NVR unavailable.
- Upload destination unavailable.
- Stale lock or stale heartbeat.

### Required architectural invariants

- A consumed `.resp` should never be silently lost.
- A queued job should always have a durable record.
- A running job should be attributable to a specific worker instance.
- A forced worker kill must only target a process whose identity has been matched by more than PID alone.
- A completed job should remain auditable for a retention period.
- A failed job should preserve enough evidence to diagnose the failure.

### Atomic state transitions

- Write then rename instead of partially overwriting active files.
- Move queued job to running atomically if using directories.
- Move running job to completed or failed atomically if using directories.

### Crash after import but before response removal

- The architecture should detect already imported responses by fingerprint.
- If the `.resp` reappears or remained undeleted, the supervisor should not enqueue it again.

### Crash after claim but before heartbeat

- The job should still be marked running or claimed.
- Later supervisors should see it as running with a stale worker.
- This is why claim state must be durable immediately.

### Crash during upload after successful download

- The job may have produced local outputs but not uploaded them.
- The architecture should document whether retries are allowed from partially completed local outputs.
- At minimum the job state should clearly say which phase failed.

### Email failure handling

- Email failure should not roll back queue state.
- If the job succeeded but the success email failed, the job should still remain successful.
- The log should show that notification failed.
- Operator recovery procedures may resend manually if needed.

### Infrastructure outage handling

- If the NVR is down, queued jobs should not disappear.
- If repeated failures indicate system-wide infrastructure issues, the worker may need a circuit-breaker-like cooldown.
- This can prevent hammering the NVR and generating repetitive failure noise.

### Circuit breaker concept

- If the NVR has failed for several consecutive jobs or health checks, mark a temporary degraded state.
- During that state, the worker can:
- pause retries.
- fail fast.
- or avoid starting more jobs for a short interval.
- The exact policy is open.
- The architectural benefit is clear:
- make global outages explicit rather than treating them as unrelated per-job mysteries.

### Developer visibility during degradation

- The system should produce one strong signal that the queue is blocked by infrastructure problems.
- It should not produce dozens of indistinguishable stack traces if the same root cause repeats.

### Queue repair

- The architecture should allow repair tools or manual procedures to:
- requeue a stalled job.
- mark a stalled job failed.
- resend a notification.
- clear stale worker state.
- reconcile jobs after a forced timeout kill.

### Manual intervention should be documented

- How to identify a healthy worker.
- How to identify a stale worker.
- How to identify the running job.
- How to move a job from stalled to queued or failed.
- How to restart the queue safely.

### Optional safety enhancement

- Keep a compact append-only event journal.
- This can supplement mutable job files.
- It helps reconstruct what happened across crashes.
- It is optional, but architecturally valuable.

---

## Additional architecture aspect: state directories and storage layout

### Why layout matters

- The file layout becomes the operational map of the application.
- Good layout makes incidents easier to diagnose.
- Bad layout hides important distinctions.

### Suggested conceptual storage roots

- `inbound/` or external OneDrive command folder.
- `state/`.
- `state/queue/`.
- `state/queue/queued/`.
- `state/queue/running/`.
- `state/queue/completed/`.
- `state/queue/failed/`.
- `state/queue/parse_failed/`.
- `state/locks/`.
- `state/heartbeats/`.
- `state/archive/`.
- `logs/`.

### Why local state should be outside OneDrive

- Queue state is internal runtime state.
- It should not sync.
- It should not depend on cloud sync ordering.
- It should not be accidentally modified externally.

### Job identifier design

- Job id should be stable after creation.
- Job id should be short enough to appear in filenames and email subjects if needed.
- Job id should not rely only on timestamp.
- A fingerprint or random component is useful.

### Response fingerprint design

- Response fingerprint should uniquely represent the inbound response artifact.
- It should support duplicate intake detection.
- It should be recorded in the job file.

### Archive naming

- Archive naming should make it easy to relate:
- raw response.
- parsed command.
- job log.
- final outcome.

### Human-readable storage principle

- Even if some machine-friendly indexes exist, the primary per-job artifacts should remain understandable when opened directly in a text editor.

---

## Additional architecture aspect: queue fairness, prioritization, and future scaling

### Default fairness policy

- FIFO by successful registration time.
- This is the easiest policy to explain and defend.

### Why not prioritize aggressively in version one

- Prioritization complicates ETA.
- Prioritization complicates support.
- Prioritization complicates fairness expectations.
- Prioritization complicates testing.

### Legitimate future priority cases

- Official or archival jobs.
- Admin override jobs.
- Very short diagnostic jobs.
- Infrastructure recovery jobs.

### How to keep room for future priority without using it now

- Include an optional priority field in the conceptual job record.
- Default it to normal.
- Ignore it in version one.

### Future multi-worker scaling

- The architecture should avoid assumptions that make more than one worker impossible later.
- Even if version one uses one worker, the queue claim semantics should be clean enough that a future `max_workers = 2` could be considered.

### What must remain explicit for future scaling

- Claim ownership.
- Lease expiration.
- Per-job isolation.
- Queue ordering semantics.
- Shared resource limits.

### Why not enable multiple heavy workers now

- The operational risk is high.
- Throughput benefit is unknown.
- Existing code and infrastructure have not yet been made queue-safe.
- Transparency and correctness are the primary goals of this redesign.

---

## Additional architecture aspect: health reporting and status surfaces

### Why health reporting matters

- A queue system without a quick health surface is hard to trust.
- Operators need a current-state view.
- Developers need to know whether the problem is:
- intake.
- queue.
- worker.
- NVR.
- upload.

### Minimal useful health outputs

- Last successful supervisor tick.
- Current worker status.
- Current queue depth.
- Oldest queued job age.
- Running job id.
- Last major error class.

### Possible status surfaces

- Human-readable status file.
- Logs.
- Optional command-line `status` mode later.
- Optional email alert summaries for developers.

### Recommendation

- At least one local current-state summary should exist.
- It should not require reading many rotated logs to understand the present.

---

## Testing

### Testing philosophy

- This architecture should be tested as a process system, not only as a library.
- Unit tests alone will not prove safety.
- Integration and failure-injection tests are essential.

### Unit test targets

- Response parsing.
- Response fingerprint generation.
- Stability detection logic.
- Queue record creation.
- State transitions.
- ETA calculation heuristics.
- Notification suppression logic.
- Stale lock detection.
- Heartbeat age classification.

### Integration test targets

- Supervisor imports valid `.resp` into queued jobs.
- Supervisor converts malformed `.resp` into parse-failure artifacts.
- Supervisor sends registration emails only once.
- Worker claims queued jobs and updates state correctly.
- Worker success flow sends final success email.
- Worker no-recordings flow sends correct outcome email.
- Worker failure flow sends failure email.
- Worker exits on empty queue.

### Process-behavior tests

- Two supervisor instances launched at the same time.
- One should work.
- The other should exit harmlessly.
- Supervisor launched while worker is healthy.
- No new worker should spawn.
- Supervisor launched while worker state is stale.
- The configured stale policy should occur.
- Supervisor launched while worker runtime exceeds 4 hours.
- It should verify worker identity, terminate the worker, reconcile queue state, and avoid duplicate replacement races.

### Restart tests

- Crash supervisor after durable job write but before inbound response removal.
- Verify no duplicate queue item after restart.
- Crash worker after claiming a job.
- Verify the job becomes stalled or otherwise reconciled according to policy.
- Kill worker during upload phase.
- Verify final state is diagnosable.
- Simulate PID reuse with stale worker-state metadata.
- Verify the supervisor refuses to kill a non-matching process.

### Scheduler-behavior tests

- Microsoft Task Scheduler launches supervisor every minute.
- Supervisor exits quickly.
- Worker continues.
- Next scheduled supervisor detects healthy worker and does not spawn another.

### File stability tests

- Partially written `.resp` should not be consumed too early.
- Stable `.resp` should be consumed promptly.
- Duplicate `.resp` content with same fingerprint should not create duplicate job intake.

### Notification tests

- Registration email sent once.
- Delayed email not sent for short jobs.
- Delayed email sent at threshold if enabled.
- Success email not duplicated after restart.
- Parse-failure email sent only if responder is recoverable.

### Logging tests

- Supervisor log clearly marks intake events.
- Worker log clearly marks execution events.
- Per-job log contains the right job id.
- Log rotation does not break active writing.

### Manual chaos tests

- Kill supervisor mid-import.
- Kill worker mid-download.
- Disconnect network during upload.
- Make NVR unreachable.
- Make OneDrive intake folder unavailable temporarily.
- Make email sending fail temporarily.

### Soak tests

- Long worker processing with multiple new `.resp` arrivals every minute.
- Ensure supervisor continues to register them while the worker is busy.
- Ensure queue depth remains correct.
- Ensure no second worker is spawned.

### Performance tests

- Many queued jobs.
- Many inbound `.resp` files at once.
- Long runtime history directory.
- Ensure supervisor stays fast enough.

### Operational runbook tests

- Simulate stale worker and follow documented manual recovery.
- Simulate duplicate inbound response and verify dedup.
- Simulate queue blockage and verify summary status is understandable.
- Simulate hard worker timeout after 4 hours and verify the logs clearly show identity verification, kill decision, kill result, and job reconciliation.

### Test environment recommendation

- Separate pure parsing tests from process-lifecycle tests.
- Use fake or mocked NVR interactions for most queue tests.
- Reserve end-to-end real NVR tests for a smaller validation set.

### Acceptance criteria for first production rollout

- New valid responses are registered while a worker is already busy.
- Only one worker processes heavy jobs.
- No duplicate worker appears during ordinary operation.
- Parse-failure and success notifications remain correct.
- Queue state is human-readable from files and logs.
- Operators can diagnose whether the system is healthy within a few minutes.

---

## Deployment and migration strategy

### Why migration needs care

- This architecture changes process lifecycle and queue semantics.
- A half-migrated system could double-process jobs or fail to spawn workers.
- Migration should therefore be staged.

### Suggested rollout stages

#### Stage 1: internal role separation

- Add internal supervisor and worker concepts.
- Keep behavior functionally similar to current system where practical.

#### Stage 2: durable intake queue

- Import `.resp` into durable local queue records.
- Keep only one heavy worker.

#### Stage 3: registration emails

- Add queue-registration notification after successful parse.
- Observe user experience and log volume.

#### Stage 4: stale-worker handling and richer logs

- Add heartbeat, summary status, and stronger operational recovery.

#### Stage 5: optional delayed emails or richer ETA

- Add only after the basic system proves stable.

### Backout plan

- Keep the current heavy processing core reusable.
- If the new queue layer misbehaves, it should be possible to disable the supervisor/worker split temporarily and return to a simpler direct mode.
- The architecture should make rollback conceptually possible even if the actual code changes are non-trivial.

### Data migration concerns

- Existing `.yml` artifacts may have a different meaning than the new job records.
- The migration should define whether old artifacts are ignored, upgraded, or archived.

### Operator documentation requirement

- Update README or deployment docs so operators understand:
- one time-triggered Supervisor task and one on-demand Worker task.
- one entrypoint.
- internal worker lifecycle.
- locations of queue state and logs.
- safe manual recovery steps.

---

## Open questions and architecture alternatives

### Open question: file queue or SQLite

- File queue is likely best for the first version.
- SQLite may become attractive later for metrics and richer state queries.
- Decision should balance:
- inspectability.
- complexity.
- migration cost.

### Open question: should `.yml` be command-only or full job record

- Full job record is more powerful.
- Command-only is closer to today.
- The final choice should reflect how much refactor the team wants to absorb in one step.

### Open question: should delayed emails exist in version one

- Benefits:
- Better transparency.
- Drawbacks:
- More notification logic.
- More possibility of spam or misleading updates.
- Registration plus final result may already solve most of the current user pain.

### Open question: should worker auto-kill be enabled

- This question is partially resolved for version one.
- A narrow auto-kill rule is enabled:
- terminate a worker that exceeds 4 hours of total wall-clock runtime after identity verification.
- Remaining open aspects are:
- whether the 4-hour threshold is correct.
- whether heartbeat-only stale conditions below that threshold should ever trigger automatic termination.
- whether future designs should move to a more granular per-job timeout model.

### Open question: should a per-job runner subprocess exist

- Benefits:
- Better isolation.
- Easier hard timeout.
- Drawbacks:
- More complexity.
- More moving parts.

### Open question: should the worker fail jobs fast when NVR is down

- Option 1:
- Fail each job quickly.
- Option 2:
- Pause the queue and keep jobs queued.
- Option 3:
- Retry with backoff then fail.
- Product expectations and support workload should drive this choice.

### Open question: should the worker drain the queue or process one job then exit

- Drain-until-empty is the recommended balance.
- One-job worker is simpler to reason about but less efficient.

### Open question: should Task Scheduler permit overlapping supervisor launches

- Letting the app self-serialize is more robust.
- Blocking overlap at the scheduler level is simpler to understand.
- The final choice should be documented explicitly and not left implicit.

### Open question: should raw `.resp` be deleted or locally archived

- Deletion is simpler.
- Local archival is better for forensics.
- If raw response text is already embedded in the durable job record, deletion may be sufficient.

### Open question: queue prioritization

- FIFO is recommended.
- Special-priority handling may be requested later.
- It should not be silently introduced without revisiting ETA and fairness.

### Open question: developer alert strategy

- Which failures deserve immediate developer email.
- Which failures should only appear in logs.
- Repeated identical alerts should probably be rate-limited.

---

## Recommended initial architecture summary

- Keep one time-triggered Microsoft Task Scheduler task plus one on-demand Worker task.
- Keep one operational entrypoint.
- Default entrypoint role is supervisor.
- Supervisor runs every minute and exits quickly.
- Supervisor owns inbound `.resp` discovery, parse, durable queue import, registration email, and Worker task trigger decisions.
- Worker is an internal role launched as its own on-demand Task Scheduler task.
- Worker processes queued jobs sequentially, one job per task run.
- Queue state lives locally, not in OneDrive.
- `.resp` is intake source only.
- Durable `.yml` job records or equivalent local artifacts become the queue source of truth.
- Task Scheduler task settings plus internal queue/runtime state control concurrency.
- Worker state includes PID, `started_at`, worker instance id, and heartbeat.
- Later supervisor runs under the same Windows account may terminate an overlong worker after verifying those identity fields.
- Version one uses a hard worker wall-clock timeout of 4 hours.
- Separate supervisor and worker logs improve human readability.
- Per-job logging is highly desirable.
- Parse failure, execution failure, and no-recordings outcomes remain distinct.
- Registration/queued email is the new user-facing addition.
- The intended user-visible email journey is:
- Forms confirmation.
- Parse failure or HikLoad registration with ETA.
- Final failure or final success.
- ETA should be approximate and conservative.
- Auto-kill in version one is intentionally narrow and applies only to the 4-hour hard worker runtime limit.

---

## Final design principles to preserve during implementation

- Do not let the scheduled entrypoint become heavy again.
- Do not treat OneDrive as a transactional queue.
- Do not rely on parent process lifetime for correctness.
- Do not rely on Task Scheduler alone for concurrency safety.
- Do not mix intake and heavy execution concerns casually.
- Do not hide queue state only in logs.
- Do not spam users with progress noise.
- Do not make recovery depend on tribal knowledge.
- Prefer explicit durable state over inferred state.
- Prefer inspectability over cleverness.
- Prefer conservative failure handling over optimistic duplicate work.

## Implementation note to future maintainers

- If later pressure arises to "just make it parallel", revisit this document first.
- Most of the risk in this redesign is not in spawning an extra process.
- The risk is in state ownership, lifecycle coordination, and failure semantics.
- If those remain clean, the architecture can evolve safely.
- If those become muddled, the system will become harder to trust than the simpler single-script design it replaces.
