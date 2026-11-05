# QNX Neutrino RTOS 8.0: Technical Reference & IPC Architecture

## 1. Microkernel IPC Primitives

QNX Neutrino RTOS is built around a true microkernel architecture (`procnto-smp-instr`). All inter-process communication relies on synchronous message passing:

### MsgSend / MsgReceive / MsgReply Lifecycle
- **`MsgSendv(coid, smsg, sparts, rmsg, rparts)`**:
  - The sending thread blocks in `STATE_SEND` until the receiving thread executes `MsgReceive()`.
  - Once received, the sender transitions to `STATE_REPLY` while the receiver processes the message.
  - The sender unblocks only when `MsgReply()` is called.
- **`MsgReceivev(chid, rmsg, rparts, info)`**:
  - The receiving thread blocks in `STATE_RECEIVE` until a message arrives on the specified channel (`chid`).
- **`MsgReplyv(rcvid, status, rmsg, rparts)`**:
  - Unblocks the client thread waiting in `STATE_REPLY`.

### Native Circular Deadlock Vulnerability
If Process A calls `MsgSend` to Process B, and Process B simultaneously calls `MsgSend` to Process C, which in turn calls `MsgSend` to Process A:
- A is in `STATE_SEND` (or `STATE_REPLY`) waiting for B.
- B is in `STATE_SEND` (or `STATE_REPLY`) waiting for C.
- C is in `STATE_SEND` (or `STATE_REPLY`) waiting for A.
- **Result:** Permanent microkernel deadlock. Neither thread can yield or proceed without external kernel intervention or slaying.

---

## 2. Kernel Telemetry & `/proc` Filesystem

### `devctl` Inspection Commands
QNX Neutrino provides microkernel process and thread introspection through `/proc/<pid>/as`:

```c
#include <sys/procfs.h>
#include <devctl.h>

// Open process address space
int fd = open("/proc/<pid>/as", O_RDONLY);

// Retrieve process information
procfs_info pinfo;
devctl(fd, DCMD_PROC_INFO, &pinfo, sizeof(pinfo), NULL);

// Retrieve thread execution status
procfs_status status;
status.tid = 1;
devctl(fd, DCMD_PROC_STATUS, &status, sizeof(status), NULL);
```

### Thread Execution States
| State ID | Constant | Description |
|:---:|:---|:---|
| 1 | `STATE_DEAD` | Thread has terminated |
| 2 | `STATE_RUNNING` | Thread is executing on a CPU core |
| 3 | `STATE_READY` | Thread is queued for execution |
| 4 | `STATE_STOPPED` | Thread is paused by debugger |
| 5 | `STATE_SEND` | Blocked waiting on `MsgSend` |
| 6 | `STATE_RECEIVE` | Blocked waiting on `MsgReceive` |
| 7 | `STATE_REPLY` | Blocked waiting on `MsgReply` |
| 8 | `STATE_WAITPAGE` | Blocked on memory page allocation |
| 9 | `STATE_MUTEX` | Blocked waiting for a `pthread_mutex` |
| 10 | `STATE_CONDVAR` | Blocked waiting for a condition variable |
| 11 | `STATE_SEM` | Blocked waiting on a semaphore |

---

## 3. System Logger 2 (slog2)

QNX 8.0 uses `slog2` for high-performance, ring-buffered system logging:

- **Buffer Allocation:** Independent per-process ring buffers configured via `slog2_register_t`.
- **Log Severities:**
  - `SLOG2_SHUTDOWN` (0): Critical system failure requiring immediate shutdown.
  - `SLOG2_CRITICAL` (1): Component-level unrecoverable fault.
  - `SLOG2_ERROR` (2): Operational error.
  - `SLOG2_WARNING` (3): Non-fatal anomaly.
  - `SLOG2_NOTICE` (4): Significant normal event.
  - `SLOG2_INFO` (5): Informational message.
  - `SLOG2_DEBUG1`..`DEBUG2` (6-7): Debug trace telemetry.
- **Log Retrieval:** `slog2_find_entity()` and `slog2_read_next_item()` over `/dev/slog2`.
