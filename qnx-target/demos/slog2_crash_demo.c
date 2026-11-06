/**
 * ============================================================================
 * QNX-Aegis Demo: QNX 8.0 slog2 Logging & Crash Telemetry
 * File: slog2_crash_demo.c
 * Purpose: Emits structured logs into QNX slog2 ring buffer, logs warnings,
 *          and triggers a fault for post-mortem AI diagnosis.
 * ============================================================================
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>

#ifdef __QNXNTO__
  #include <sys/slog2.h>
#endif

#define DEMO_SLOG2_BUFFER_NAME "qnx_aegis_demo"

int main(int argc, char **argv)
{
    int should_crash = 1;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--no-crash") == 0) {
            should_crash = 0;
        }
    }

    printf("============================================================\n");
    printf(" QNX-Aegis Demo: QNX 8.0 slog2 Logging & Fault Simulator    \n");
    printf("============================================================\n");

#ifdef __QNXNTO__
    slog2_buffer_set_config_t config;
    slog2_buffer_t buffer_handle[1];

    memset(&config, 0, sizeof(config));
    config.buffer_set_name = DEMO_SLOG2_BUFFER_NAME;
    config.num_buffers = 1;
    config.verbosity_level = SLOG2_DEBUG1;
    config.buffer_config[0].buffer_name = "diagnostics";
    config.buffer_config[0].num_pages = 2; /* 8KB ring buffer */

    if (slog2_register(&config, buffer_handle, 0) == -1) {
        fprintf(stderr, "[!] slog2_register failed. Slogger2 daemon running?\n");
    } else {
        printf("[+] Registered slog2 buffer set: %s\n", DEMO_SLOG2_BUFFER_NAME);

        slog2f(buffer_handle[0], 0, SLOG2_INFO, "Subsystem initialized successfully. PID=%d", getpid());
        printf(" -> Logged SLOG2_INFO\n");
        usleep(200000);

        slog2f(buffer_handle[0], 0, SLOG2_WARNING, "Channel communication latency: 42ms (threshold: 20ms)");
        printf(" -> Logged SLOG2_WARNING\n");
        usleep(200000);

        slog2f(buffer_handle[0], 0, SLOG2_ERROR, "Invalid packet header received on side-channel coid 4");
        printf(" -> Logged SLOG2_ERROR\n");
        usleep(200000);

        slog2f(buffer_handle[0], 0, SLOG2_CRITICAL, "CRITICAL: Segmentation fault imminent. Memory check failed at 0xDEADBEEF");
        printf(" -> Logged SLOG2_CRITICAL\n");
    }
#else
    printf("[*] Simulated slog2: Emitted INFO, WARNING, ERROR, and CRITICAL1 messages.\n");
#endif

    if (should_crash) {
        printf("\n[!] Triggering deliberate fault (SIGSEGV) for telemetry verification...\n");
        fflush(stdout);
        usleep(100000);
        /* Raise SIGSEGV to simulate unhandled exception */
        raise(SIGSEGV);
    } else {
        printf("\n[*] Running with --no-crash. Exiting gracefully.\n");
    }

    return 0;
}
