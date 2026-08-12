#!/usr/bin/env python3
"""Stress test for the file-storage KV database.

Generates random command sequences, runs the C++ program (possibly split
across several consecutive runs to exercise persistence), and compares the
output against a pure-Python reference model.
"""
import os
import random
import shutil
import string
import subprocess
import sys

CODE = os.environ.get("CODE", "/workspace/problem_015/code")


class Ref:
    def __init__(self):
        self.db = {}  # key -> set of values

    def insert(self, k, v):
        self.db.setdefault(k, set()).add(v)

    def delete(self, k, v):
        s = self.db.get(k)
        if s is not None:
            s.discard(v)
            if not s:
                del self.db[k]

    def find(self, k):
        s = self.db.get(k)
        if not s:
            return "null"
        return " ".join(str(x) for x in sorted(s))


def gen_commands(rng, n, keys, max_value, ins_w=0.45, del_w=0.25):
    """Generate n valid commands (never inserts an existing (key,value))."""
    ref = Ref()
    cmds = []
    for _ in range(n):
        r = rng.random()
        if r < ins_w:
            k = rng.choice(keys)
            # pick a value not currently used for k
            used = ref.db.get(k, set())
            if len(used) > max_value:
                continue
            if max_value <= 10000:
                free = [v for v in range(max_value + 1) if v not in used]
                if not free:
                    continue
                v = rng.choice(free)
            else:
                while True:
                    v = rng.randint(0, max_value)
                    if v not in used:
                        break
            ref.insert(k, v)
            cmds.append(f"insert {k} {v}")
        elif r < ins_w + del_w:
            if not ref.db:
                continue
            k = rng.choice(list(ref.db.keys())) if rng.random() < 0.7 else rng.choice(keys)
            s = ref.db.get(k)
            if s:
                v = rng.choice(list(s)) if rng.random() < 0.8 else rng.randint(0, max_value)
            else:
                v = rng.randint(0, max_value)  # delete of non-existent entry
            ref.delete(k, v)
            cmds.append(f"delete {k} {v}")
        else:
            if ref.db and rng.random() < 0.6:
                k = rng.choice(list(ref.db.keys()))
            else:
                k = rng.choice(keys)
            cmds.append(f"find {k}")
    return cmds


def run_split(cmds, workdir, splits):
    """Run the command list through the binary, split into `splits` runs."""
    outputs = []
    idx = 0
    total = len(cmds)
    for s in range(splits):
        end = total * (s + 1) // splits
        chunk = cmds[idx:end]
        idx = end
        # count find commands to know how many output lines to expect
        data = f"{len(chunk)}\n" + "".join(c + "\n" for c in chunk)
        p = subprocess.run([CODE], input=data.encode(), stdout=subprocess.PIPE,
                           cwd=workdir, timeout=300)
        if p.returncode != 0:
            raise RuntimeError(f"program exited with {p.returncode}")
        outputs.extend(p.stdout.decode().splitlines())
    return outputs


def expected_output(cmds):
    ref = Ref()
    out = []
    for c in cmds:
        parts = c.split()
        if parts[0] == "insert":
            ref.insert(parts[1], int(parts[2]))
        elif parts[0] == "delete":
            ref.delete(parts[1], int(parts[2]))
        else:
            out.append(ref.find(parts[1]))
    return out


def rand_keys(rng, count, minlen, maxlen):
    ks = set()
    alphabet = string.ascii_letters + string.digits + "_-.!@#$%&*()+~=;:,?/[]{}<>|\\"
    while len(ks) < count:
        ln = rng.randint(minlen, maxlen)
        ks.add("".join(rng.choice(alphabet) for _ in range(ln)))
    return sorted(ks)


def one_case(seed, n, key_count, minlen, maxlen, max_value, splits, ins_w=0.45, del_w=0.25):
    rng = random.Random(seed)
    keys = rand_keys(rng, key_count, minlen, maxlen)
    cmds = gen_commands(rng, n, keys, max_value, ins_w, del_w)
    workdir = "/tmp/run015_case"
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir)
    got = run_split(cmds, workdir, splits)
    exp = expected_output(cmds)
    if got != exp:
        print(f"FAIL seed={seed} n={n} keys={key_count} splits={splits}")
        for i, (g, e) in enumerate(zip(got, exp)):
            if g != e:
                print(f"  first diff at find #{i}: got {g!r} expected {e!r}")
                break
        print(f"  got {len(got)} lines, expected {len(exp)} lines")
        return False
    return True


def main():
    cases = [
        # seed, n, key_count, minlen, maxlen, max_value, splits
        (1, 500, 20, 1, 10, 100, 1),
        (2, 2000, 50, 1, 64, 10**9, 1),
        (3, 5000, 200, 1, 64, 2**31 - 1, 3),
        (4, 5000, 30, 60, 64, 50, 4),          # long keys, few keys, small values
        (5, 3000, 1, 5, 5, 100000, 2),          # single key, many values
        (6, 4000, 500, 1, 8, 2**31 - 1, 1, 0.6, 0.3),   # insert/delete heavy
        (7, 4000, 500, 1, 8, 2**31 - 1, 5, 0.3, 0.2),   # find heavy, many splits
        (8, 6000, 3, 1, 3, 30, 3, 0.5, 0.35),   # tiny key/value space -> churn
        (9, 2500, 100, 64, 64, 0, 2),           # all keys len 64, value always 0
        (10, 1000, 10, 1, 5, 5, 10, 0.5, 0.3),  # many tiny runs (persistence)
        (11, 100000, 5000, 1, 20, 2**31 - 1, 5),  # large multi-run persistence
        (12, 10000, 300, 1, 64, 2**31 - 1, 2, 0.5, 0.3),  # special chars
    ]
    ok = True
    for c in cases:
        if not one_case(*c):
            ok = False
        else:
            print(f"OK  seed={c[0]} n={c[1]} keys={c[2]} len={c[3]}..{c[4]} maxv={c[5]} splits={c[6]}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
