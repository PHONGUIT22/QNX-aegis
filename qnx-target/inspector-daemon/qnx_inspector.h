/**
 * ============================================================================
 * QNX-Aegis: QNX 8.0 Resource Manager & Microkernel Inspector
 * Header: Shared Data Structures, IPC States, and Prototypes
 * ============================================================================
 */

#ifndef QNX_INSPECTOR_H
#define QNX_INSPECTOR_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <errno.h>
#include <limits.h>

#ifdef __QNXNTO__
  #include <sys/iofunc.h>
  #include <sys/dispatch.h>
  #include <sys/neutrino.h>
  #include <sys/resmgr.h>
  #include <sys/slog2.h>
  #include <sys/procfs.h>
  #include <sys/syspage.h>
  #include <devctl.h>
  #include <unistd.h>
  #include <fcntl.h>
  #include <dirent.h>
#else
  /* Host / IDE development fallback stubs */
  typedef void* dispatch_t;
  typedef void* dispatch_context_t;
  typedef struct { int flags; } resmgr_attr_t;
  typedef struct { int mode; } iofunc_attr_t;
  typedef struct { int dummy; } resmgr_connect_funcs_t;
  typedef struct { int dummy; int (*read)(void*, void*, void*); } resmgr_io_funcs_t;
  #define _RESMGR_CONNECT_NFUNCS 1
  #define _RESMGR_IO_NFUNCS 1
  #define _FTYPE_ANY 0
  #define _RESMGR_DEFAULT 0
  #define _IO_SET_READ_NBYTES(ctp, n) ((void)0)
#endif

#define QNX_INSPECTOR_VERSION "1.0.0"
#define QNX_INSPECTOR_DEVICE  "/dev/qnx_inspector"
#define MAX_THREADS_SNAPSHOT  256
#define MAX_JSON_BUFFER_SIZE  (128 * 1024)
#define MAX_NAME_LEN          64
#define MAX_MSG_LEN           256

/**
 * QNX Neutrino RTOS Thread States (corresponds to kernel STATE_* defines)
 */
typedef enum {
    QNX_STATE_DEAD        = 0,
    QNX_STATE_RUNNING     = 1,
    QNX_STATE_READY       = 2,
    QNX_STATE_STOPPED     = 3,
    QNX_STATE_SEND        = 4,  /* Blocked on MsgSend waiting for MsgReceive */
    QNX_STATE_RECEIVE     = 5,  /* Blocked on MsgReceive waiting for client */
    QNX_STATE_REPLY       = 6,  /* Blocked on MsgSend waiting for MsgReply */
    QNX_STATE_WAITPAGE    = 7,
    QNX_STATE_STACK       = 8,
    QNX_STATE_MUTEX       = 9,
    QNX_STATE_CONDVAR     = 10,
    QNX_STATE_SEM         = 11,
    QNX_STATE_INTR        = 12,
    QNX_STATE_NET_SEND    = 13,
    QNX_STATE_NET_REPLY   = 14,
    QNX_STATE_UNKNOWN     = 99
} qnx_thread_state_t;

/**
 * Structure representing an inspected QNX Thread and its IPC associations
 */
typedef struct {
    pid_t              pid;
    int                tid;
    char               name[MAX_NAME_LEN];
    qnx_thread_state_t state;
    char               state_str[16];
    int                priority;
    int                chid;          /* Channel ID thread is receiving on (if RECEIVE) */
    int                coid;          /* Connection ID thread is sending on (if SEND/REPLY) */
    pid_t              target_pid;    /* Target PID thread is blocked on (if known) */
    int                target_tid;    /* Target TID thread is blocked on (if known) */
    int                is_deadlocked; /* Flagged if involved in circular wait */
} qnx_thread_info_t;

/**
 * High-level QNX 8.0 System Information
 */
typedef struct {
    char     os_name[32];
    char     architecture[16];
    char     hostname[64];
    uint64_t uptime_sec;
    int      num_processes;
    int      num_threads;
    int      deadlocks_detected;
} qnx_system_info_t;

/**
 * Snapshot of QNX microkernel state captured for JSON output
 */
typedef struct {
    qnx_system_info_t system_info;
    qnx_thread_info_t threads[MAX_THREADS_SNAPSHOT];
    int               thread_count;
    char              slog2_buffer[4096];
} qnx_ipc_snapshot_t;

/* --- Function Prototypes --- */

/**
 * Query QNX 8.0 microkernel (/proc and syspage) to fill the IPC snapshot.
 */
int analyze_qnx_ipc(qnx_ipc_snapshot_t *snapshot);

/**
 * Read latest system log lines from QNX slog2 ring buffer.
 */
int read_recent_slog2(char *buffer, size_t max_len);

/**
 * Serialize the snapshot and slog2 entries into a clean JSON string.
 */
int format_snapshot_json(const qnx_ipc_snapshot_t *snapshot, char *json_buf, size_t max_len);

/**
 * Convert numeric QNX thread state to human-readable string.
 */
const char* qnx_state_to_string(int state);

#endif /* QNX_INSPECTOR_H */
