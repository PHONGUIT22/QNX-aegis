# ==============================================================================
# QNX-Aegis: Top-Level Target Makefile (QNX SDP 8.0)
# ==============================================================================

ARCH ?= gcc_ntox86_64
BINDIR = $(CURDIR)/bin

all: dirs
	$(MAKE) -C inspector-daemon ARCH=$(ARCH)
	$(MAKE) -C demos ARCH=$(ARCH)
	cp inspector-daemon/qnx_inspector $(BINDIR)/
	cp demos/ipc_deadlock_demo $(BINDIR)/
	cp demos/ipc_healthy_demo $(BINDIR)/
	cp demos/slog2_crash_demo $(BINDIR)/
	@echo "All QNX 8.0 target binaries successfully compiled into $(BINDIR)"

dirs:
	mkdir -p $(BINDIR)

clean:
	$(MAKE) -C inspector-daemon clean
	$(MAKE) -C demos clean
	rm -rf $(BINDIR)

.PHONY: all dirs clean
