/**
 * ============================================================================
 * QNX-Aegis: Microkernel IPC Analyzer
 * File: ipc_analyzer.c
 * Purpose: Queries QNX 8.0 /proc devctl interfaces to capture thread states,
 *          channel associations, and formats the snapshot into structured JSON.
 * ============================================================================
 */

#include "qnx_inspector.h"

const char* qnx_state_to_string(int state)
{
    switch (state) {
        case QNX_STATE_DEAD:      return "DEAD";
        case QNX_STATE_RUNNING:   return "RUNNING";
        case QNX_STATE_READY:     return "READY";
        case QNX_STATE_STOPPED:   return "STOPPED";
        case QNX_STATE_SEND:      return "SEND";
        case QNX_STATE_RECEIVE:   return "RECEIVE";
        case QNX_STATE_REPLY:     return "REPLY";
        case QNX_STATE_WAITPAGE:  return "WAITPAGE";
        case QNX_STATE_STACK:     return "STACK";
        case QNX_STATE_MUTEX:     return "MUTEX";
        case QNX_STATE_CONDVAR:   return "CONDVAR";
        case QNX_STATE_SEM:       return "SEM";
        case QNX_STATE_INTR:      return "INTR";
        case QNX_STATE_NET_SEND:  return "NET_SEND";
        case QNX_STATE_NET_REPLY: return "NET_REPLY";
        default:                  return "UNKNOWN";
    }
}

