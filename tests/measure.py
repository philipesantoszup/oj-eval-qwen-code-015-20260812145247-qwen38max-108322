#!/usr/bin/env python3
"""Run the binary on an input file, report wall time and peak RSS (KiB)."""
import resource
import subprocess
import sys
import time

code, infile, outfile, workdir = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
t0 = time.time()
with open(infile, "rb") as fin, open(outfile, "wb") as fout:
    p = subprocess.run([code], stdin=fin, stdout=fout, cwd=workdir)
el = time.time() - t0
ru = resource.getrusage(resource.RUSAGE_CHILDREN)
print(f"exit={p.returncode} wall={el:.3f}s maxrss={ru.ru_maxrss}KiB")
