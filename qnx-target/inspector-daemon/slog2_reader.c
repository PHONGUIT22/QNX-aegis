/**
 * ============================================================================
 * QNX-Aegis: QNX slog2 Reader Interface
 * File: slog2_reader.c
 * Purpose: Queries QNX System Logger 2 (slog2) ring buffers and formats
 *          diagnostic log lines for the inspector telemetry payload.
 * ============================================================================
 */

#include "qnx_inspector.h"

int read_recent_slog2(char *buffer, size_t max_len)
{
    if (buffer == NULL || max_len == 0) {
        return -1;
    }

    buffer[0] = '\0';

#ifdef __QNXNTO__
    /*
     * On QNX Neutrino RTOS, slog2info extracts logs from the dynamic
     * slog2 shared-memory ring buffers. Combine with persistent fault log
     * and take the latest 20 lines (QNX slog2info does not take -l line count).
     */
    FILE *fp = popen("(slog2info 2>/dev/null; cat /tmp/qnx-aegis/slog2_fault.log 2>/dev/null) | tail -n 20", "r");
    if (fp != NULL) {
        char line[512];
        size_t current_len = 0;

        while (fgets(line, sizeof(line), fp) != NULL && current_len + 16 < max_len) {
            size_t len = strlen(line);
            while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) {
                line[--len] = '\0';
            }
            if (len == 0) {
                continue;
            }

            /* Sanitize quotes, backslashes, and control chars for valid JSON string output */
            for (size_t i = 0; i < len; i++) {
                unsigned char ch = (unsigned char)line[i];
                if (ch == '"') {
                    line[i] = '\'';
                } else if (ch == '\\') {
                    line[i] = '/';
                } else if (ch < 0x20) {
                    line[i] = ' ';
                }
            }

            const char *sep = (current_len > 0) ? "\\n" : "";
            int written = snprintf(buffer + current_len, max_len - current_len, "%s%s", sep, line);
            if (written > 0) {
                size_t step = (size_t)written;
                if (current_len + step >= max_len) {
                    current_len = max_len - 1;
                    break;
                }
                current_len += step;
            }
        }
        pclose(fp);

        if (current_len > 0) {
            return 0;
        }
    }

    /* Fallback if slog2 is clean or empty */
    strncpy(buffer, "[slog2] Normal operation. No critical kernel faults recorded.", max_len - 1);
    buffer[max_len - 1] = '\0';
    return 0;

#else
    /* Simulation data for host / mock environments */
    snprintf(buffer, max_len,
        "[slog2] [INFO] procnto: CPU 0 active, microkernel ready\\n"
        "[slog2] [DEBUG] qnx_inspector: Registered on /dev/qnx_inspector\\n"
        "[slog2] [INFO] Network stack io-pkt-v6 initialized on en0"
    );
    return 0;
#endif
}
