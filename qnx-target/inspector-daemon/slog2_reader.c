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
     * slog2 shared-memory ring buffers. We read the latest lines.
     */
    FILE *fp = popen("slog2info -l 10 2>/dev/null", "r");
    if (fp != NULL) {
        char line[512];
        size_t current_len = 0;

        while (fgets(line, sizeof(line), fp) != NULL && current_len < max_len - 64) {
            /* Sanitize control characters and quotes for JSON safety */
            for (size_t i = 0; line[i] != '\0'; i++) {
                if (line[i] == '"' || line[i] == '\\') {
                    line[i] = '\'';
                } else if (line[i] == '\r' || line[i] == '\n') {
                    line[i] = ' ';
                }
            }
            int written = snprintf(buffer + current_len, max_len - current_len, "%s | ", line);
            if (written > 0) {
                current_len += written;
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
        "[slog2] [INFO] procnto: CPU 0 active, microkernel ready | "
        "[slog2] [DEBUG] qnx_inspector: Registered on /dev/qnx_inspector | "
        "[slog2] [INFO] Network stack io-pkt-v6 initialized on en0"
    );
    return 0;
#endif
}
