#!/usr/bin/env python3
"""Generate large worst-case-ish inputs for performance testing."""
import random
import sys

seed = int(sys.argv[1])
mode = sys.argv[2]
n = int(sys.argv[3])
out = sys.argv[4]
rng = random.Random(seed)

lines = [str(n)]
if mode == "inserts":          # n inserts, random keys
    for i in range(n):
        k = "key" + str(rng.randrange(10**9))
        lines.append(f"insert {k} {rng.randrange(2**31)}")
elif mode == "sorted_inserts":  # inserts of one key in increasing value order
    for i in range(n):
        lines.append(f"insert K {i}")
elif mode == "rev_inserts":     # one key, decreasing values (front memmoves)
    for i in range(n):
        lines.append(f"insert K {n - i}")
elif mode == "ins_find":        # half inserts, half finds
    ks = []
    for i in range(n // 2):
        k = "key" + str(rng.randrange(10**8))
        ks.append(k)
        lines.append(f"insert {k} {rng.randrange(2**31)}")
    for i in range(n - n // 2):
        lines.append(f"find {rng.choice(ks)}")
elif mode == "churn":           # insert then delete, same keys
    for i in range(n // 2):
        k = "k" + str(i % 1000)
        lines.append(f"insert {k} {i}")
    for i in range(n - n // 2):
        k = "k" + str(i % 1000)
        lines.append(f"delete {k} {i}")
elif mode == "onekey_find":     # one key with many values, many finds
    v = n // 3
    for i in range(v):
        lines.append(f"insert BIG {rng.randrange(2**31)}")
    for i in range(n - v):
        lines.append("find BIG")
elif mode == "mixed":           # realistic mix with deletes of nonexistent
    keys = ["word" + str(rng.randrange(50000)) for _ in range(2000)]
    live = {}
    for i in range(n):
        r = rng.random()
        k = rng.choice(keys)
        if r < 0.45:
            v = rng.randrange(2**31)
            lines.append(f"insert {k} {v}")
            live.setdefault(k, set()).add(v)
        elif r < 0.7:
            s = live.get(k)
            if s and rng.random() < 0.8:
                v = rng.choice(list(s))
                s.discard(v)
            else:
                v = rng.randrange(2**31)
            lines.append(f"delete {k} {v}")
        else:
            lines.append(f"find {k}")
with open(out, "w") as f:
    f.write("\n".join(lines) + "\n")
print(f"wrote {out}: mode={mode} n={n}")
