#!/usr/bin/env python3
"""Measure a process's true peak RSS by polling /proc/<pid>/status VmHWM."""
import subprocess
import sys
import time

code, infile, outfile, workdir = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
fin = open(infile, "rb")
fout = open(outfile, "wb")
t0 = time.time()
p = subprocess.Popen([code], stdin=fin, stdout=fout, cwd=workdir)
hwm = 0
rss = 0
while p.poll() is None:
    try:
        with open(f"/proc/{p.pid}/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    hwm = int(line.split()[1])
                elif line.startswith("VmRSS:"):
                    rss = int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError):
        break
    time.sleep(0.002)
el = time.time() - t0
print(f"exit={p.returncode} wall={el:.3f}s VmHWM={hwm}KiB lastRSS={rss}KiB")
