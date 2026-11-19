# Parity baseline vs Python

Values captured by running the **shipping Python implementation** from `src/`.
The Rust unit tests assert the same values, so they double as a regression
contract for the port.

Regenerate with:

```powershell
python -c "import sys;sys.path.insert(0,'src/BuzzInstaller');sys.path.insert(0,'src');import core.chunking as c;print(c.chunk_count(25*1024*1024,10*1024*1024))"
```

## Links

| Input | Python | Rust | Same |
|---|---|---|---|
| `BZ://h0l6lgmru052` | `h0l6lgmru052` | `h0l6lgmru052` | yes |
| `bz://h0l6lgmru052` | `h0l6lgmru052` | `h0l6lgmru052` | yes |
| `  BZ://h0l6lgmru052  ` | `h0l6lgmru052` | `h0l6lgmru052` | yes |
| `https://buzzheavier.com/h0l6lgmru052` | `h0l6lgmru052` | `h0l6lgmru052` | yes |
| `h0l6lgmru052` | `h0l6lgmru052` | `h0l6lgmru052` | yes |
| `BZ:h0l6lgmru052` | **error** | `h0l6lgmru052` | fixed |
| `BZ://H0L6LGM` | **error** | `h0l6lgm` | fixed |

The two "fixed" rows are documented behaviour in Python's docstring that the
Python regex does not actually implement. The Rust core honours the docs.

## Chunk maths (1 MiB = 1,048,576)

| Call | Python | Rust |
|---|---|---|
| `chunk_count(25 MiB, 10 MiB)` | `3` | `3` |
| `chunk_count(30 MiB, 10 MiB)` | `3` | `3` |
| `describe(25 MiB, 10 MiB)` | `(3, 5,242,880)` | `(3, 5 MiB)` |
| `describe(30 MiB, 10 MiB)` | `(3, 10,485,760)` | `(3, 10 MiB)` |
| `chunk_count(25, 10)` | `1` (clamped to 1 MiB) | `1` |

## Names

| Call | Python | Rust |
|---|---|---|
| `chunk_name("holiday video.mkv", "abc123def456", 2, 12)` | `holiday video.abc123de.part00002-of-00012.bzbchunk` | same |
| `chunk_name(r"C:\Users\me\a.tar.gz", "", 0, 1)` | `a.tar.00000000.part00000-of-00001.bzbchunk` | same |
| `sanitize_filename("bad:name?.txt")` | `bad_name_.txt` | same |
| `sanitize_filename("...")` | `unnamed` | same |
| `sanitize_filename("ok", 1)` | `o` | same |
