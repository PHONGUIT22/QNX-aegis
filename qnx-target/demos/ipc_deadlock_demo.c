/**
 * ============================================================================
 * QNX-Aegis Demo: Deterministic QNX Native IPC Deadlock
 * File: ipc_deadlock_demo.c
 * Purpose: Demonstrates mutual MsgSend() circular wait between two threads.
 *          Both threads enter STATE_SEND / STATE_REPLY, freezing execution.
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
  #include <sys/slog2.h>
#else
  #define ChannelCreate(x) (1)
  #define ConnectAttach(a,b,c,d,e) (1)
  #define MsgSend(a,b,c,d,e) (0)
  #define MsgReceive(a,b,c,d) (0)
  #define MsgReply(a,b,c,d) (0)
  #define _NTO_SIDE_CHANNEL 0
#endif

typedef struct {
    int type;
    char payload[64];
} demo_msg_t;

static int chid_a = -1;
static int chid_b = -1;
static pthread_barrier_t barrier;

void* thread_alpha(void *arg)
{
    (void)arg;
    demo_msg_t send_msg = { .type = 101, .payload = "Msg from Alpha to Beta" };
    demo_msg_t reply_msg;

#ifdef __QNXNTO__
    chid_a = ChannelCreate(0);
    printf("[Alpha / TID %d] Channel A created (chid=%d)\n", (int)pthread_self(), chid_a);
#endif

    /* Wait until Beta thread has created its channel */
    pthread_barrier_wait(&barrier);

#ifdef __QNXNTO__
    int coid_to_b = ConnectAttach(0, getpid(), chid_b, _NTO_SIDE_CHANNEL, 0);
    printf("[Alpha / TID %d] Attached to Channel B (coid=%d). Calling MsgSend()...\n",
           (int)pthread_self(), coid_to_b);

    /*
     * DEADLOCK TRIGGER:
     * Alpha calls MsgSend() to Beta BEFORE calling MsgReceive() on Channel A.
     * Alpha transitions into STATE_SEND / STATE_REPLY waiting for Beta.
     */
    int status = MsgSend(coid_to_b, &send_msg, sizeof(send_msg), &reply_msg, sizeof(reply_msg));
    printf("[Alpha] Received reply status: %d (SHOULD NEVER BE REACHED)\n", status);
#else
    printf("[Alpha] Simulation: Thread blocked in simulated MsgSend deadlock.\n");
    while (1) { sleep(10); }
#endif

    return NULL;
}

void* thread_beta(void *arg)
{
    (void)arg;
    demo_msg_t send_msg = { .type = 202, .payload = "Msg from Beta to Alpha" };
    demo_msg_t reply_msg;

#ifdef __QNXNTO__
    chid_b = ChannelCreate(0);
    printf("[Beta  / TID %d] Channel B created (chid=%d)\n", (int)pthread_self(), chid_b);
#endif

    /* Wait until Alpha thread has created its channel */
    pthread_barrier_wait(&barrier);

#ifdef __QNXNTO__
    int coid_to_a = ConnectAttach(0, getpid(), chid_a, _NTO_SIDE_CHANNEL, 0);
    printf("[Beta  / TID %d] Attached to Channel A (coid=%d). Calling MsgSend()...\n",
           (int)pthread_self(), coid_to_a);

    /*
     * DEADLOCK TRIGGER:
     * Beta simultaneously calls MsgSend() to Alpha BEFORE calling MsgReceive() on Channel B.
     * Beta transitions into STATE_SEND / STATE_REPLY waiting for Alpha.
     * Result: Alpha waits for Beta, Beta waits for Alpha -> CIRCULAR DEADLOCK.
     */
    int status = MsgSend(coid_to_a, &send_msg, sizeof(send_msg), &reply_msg, sizeof(reply_msg));
    printf("[Beta] Received reply status: %d (SHOULD NEVER BE REACHED)\n", status);
#else
    printf("[Beta] Simulation: Thread blocked in simulated MsgSend deadlock.\n");
    while (1) { sleep(10); }
#endif

    return NULL;
}

int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    pthread_t t_alpha, t_beta;

    printf("============================================================\n");
    printf(" QNX-Aegis: QNX 8.0 Native IPC Mutual Deadlock Demo         \n");
    printf(" Spawning Thread Alpha and Thread Beta                      \n");
    printf("============================================================\n");

    pthread_barrier_init(&barrier, NULL, 2);

    if (pthread_create(&t_alpha, NULL, thread_alpha, NULL) != 0) {
        perror("pthread_create t_alpha");
        return 1;
    }

    if (pthread_create(&t_beta, NULL, thread_beta, NULL) != 0) {
        perror("pthread_create t_beta");
        return 1;
    }

    printf("[Main] Both threads dispatched. System is entering deadlock state.\n");
    printf("[Main] Observe blocking states via: pidin or cat /dev/qnx_inspector\n");

    pthread_join(t_alpha, NULL);
    pthread_join(t_beta, NULL);
    pthread_barrier_destroy(&barrier);

    return 0;
}
