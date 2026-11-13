/**
 * ============================================================================
 * QNX-Aegis Demo: Native QNX 8.0 Microkernel IPC Hardware Benchmark
 * File: ipc_benchmark_demo.c
 * Purpose: Measures 1,000 synchronous MsgSend() -> MsgReceive() -> MsgReply()
 *          64-byte round-trips using hardware ClockCycles() and syspage
 *          qtime->cycles_per_sec, logs to slog2, and prints single-line JSON.
 * ============================================================================
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <inttypes.h>
#include <unistd.h>
#include <pthread.h>

#ifdef __QNXNTO__
  #include <sys/neutrino.h>
  #include <sys/syspage.h>
  #include <sys/slog2.h>
#endif

#define BENCH_ITERATIONS 1000
#define BENCH_PAYLOAD_BYTES 64
#define BENCH_SLOG2_NAME "qnx_aegis_bench"

typedef struct {
    int chid;
    pthread_barrier_t ready_barrier;
} bench_ctx_t;

static void* bench_server_thread(void *arg)
{
    bench_ctx_t *ctx = (bench_ctx_t *)arg;

#ifdef __QNXNTO__
    ctx->chid = ChannelCreate(0);
    pthread_barrier_wait(&ctx->ready_barrier);

    if (ctx->chid < 0) {
        return NULL;
    }

    uint8_t recv_buf[BENCH_PAYLOAD_BYTES];
    uint8_t reply_buf[BENCH_PAYLOAD_BYTES];
    memset(reply_buf, 0xA5, sizeof(reply_buf));

    int handled = 0;
    while (handled < BENCH_ITERATIONS) {
        struct _msg_info info;
        int rcvid = MsgReceive(ctx->chid, recv_buf, sizeof(recv_buf), &info);
        if (rcvid < 0) {
            break;
        }
        if (rcvid == 0) {
            /* Ignore asynchronous pulses */
            continue;
        }
        memcpy(reply_buf, recv_buf, sizeof(reply_buf));
        MsgReply(rcvid, 0, reply_buf, sizeof(reply_buf));
        handled++;
    }
#else
    ctx->chid = 1;
    pthread_barrier_wait(&ctx->ready_barrier);
#endif

    return NULL;
}

int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;

