# ==============================================================================
# QNX-Aegis: inspector-daemon Makefile (QNX SDP 8.0)
# ==============================================================================

ARCH ?= gcc_ntox86_64
CC = qcc
CFLAGS = -V$(ARCH) -O2 -Wall -Wextra -D_QNX_SOURCE -I.
LDFLAGS = -lslog2

TARGET = qnx_inspector
SRCS = main.c ipc_analyzer.c slog2_reader.c
OBJS = $(SRCS:.c=.o)

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS) $(LDFLAGS)

%.o: %.c qnx_inspector.h
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET)

.PHONY: all clean
