# ==============================================================================
# QNX-Aegis: Demos Makefile (QNX SDP 8.0)
# ==============================================================================

ARCH ?= gcc_ntox86_64
CC = qcc
CFLAGS = -V$(ARCH) -O2 -Wall -Wextra -D_QNX_SOURCE
LDFLAGS = -lslog2

TARGETS = ipc_deadlock_demo ipc_healthy_demo slog2_crash_demo ipc_benchmark_demo

all: $(TARGETS)

ipc_deadlock_demo: ipc_deadlock_demo.c
	$(CC) $(CFLAGS) -o $@ $<

ipc_healthy_demo: ipc_healthy_demo.c
	$(CC) $(CFLAGS) -o $@ $<

slog2_crash_demo: slog2_crash_demo.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

ipc_benchmark_demo: ipc_benchmark_demo.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f $(TARGETS) *.o

.PHONY: all clean
