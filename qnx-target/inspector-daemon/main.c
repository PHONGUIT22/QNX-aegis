/**
 * ============================================================================
 * QNX-Aegis: QNX 8.0 Resource Manager Daemon
 * File: main.c
 * Purpose: Registers /dev/qnx_inspector in the QNX namespace and provides
 *          zero-copy JSON telemetry streaming of microkernel IPC states.
 * ============================================================================
 */

#include "qnx_inspector.h"

#ifdef __QNXNTO__
static resmgr_connect_funcs_t connect_funcs;
static resmgr_io_funcs_t      io_funcs;
static iofunc_attr_t          attr;
static dispatch_t             *dpp = NULL;
static resmgr_attr_t          resmgr_attr;
static int                    resmgr_id = -1;

/**
 * Custom io_read callback:
 * Invoked whenever a process executes read() or cat on /dev/qnx_inspector.
 */
static int inspector_io_read(resmgr_context_t *ctp, io_read_t *msg, RESMGR_OCB_T *ocb)
{
    int status;
    int nbytes;
    static char json_buffer[MAX_JSON_BUFFER_SIZE];
    static int json_len = 0;
    static qnx_ipc_snapshot_t snapshot;

    /* Verify POSIX read permissions */
    if ((status = iofunc_read_verify(ctp, msg, ocb, NULL)) != EOK) {
        return status;
    }

    /* Handle xtype (non-standard reads) */
    if ((msg->i.xtype & _IO_XTYPE_MASK) != _IO_XTYPE_NONE) {
        return ENOSYS;
    }

    /* At offset 0 (start of read), capture fresh microkernel telemetry snapshot */
    if (ocb->offset == 0) {
        memset(&snapshot, 0, sizeof(snapshot));
        analyze_qnx_ipc(&snapshot);
        read_recent_slog2(snapshot.slog2_buffer, sizeof(snapshot.slog2_buffer));
        json_len = format_snapshot_json(&snapshot, json_buffer, sizeof(json_buffer));
        if (json_len < 0) {
            return EIO;
        }
    }

    /* If offset reached or exceeded buffer length, return EOF */
    if (ocb->offset >= json_len) {
        _IO_SET_READ_NBYTES(ctp, 0);
        return (_RESMGR_PTR(ctp, NULL, 0));
    }

    /* Determine number of bytes to transfer in this chunk */
    nbytes = msg->i.nbytes;
    if (nbytes > json_len - (int)ocb->offset) {
        nbytes = json_len - (int)ocb->offset;
    }

    /* Set read bytes and reply to client with offset slice */
    _IO_SET_READ_NBYTES(ctp, nbytes);
    int res = _RESMGR_PTR(ctp, json_buffer + ocb->offset, nbytes);
    ocb->offset += nbytes;
    return res;
}
#endif

int main(int argc, char **argv)
{
    int cli_dump = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--dump") == 0 || strcmp(argv[i], "-d") == 0) {
            cli_dump = 1;
        }
    }

#ifdef __QNXNTO__
    /* If run in CLI dump mode, print ONLY pure JSON to stdout and exit */
    if (cli_dump) {
        static qnx_ipc_snapshot_t snap;
        static char buf[MAX_JSON_BUFFER_SIZE];
        analyze_qnx_ipc(&snap);
        read_recent_slog2(snap.slog2_buffer, sizeof(snap.slog2_buffer));
        format_snapshot_json(&snap, buf, sizeof(buf));
        printf("%s\n", buf);
        return 0;
    }

    printf("[QNX-Aegis] Inspector Daemon v%s starting...\n", QNX_INSPECTOR_VERSION);

    /* Step 1: Initialize dispatch structure */
    dpp = dispatch_create();
    if (dpp == NULL) {
        fprintf(stderr, "[!] dispatch_create failed: %s\n", strerror(errno));
        return EXIT_FAILURE;
    }

    /* Step 2: Initialize default POSIX function tables */
    iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &connect_funcs,
                     _RESMGR_IO_NFUNCS, &io_funcs);

    /* Override read handler to serve dynamic JSON */
    io_funcs.read = inspector_io_read;

    /* Initialize device attributes (read-only for clients, rw for owner) */
    iofunc_attr_init(&attr, S_IFCHR | 0666, 0, 0);

    memset(&resmgr_attr, 0, sizeof(resmgr_attr));
    resmgr_attr.nparts_max = 2;
    resmgr_attr.msg_max_size = 4096;

    /* Step 3: Register /dev/qnx_inspector pathname in QNX namespace */
    resmgr_id = resmgr_attach(dpp, &resmgr_attr, QNX_INSPECTOR_DEVICE,
                              _FTYPE_ANY, 0, &connect_funcs, &io_funcs, &attr);
    if (resmgr_id == -1) {
        fprintf(stderr, "[!] resmgr_attach to %s failed: %s (errno %d)\n",
                QNX_INSPECTOR_DEVICE, strerror(errno), errno);
        if (errno == EACCES || errno == EPERM) {
            fprintf(stderr, "[*] Notice: Attaching to /dev/qnx_inspector requires root/PROCMGR_AID_PATHSPACE on QNX 8.0.\n"
                            "[*] Run daemon with sudo: 'echo qnxuser | sudo -S %s' or use CLI dump '%s --dump'\n",
                            argv[0], argv[0]);
        }
        dispatch_destroy(dpp);
        return EXIT_FAILURE;
    }

    printf("[+] Successfully attached QNX Resource Manager to %s\n", QNX_INSPECTOR_DEVICE);
    printf("[*] Entering QNX event dispatch loop (dispatch_block)...\n");

    /* Step 4: Allocate dispatch context and enter event loop */
    dispatch_context_t *ctp = dispatch_context_alloc(dpp);
    if (ctp == NULL) {
        fprintf(stderr, "[!] dispatch_context_alloc failed: %s\n", strerror(errno));
        return EXIT_FAILURE;
    }

    while (1) {
        ctp = dispatch_block(ctp);
        if (ctp == NULL) {
            fprintf(stderr, "[!] dispatch_block error: %s\n", strerror(errno));
            break;
        }
        dispatch_handler(ctp);
    }

    dispatch_destroy(dpp);
#else
    printf("[*] Host/Simulation build: Simulating snapshot output...\n");
    static qnx_ipc_snapshot_t snap;
    static char buf[MAX_JSON_BUFFER_SIZE];
    analyze_qnx_ipc(&snap);
    read_recent_slog2(snap.slog2_buffer, sizeof(snap.slog2_buffer));
    format_snapshot_json(&snap, buf, sizeof(buf));
    printf("%s\n", buf);
#endif

    return EXIT_SUCCESS;
}
