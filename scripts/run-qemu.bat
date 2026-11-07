@echo off
title QNX Neutrino 8.0 QEMU Target
qemu-system-x86_64 -cpu max -m 2048 -smp 4 -drive file=D:\QNX_VM\qemu\output\disk-qemu,format=raw,if=ide -net nic,model=virtio -net user,hostfwd=tcp::2222-:22 -serial tcp:127.0.0.1:4444,server,nowait -display none