#ifdef __QNXNTO__
int analyze_qnx_ipc(qnx_ipc_snapshot_t *snapshot)
{
    DIR *dir;
    struct dirent *entry;
    struct timespec ts;
    int thread_count = 0;
    int proc_count = 0;

    /* 1. Capture system info */
    snprintf(snapshot->system_info.os_name, sizeof(snapshot->system_info.os_name), "%s", "QNX Neutrino RTOS 8.0");
    snprintf(snapshot->system_info.architecture, sizeof(snapshot->system_info.architecture), "%s", "x86_64");
    if (gethostname(snapshot->system_info.hostname, sizeof(snapshot->system_info.hostname) - 1) != 0) {
        snprintf(snapshot->system_info.hostname, sizeof(snapshot->system_info.hostname), "%s", "localhost-qnx");
    }

    if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0) {
        snapshot->system_info.uptime_sec = (uint64_t)ts.tv_sec;
    } else {
        snapshot->system_info.uptime_sec = 0;
    }

    /* 2. Traverse /proc directory and query kernel devctl for active processes */
    dir = opendir("/proc");
    if (!dir) {
        return -1;
    }

    while ((entry = readdir(dir)) != NULL && thread_count < MAX_THREADS_SNAPSHOT) {
        /* Filter out non-numeric entries like /proc/mount, /proc/boot */
        char *endptr;
        long pid_val = strtol(entry->d_name, &endptr, 10);
        if (*endptr != '\0' || pid_val <= 0) {
            continue;
        }

        proc_count++;
        char proc_path[64];
        snprintf(proc_path, sizeof(proc_path), "/proc/%ld/as", pid_val);

        int fd = open(proc_path, O_RDONLY);
        if (fd < 0) {
            continue;
        }

        /* Query general process info using DCMD_PROC_INFO */
        procfs_info pinfo;
        if (devctl(fd, DCMD_PROC_INFO, &pinfo, sizeof(pinfo), NULL) == EOK) {
            uint32_t num_threads = pinfo.num_threads;

            /* Retrieve process binary name using DCMD_PROC_MAPDEBUG_BASE */
            struct {
                procfs_debuginfo info;
                char buff[PATH_MAX];
            } dbg;
            memset(&dbg, 0, sizeof(dbg));

            char proc_name[MAX_NAME_LEN];
            snprintf(proc_name, sizeof(proc_name), "proc_%ld", pid_val);

            if (devctl(fd, DCMD_PROC_MAPDEBUG_BASE, &dbg, sizeof(dbg), NULL) == EOK) {
                const char *path_ptr = (const char *)dbg.info.path;
                char *base = strrchr(path_ptr, '/');
                if (base != NULL && *(base + 1) != '\0') {
                    snprintf(proc_name, sizeof(proc_name), "%s", base + 1);
                } else if (path_ptr[0] != '\0') {
                    snprintf(proc_name, sizeof(proc_name), "%s", path_ptr);
                }
            }

            /* Query thread status for each active thread using DCMD_PROC_TIDSTATUS */
            for (uint32_t tid = 1; tid <= num_threads && thread_count < MAX_THREADS_SNAPSHOT; tid++) {
                procfs_status status;
                memset(&status, 0, sizeof(status));
                status.tid = (pthread_t)tid;

                if (devctl(fd, DCMD_PROC_TIDSTATUS, &status, sizeof(status), NULL) == EOK ||
                    devctl(fd, DCMD_PROC_STATUS, &status, sizeof(status), NULL) == EOK) {
                    qnx_thread_info_t *t = &snapshot->threads[thread_count];
                    t->pid = (pid_t)pid_val;
                    t->tid = (status.tid > 0) ? (int)status.tid : (int)tid;
                    snprintf(t->name, sizeof(t->name), "%s", proc_name);
                    t->state = (qnx_thread_state_t)status.state;
                    snprintf(t->state_str, sizeof(t->state_str), "%s", qnx_state_to_string(status.state));
                    t->priority = (int)status.priority;
                    t->chid = -1;
                    t->coid = -1;
                    t->target_pid = 0;
                    t->target_tid = 0;
                    t->is_deadlocked = 0;

                    thread_count++;
                }
            }
        }
        close(fd);
    }
    closedir(dir);

    /* 3. Enrich / supplement thread blocking targets via QNX pidin */
    FILE *pfp = popen("pidin -F \"%a %b %N %J %B\" 2>/dev/null", "r");
    if (pfp != NULL) {
        char pline[256];
        /* Skip header line */
        if (fgets(pline, sizeof(pline), pfp) != NULL) {
            while (fgets(pline, sizeof(pline), pfp) != NULL) {
                long p_pid = 0, p_tid = 0;
                char p_name[MAX_NAME_LEN] = {0};
                char p_state[32] = {0};
                char p_blocked[64] = {0};

                if (sscanf(pline, "%ld %ld %63s %31s %63s", &p_pid, &p_tid, p_name, p_state, p_blocked) >= 4) {
                    for (int i = 0; i < thread_count; i++) {
                        if (snapshot->threads[i].pid == (pid_t)p_pid && snapshot->threads[i].tid == (int)p_tid) {
                            if (p_name[0] != '\0' && strstr(snapshot->threads[i].name, "proc_") != NULL) {
                                snprintf(snapshot->threads[i].name, sizeof(snapshot->threads[i].name), "%s", p_name);
                            }

                            /* Ensure state and state_str reflect REPLY/SEND/RECEIVE reliably */
                            if (strcasecmp(p_state, "Reply") == 0) {
                                snapshot->threads[i].state = QNX_STATE_REPLY;
                                snprintf(snapshot->threads[i].state_str, sizeof(snapshot->threads[i].state_str), "REPLY");
                            } else if (strcasecmp(p_state, "Send") == 0) {
                                snapshot->threads[i].state = QNX_STATE_SEND;
                                snprintf(snapshot->threads[i].state_str, sizeof(snapshot->threads[i].state_str), "SEND");
                            } else if (strcasecmp(p_state, "Receive") == 0) {
                                snapshot->threads[i].state = QNX_STATE_RECEIVE;
                                snprintf(snapshot->threads[i].state_str, sizeof(snapshot->threads[i].state_str), "RECEIVE");
                            }

                            if (p_blocked[0] != '\0' && strcmp(p_blocked, "-") != 0) {
                                long target_val = strtol(p_blocked, NULL, 10);
                                if (strcasecmp(p_state, "Receive") == 0) {
                                    snapshot->threads[i].chid = (int)target_val;
                                } else if (strcasecmp(p_state, "Reply") == 0 || strcasecmp(p_state, "Send") == 0) {
                                    snapshot->threads[i].target_pid = (pid_t)target_val;
                                    snapshot->threads[i].coid = (int)target_val;
                                }
                            }
                            break;
                        }
                    }
                }
            }
        }
        pclose(pfp);
    }

    snapshot->system_info.num_processes = proc_count;
    snapshot->system_info.num_threads = thread_count;
    snapshot->thread_count = thread_count;

    return 0;
}
#else
/* Simulation mode for host builds / linting */
int analyze_qnx_ipc(qnx_ipc_snapshot_t *snapshot)
{
    strncpy(snapshot->system_info.os_name, "QNX Neutrino RTOS 8.0 (Simulated)", sizeof(snapshot->system_info.os_name) - 1);
    strncpy(snapshot->system_info.architecture, "x86_64", sizeof(snapshot->system_info.architecture) - 1);
    strncpy(snapshot->system_info.hostname, "qnx-host-sim", sizeof(snapshot->system_info.hostname) - 1);
    snapshot->system_info.uptime_sec = 1420;
    snapshot->system_info.num_processes = 5;
    snapshot->system_info.num_threads = 6;
    snapshot->system_info.deadlocks_detected = 0;

    int idx = 0;
    /* Simulated procnto */
    snapshot->threads[idx].pid = 1;
    snapshot->threads[idx].tid = 1;
    strncpy(snapshot->threads[idx].name, "procnto-smp-instr", MAX_NAME_LEN - 1);
    snapshot->threads[idx].state = QNX_STATE_READY;
    strncpy(snapshot->threads[idx].state_str, "READY", 15);
    snapshot->threads[idx].priority = 255;
    idx++;

    /* Simulated inspector daemon */
    snapshot->threads[idx].pid = 4102;
    snapshot->threads[idx].tid = 1;
    strncpy(snapshot->threads[idx].name, "qnx_inspector", MAX_NAME_LEN - 1);
    snapshot->threads[idx].state = QNX_STATE_RECEIVE;
    strncpy(snapshot->threads[idx].state_str, "RECEIVE", 15);
    snapshot->threads[idx].chid = 1;
    snapshot->threads[idx].priority = 10;
    idx++;

    /* Simulated worker 1 */
    snapshot->threads[idx].pid = 8201;
    snapshot->threads[idx].tid = 1;
    strncpy(snapshot->threads[idx].name, "actor_alpha", MAX_NAME_LEN - 1);
    snapshot->threads[idx].state = QNX_STATE_RUNNING;
    strncpy(snapshot->threads[idx].state_str, "RUNNING", 15);
    snapshot->threads[idx].priority = 12;
    idx++;

    snapshot->thread_count = idx;
    return 0;
}
#endif

