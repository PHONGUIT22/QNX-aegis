/**
 * ============================================================================
 * QNX-Aegis Demo: Healthy QNX Microkernel IPC Transaction Loop
 * File: ipc_healthy_demo.c
 * Purpose: Demonstrates a properly synchronized client-server architecture
 *          using ChannelCreate, ConnectAttach, MsgSend, MsgReceive, and MsgReply.
 * ============================================================================
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>

#ifdef __QNXNTO__
  #include <sys/neutrino.h>
  #include <sys/netmgr.h>
#else
  #define ChannelCreate(x) (1)
  #define ConnectAttach(a,b,c,d,e) (1)
  #define MsgSend(a,b,c,d,e) (0)
  #define MsgReceive(a,b,c,d) (1)
  #define MsgReply(a,b,c,d) (0)
  #define _NTO_SIDE_CHANNEL 0
  struct _msg_info { int dummy; };
#endif

typedef struct {
    int seq_num;
    char payload[64];
} ping_msg_t;

typedef struct {
    int ack_num;
    char reply[64];
} pong_msg_t;

static int server_chid = -1;
static volatile int keep_running = 1;

void* server_thread(void *arg)
{
    (void)arg;
#ifdef __QNXNTO__
    server_chid = ChannelCreate(0);
    printf("[Server] Channel created (chid=%d). Waiting in MsgReceive()...\n", server_chid);

    while (keep_running) {
        ping_msg_t msg;
        struct _msg_info info;
        int rcvid = MsgReceive(server_chid, &msg, sizeof(msg), &info);
        if (rcvid < 0) {
            break;
        }

        /* Verify standard message vs pulse */
        if (rcvid > 0) {
            pong_msg_t reply;
            reply.ack_num = msg.seq_num + 1;
            snprintf(reply.reply, sizeof(reply.reply), "ACK for seq %d", msg.seq_num);

            /* Reply immediately to release client from STATE_REPLY */
            MsgReply(rcvid, 0, &reply, sizeof(reply));
            printf("[Server] Handled msg seq=%d, sent ACK=%d\n", msg.seq_num, reply.ack_num);
        }
    }
#else
    server_chid = 1;
    printf("[Server] Simulation: Server initialized.\n");
    while (keep_running) { sleep(1); }
#endif
    return NULL;
}

void* client_thread(void *arg)
{
    (void)arg;
    /* Wait until server channel is initialized */
    while (server_chid == -1) {
        usleep(10000);
    }

#ifdef __QNXNTO__
    int coid = ConnectAttach(0, getpid(), server_chid, _NTO_SIDE_CHANNEL, 0);
    printf("[Client] Connected to server channel (coid=%d). Starting transactions...\n", coid);

    for (int i = 1; i <= 5 && keep_running; i++) {
        ping_msg_t msg;
        pong_msg_t reply;
        msg.seq_num = i;
        snprintf(msg.payload, sizeof(msg.payload), "Heartbeat packet %d", i);

        int status = MsgSend(coid, &msg, sizeof(msg), &reply, sizeof(reply));
        if (status == 0) {
            printf("[Client] Sent seq=%d -> Got: %s\n", i, reply.reply);
        } else {
            perror("[Client] MsgSend failed");
        }
        usleep(100000); /* 100ms delay */
    }

    keep_running = 0;
    ConnectDetach(coid);
    ChannelDestroy(server_chid);
#else
    printf("[Client] Simulation: Sent 5 healthy IPC transactions.\n");
    keep_running = 0;
#endif
    return NULL;
}

int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    pthread_t t_srv, t_cli;

    printf("============================================================\n");
    printf(" QNX-Aegis Demo: Healthy QNX Microkernel Synchronous IPC    \n");
    printf(" Demonstrates non-blocking MsgReceive/MsgReply flow         \n");
    printf("============================================================\n");

    pthread_create(&t_srv, NULL, server_thread, NULL);
    pthread_create(&t_cli, NULL, client_thread, NULL);

    pthread_join(t_cli, NULL);
    pthread_join(t_srv, NULL);

    printf("[Main] Healthy IPC demonstration completed successfully.\n");
    return 0;
}
