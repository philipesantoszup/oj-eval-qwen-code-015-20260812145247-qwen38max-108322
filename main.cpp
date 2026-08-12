// Problem 015 - File Storage
//
// File-based key-value database.
//
// Design:
//  - keys.bin : on-disk store of distinct keys in fixed 65-byte slots
//               (1 byte length + up to 64 key bytes). Keys themselves are
//               never kept in memory; slots are read on demand with pread().
//  - data.bin : array of 16-byte records { hash64, key_slot, value },
//               kept sorted by (hash, value). Loaded once at startup
//               (<= 100000 records -> 1.6 MB) and rewritten on exit.
//
// Lookup groups records by 64-bit FNV-1a hash; collisions are resolved by
// comparing the real key read from keys.bin, so results are exact.
// Both files are created if missing and reused otherwise, so the database
// persists between consecutive runs of the program.

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <unistd.h>
#include <fcntl.h>

namespace {

constexpr const char *KEYS_FILE = "keys.bin";
constexpr const char *DATA_FILE = "data.bin";
constexpr int KEY_MAX = 64;
constexpr int SLOT_SZ = KEY_MAX + 1;          // 1 length byte + key bytes
constexpr int MAX_ENTRIES = 100000 + 16;

struct Entry {
    uint64_t hash;
    uint32_t slot;
    uint32_t value;
};

static_assert(sizeof(Entry) == 16, "Entry must stay 16 bytes");

Entry entries[MAX_ENTRIES];
uint32_t ent_count = 0;

int keys_fd = -1;
uint32_t slot_count = 0;

// ---------------- fast input ----------------
char inbuf[1 << 16];
int in_len = 0, in_pos = 0;

inline int gc() {
    if (in_pos >= in_len) {
        in_len = (int)read(STDIN_FILENO, inbuf, sizeof(inbuf));
        in_pos = 0;
        if (in_len <= 0) return -1;
    }
    return (unsigned char)inbuf[in_pos++];
}

inline bool is_ws(int c) { return c == ' ' || c == '\n' || c == '\r' || c == '\t'; }

int read_token(char *buf) {
    int c;
    do { c = gc(); } while (c != -1 && is_ws(c));
    int n = 0;
    while (c != -1 && !is_ws(c)) { buf[n++] = (char)c; c = gc(); }
    buf[n] = 0;
    return n;
}

uint32_t read_uint() {
    int c;
    do { c = gc(); } while (c != -1 && is_ws(c));
    uint32_t x = 0;
    while (c >= '0' && c <= '9') { x = x * 10 + (uint32_t)(c - '0'); c = gc(); }
    return x;
}

// ---------------- buffered output ----------------
char outbuf[1 << 17];
int out_pos = 0;

void flush_out() {
    if (out_pos > 0) {
        int off = 0;
        while (off < out_pos) {
            ssize_t w = write(STDOUT_FILENO, outbuf + off, (size_t)(out_pos - off));
            if (w <= 0) break;
            off += (int)w;
        }
        out_pos = 0;
    }
}

inline void put_char(char c) {
    if (out_pos >= (int)sizeof(outbuf)) flush_out();
    outbuf[out_pos++] = c;
}

inline void put_str(const char *s) {
    while (*s) put_char(*s++);
}

void put_uint(uint32_t x) {
    char tmp[12];
    int n = 0;
    if (x == 0) { put_char('0'); return; }
    while (x) { tmp[n++] = (char)('0' + x % 10); x /= 10; }
    while (n) put_char(tmp[--n]);
}

// ---------------- hashing ----------------
inline uint64_t hash_key(const char *k, int len) {
    uint64_t h = 14695981039346656037ULL;          // FNV-1a 64
    for (int i = 0; i < len; i++) {
        h ^= (uint8_t)k[i];
        h *= 1099511628211ULL;
    }
#ifdef WEAK_HASH_BITS
    // test hook: force collisions to exercise collision-resolution paths
    h &= (1ULL << WEAK_HASH_BITS) - 1;
#endif
    return h;
}

// ---------------- sorted entries ----------------
inline bool entry_less_target(const Entry &e, uint64_t h, uint32_t v) {
    if (e.hash != h) return e.hash < h;
    return e.value < v;
}

uint32_t lower_bound(uint64_t h, uint32_t v) {
    uint32_t lo = 0, hi = ent_count;
    while (lo < hi) {
        uint32_t mid = (lo + hi) >> 1;
        if (entry_less_target(entries[mid], h, v)) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

// ---------------- key slots ----------------
uint8_t slotbuf[SLOT_SZ];

void write_fully(int fd, const void *buf, size_t n, off_t off) {
    const char *p = (const char *)buf;
    while (n > 0) {
        ssize_t w = pwrite(fd, p, n, off);
        if (w <= 0) break;
        p += w;
        n -= (size_t)w;
        off += w;
    }
}

ssize_t read_fully(int fd, void *buf, size_t n, off_t off) {
    char *p = (char *)buf;
    size_t done = 0;
    while (done < n) {
        ssize_t r = pread(fd, p + done, n - done, off + (off_t)done);
        if (r <= 0) break;
        done += (size_t)r;
    }
    return (ssize_t)done;
}

bool slot_key_equals(uint32_t slot, const char *key, int klen) {
    if (read_fully(keys_fd, slotbuf, SLOT_SZ, (off_t)slot * SLOT_SZ) != SLOT_SZ) return false;
    if (slotbuf[0] != (uint8_t)klen) return false;
    return memcmp(slotbuf + 1, key, (size_t)klen) == 0;
}

uint32_t new_slot(const char *key, int klen) {
    uint8_t buf[SLOT_SZ];
    memset(buf, 0, sizeof(buf));
    buf[0] = (uint8_t)klen;
    memcpy(buf + 1, key, (size_t)klen);
    write_fully(keys_fd, buf, SLOT_SZ, (off_t)slot_count * SLOT_SZ);
    return slot_count++;
}

// Find the slot of an existing key among entries with hash h,
// or create a fresh slot if the key has no live entries.
uint32_t find_or_create_slot(const char *key, int klen, uint64_t h) {
    uint32_t pos = lower_bound(h, 0);
    for (uint32_t i = pos; i < ent_count && entries[i].hash == h; i++) {
        if (slot_key_equals(entries[i].slot, key, klen)) return entries[i].slot;
    }
    return new_slot(key, klen);
}

// ---------------- operations ----------------
void do_insert(const char *key, int klen, uint32_t value) {
    uint64_t h = hash_key(key, klen);
    uint32_t slot = find_or_create_slot(key, klen, h);
    uint32_t pos = lower_bound(h, value);
    if (ent_count < MAX_ENTRIES) {
        memmove(&entries[pos + 1], &entries[pos], (size_t)(ent_count - pos) * sizeof(Entry));
        entries[pos] = Entry{h, slot, value};
        ent_count++;
    }
}

void do_delete(const char *key, int klen, uint32_t value) {
    uint64_t h = hash_key(key, klen);
    uint32_t pos = lower_bound(h, value);
    for (uint32_t i = pos; i < ent_count && entries[i].hash == h && entries[i].value == value; i++) {
        if (slot_key_equals(entries[i].slot, key, klen)) {
            memmove(&entries[i], &entries[i + 1], (size_t)(ent_count - i - 1) * sizeof(Entry));
            ent_count--;
            return;
        }
    }
}

void do_find(const char *key, int klen) {
    uint64_t h = hash_key(key, klen);
    uint32_t pos = lower_bound(h, 0);
    bool any = false;
    bool slot_known = false;
    uint32_t target_slot = 0;
    for (uint32_t i = pos; i < ent_count && entries[i].hash == h; i++) {
        bool match;
        if (slot_known) {
            match = (entries[i].slot == target_slot);
        } else {
            match = slot_key_equals(entries[i].slot, key, klen);
            if (match) { target_slot = entries[i].slot; slot_known = true; }
        }
        if (match) {
            if (any) put_char(' ');
            put_uint(entries[i].value);
            any = true;
        }
    }
    if (!any) put_str("null");
    put_char('\n');
}

// ---------------- persistence ----------------
void load_db() {
    keys_fd = open(KEYS_FILE, O_RDWR);
    if (keys_fd < 0) {
        keys_fd = open(KEYS_FILE, O_RDWR | O_CREAT, 0644);
        slot_count = 0;
    } else {
        off_t sz = lseek(keys_fd, 0, SEEK_END);
        slot_count = (uint32_t)(sz / SLOT_SZ);
    }

    int dfd = open(DATA_FILE, O_RDONLY);
    if (dfd >= 0) {
        off_t sz = lseek(dfd, 0, SEEK_END);
        uint32_t n = (uint32_t)(sz / sizeof(Entry));
        if (n > MAX_ENTRIES) n = MAX_ENTRIES;
        if (n > 0) {
            ssize_t r = read_fully(dfd, entries, (size_t)n * sizeof(Entry), 0);
            if (r > 0) n = (uint32_t)(r / sizeof(Entry));
            else n = 0;
        }
        ent_count = n;
        close(dfd);
    }
}

void save_db() {
    int dfd = open(DATA_FILE, O_RDWR | O_CREAT | O_TRUNC, 0644);
    if (dfd >= 0) {
        if (ent_count > 0)
            write_fully(dfd, entries, (size_t)ent_count * sizeof(Entry), 0);
        close(dfd);
    }
    close(keys_fd);
    flush_out();
}

} // namespace

int main() {
    load_db();

    uint32_t n = read_uint();
    char cmd[8], key[KEY_MAX + 8];

    for (uint32_t i = 0; i < n; i++) {
        read_token(cmd);
        if (cmd[0] == 'i') {                    // insert <key> <value>
            int klen = read_token(key);
            uint32_t v = read_uint();
            do_insert(key, klen, v);
        } else if (cmd[0] == 'd') {             // delete <key> <value>
            int klen = read_token(key);
            uint32_t v = read_uint();
            do_delete(key, klen, v);
        } else {                                // find <key>
            int klen = read_token(key);
            do_find(key, klen);
        }
    }

    save_db();
    return 0;
}