#ifdef __QNXNTO__
    bench_ctx_t ctx;
    ctx.chid = -1;
    pthread_barrier_init(&ctx.ready_barrier, NULL, 2);

    pthread_t srv_tid;
    if (pthread_create(&srv_tid, NULL, bench_server_thread, &ctx) != 0) {
        fprintf(stderr, "{\"error\":\"pthread_create failed\"}\n");
        pthread_barrier_destroy(&ctx.ready_barrier);
        return 1;
    }

    /* Wait for server thread to create its private channel */
    pthread_barrier_wait(&ctx.ready_barrier);
    if (ctx.chid < 0) {
        fprintf(stderr, "{\"error\":\"ChannelCreate failed\"}\n");
        pthread_join(srv_tid, NULL);
        pthread_barrier_destroy(&ctx.ready_barrier);
        return 1;
    }

    int coid = ConnectAttach(0, 0, ctx.chid, _NTO_SIDE_CHANNEL, 0);
    if (coid < 0) {
        fprintf(stderr, "{\"error\":\"ConnectAttach failed\"}\n");
        ChannelDestroy(ctx.chid);
        pthread_join(srv_tid, NULL);
        pthread_barrier_destroy(&ctx.ready_barrier);
        return 1;
    }

    uint8_t send_buf[BENCH_PAYLOAD_BYTES];
    uint8_t rmsg_buf[BENCH_PAYLOAD_BYTES];
    memset(send_buf, 0x5A, sizeof(send_buf));
    memset(rmsg_buf, 0, sizeof(rmsg_buf));

    uint64_t cycles_per_sec = SYSPAGE_ENTRY(qtime)->cycles_per_sec;
    if (cycles_per_sec == 0) {
        cycles_per_sec = 1000000000ULL;
    }

    uint64_t min_cycles = UINT64_MAX;
    uint64_t max_cycles = 0;
    uint64_t total_cycles = 0;

    for (int i = 0; i < BENCH_ITERATIONS; i++) {
        uint32_t seq = (uint32_t)i;
        memcpy(send_buf, &seq, sizeof(seq));

        uint64_t start_cyc = ClockCycles();
        int rc = MsgSend(coid, send_buf, sizeof(send_buf), rmsg_buf, sizeof(rmsg_buf));
        uint64_t end_cyc = ClockCycles();

        if (rc != 0) {
            fprintf(stderr, "{\"error\":\"MsgSend failed at iteration %d\"}\n", i);
            ConnectDetach(coid);
            ChannelDestroy(ctx.chid);
            pthread_join(srv_tid, NULL);
            pthread_barrier_destroy(&ctx.ready_barrier);
            return 1;
        }

        uint64_t delta = (end_cyc >= start_cyc) ? (end_cyc - start_cyc) : 0ULL;
        if (delta < min_cycles) {
            min_cycles = delta;
        }
        if (delta > max_cycles) {
            max_cycles = delta;
        }
        total_cycles += delta;
    }

    ConnectDetach(coid);
    pthread_join(srv_tid, NULL);
    ChannelDestroy(ctx.chid);
    pthread_barrier_destroy(&ctx.ready_barrier);

    double avg_cycles = (double)total_cycles / (double)BENCH_ITERATIONS;
    double avg_latency_ns = (avg_cycles * 1e9) / (double)cycles_per_sec;
    double total_sec = (double)total_cycles / (double)cycles_per_sec;
    double throughput_msg_sec = (total_sec > 0.0) ? ((double)BENCH_ITERATIONS / total_sec) : 0.0;

    /* Emit QNX 8.0 slog2 summary */
    slog2_buffer_set_config_t s2cfg;
    slog2_buffer_t s2handle[1];
    memset(&s2cfg, 0, sizeof(s2cfg));
    s2cfg.buffer_set_name = BENCH_SLOG2_NAME;
    s2cfg.num_buffers = 1;
    s2cfg.verbosity_level = SLOG2_INFO;
    s2cfg.buffer_config[0].buffer_name = "ipc_bench";
    s2cfg.buffer_config[0].num_pages = 1;

    if (slog2_register(&s2cfg, s2handle, 0) == 0) {
        slog2f(
            s2handle[0],
            0,
            SLOG2_INFO,
            "IPC Benchmark: %d round-trips (%dB) | avg=%.2f ns | min=%" PRIu64 " avg=%.1f max=%" PRIu64 " cycles | %.1f msg/s",
            BENCH_ITERATIONS,
            BENCH_PAYLOAD_BYTES,
            avg_latency_ns,
            min_cycles,
            avg_cycles,
            max_cycles,
            throughput_msg_sec
        );
    }

    /* Clean single-line JSON output to stdout */
    printf(
        "{\"benchmark\":\"qnx_ipc_roundtrip\",\"iterations\":%d,\"payload_bytes\":%d,"
        "\"cycles_per_sec\":%" PRIu64 ",\"min_cycles\":%" PRIu64 ",\"avg_cycles\":%.2f,"
        "\"max_cycles\":%" PRIu64 ",\"avg_latency_ns\":%.2f,\"throughput_msg_sec\":%.2f}\n",
        BENCH_ITERATIONS,
        BENCH_PAYLOAD_BYTES,
        cycles_per_sec,
        min_cycles,
        avg_cycles,
        max_cycles,
        avg_latency_ns,
        throughput_msg_sec
    );
    fflush(stdout);
    return 0;
#else
    fprintf(stderr, "{\"error\":\"ipc_benchmark_demo requires QNX Neutrino RTOS (__QNXNTO__)\"}\n");
    return 1;
#endif
}