int format_snapshot_json(const qnx_ipc_snapshot_t *snapshot, char *json_buf, size_t max_len)
{
    int offset = 0;

    offset += snprintf(json_buf + offset, max_len - offset,
        "{\n"
        "  \"version\": \"%s\",\n"
        "  \"system\": {\n"
        "    \"os\": \"%s\",\n"
        "    \"architecture\": \"%s\",\n"
        "    \"hostname\": \"%s\",\n"
        "    \"uptime_sec\": %llu,\n"
        "    \"num_processes\": %d,\n"
        "    \"num_threads\": %d,\n"
        "    \"deadlocks_detected\": %d\n"
        "  },\n"
        "  \"threads\": [\n",
        QNX_INSPECTOR_VERSION,
        snapshot->system_info.os_name,
        snapshot->system_info.architecture,
        snapshot->system_info.hostname,
        (unsigned long long)snapshot->system_info.uptime_sec,
        snapshot->system_info.num_processes,
        snapshot->system_info.num_threads,
        snapshot->system_info.deadlocks_detected
    );

    for (int i = 0; i < snapshot->thread_count && offset < (int)max_len - 512; i++) {
        const qnx_thread_info_t *t = &snapshot->threads[i];
        offset += snprintf(json_buf + offset, max_len - offset,
            "    {\n"
            "      \"pid\": %ld,\n"
            "      \"tid\": %d,\n"
            "      \"name\": \"%s\",\n"
            "      \"state\": \"%s\",\n"
            "      \"priority\": %d,\n"
            "      \"chid\": %d,\n"
            "      \"coid\": %d,\n"
            "      \"target_pid\": %ld,\n"
            "      \"target_tid\": %d,\n"
            "      \"is_deadlocked\": %s\n"
            "    }%s\n",
            (long)t->pid,
            t->tid,
            t->name,
            t->state_str,
            t->priority,
            t->chid,
            t->coid,
            (long)t->target_pid,
            t->target_tid,
            t->is_deadlocked ? "true" : "false",
            (i < snapshot->thread_count - 1) ? "," : ""
        );
    }

    offset += snprintf(json_buf + offset, max_len - offset,
        "  ],\n"
        "  \"slog2_tail\": \"%s\"\n"
        "}\n",
        (snapshot->slog2_buffer[0] != '\0') ? snapshot->slog2_buffer : "No recent slog2 entries"
    );

    return offset;
}
