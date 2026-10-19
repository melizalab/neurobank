# Metadata readers (#58)

Deposit reads the registry metadata for each file from the file itself, so that
what's recorded about a resource comes from the program that made it rather than
from what someone types at deposit. A reader is a plugin for one dtype: it checks
that a file really is that dtype and returns the metadata that belongs in the
registry. Readers are owned by the packages that produce the files.

| dtype               | Package          | Producer            |
|---------------------|------------------|---------------------|
| `extracellular-arf` | arfx             | `arfx-oephys`       |
| `spikes-pprox`      | melizalab-tools  | `group-kilo-spikes` |
| `spikes-hdf5`       | melizalab-tools  | `group-kilo-spikes` |

## Discovery

- Readers are entry points in the group `nbank.metadata_readers`. The entry
  point's name is the dtype, as registered in the registry:

  ```toml
  [project.entry-points."nbank.metadata_readers"]
  extracellular-arf = "arfx.nbank:read_metadata"
  ```

- Deposit looks up the reader for the dtype given with `-d`/`dtype=`. With no
  dtype, or no reader installed for it, deposit works as it does now, with only
  the metadata given on the command line, and logs that no reader was found.
- Two installed packages claiming the same dtype is an error: deposit refuses
  to start and names both packages.
- Readers are loaded only when needed, so a broken plugin for one dtype doesn't
  affect deposits of another.

## The reader contract

```python
def read_metadata(path: pathlib.Path) -> dict[str, Any]: ...
```

- **No dependency on neurobank.** The reader is a plain function taking a path.
  The producer packages don't import neurobank for it, so they aren't tied to
  its version (melizalab-tools currently pins `neurobank<0.13`).
- **Returns** the metadata for the registry, as JSON-serializable values:
  - `experimenter` (a registry username) and `animal` (the animal's full
    identifier; a colony UUID in our lab) are the resource fields; every other
    key is metadata.
  - The agreed names, not legacy ones: a reader for files written before the
    producers were fixed maps `bird` to `animal` and `experiment` to `protocol`.
    The producer knows its own history; neurobank doesn't.
  - Only what's known: a key with no value is left out, never returned as None.
  - Nothing the registry sets itself (name, sha1, dtype, locations).
- **Validates the file.** If the file isn't a well-formed file of the dtype (wrong
  format, missing required structure or attributes, inconsistent contents), the
  reader raises `ValueError` with a message for the user. Deposit treats any other
  exception as a failure of the reader for that file and reports it with its type.
- **Read-only and cheap.** It must not change the file, and should read headers
  and attributes rather than the data, since it runs on every file deposited.
- **Pure.** No registry access and no network: a file's metadata depends only on
  the file.

## Deposit

- **When:** after the existence, directory, symlink, and permission checks, and
  before hashing, so a file that isn't what it claims to be fails before the slow
  step.
- **Merging with what the user gives** (`--experimenter`, `--animal`, `-k`):
  - keys only the file has, or only the user gave, are all used;
  - a key both have with the same value is fine;
  - a key with different values fails that file, naming the key and both
    values, unless the user asks for their values to win
    (`--override-file-metadata`, `override_file_metadata=True` in
    `core.deposit`), which is logged per key.
- **Failures:** a file whose reader fails isn't registered or moved. With
  `skip_errors` (the CLI default) it yields `{"source", "error"}` and deposit
  moves on, as for other per-file errors.
- **Dry run:** runs the readers and shows the merged metadata for each file, in
  the log and in `-j` output (`"metadata"` in each record), so the result can be
  checked before depositing.
- **Opt out:** `--no-file-metadata` on the command line and
  `read_metadata=False` in `core.deposit` skip the reader, for files a reader
  can't handle or deposits that must use only the given metadata.

## The three readers

### `extracellular-arf` (arfx)

- **Validation:** an HDF5 file with the ARF root attributes, at least one entry,
  and in each entry the datasets `arfx-oephys` writes (continuous extracellular
  data with a sampling rate).
- **Which attributes:** entries hold the user's `-k` attributes together with
  attributes from the open-ephys structure and ARF's own (`timestamp`, `uuid`,
  `entry_creator`). `arfx-oephys` records the names of the attributes that came
  from `-k` (and from `--experimenter`/`--animal`, if added) in an entry
  attribute, and the reader takes exactly those. Files written before that have
  no such list; how the reader handles them is to be decided with the rule for
  differences below.
- **Differences between entries:** the registry describes the file as a whole,
  so an attribute is registered only if it has one value for the file. When
  entries differ, some differences are acceptable (the attribute is left out)
  and others mean the file is inconsistent (the reader raises `ValueError`).
  Which is which is to be worked out when the reader is written; see the open
  questions.
- **Producer changes:** `arfx-oephys` records the names of the `-k` attributes
  in each entry. Users write `animal` (a full colony UUID) and `experimenter` (a
  username) instead of `bird`; ideally `arfx-oephys` gets
  `--experimenter`/`--animal` options and checks the UUID form, to catch errors
  when the file is made rather than when it's deposited.

### `spikes-pprox` (melizalab-tools)

- **Validation:** JSON with a known pprox `$schema` and a `pprox` list of trials.
  Checking the whole file against the schema is optional (the files can be
  large); checking the top level is required.
- **Metadata:** the top-level scalars (strings, numbers, booleans), except
  `$schema`. Lists and objects (`pprox`, `entry_metadata`, `processed_by`) are left
  out. That keeps what the registry has for these files now: the `kilosort_*`
  values, `recording`, and the copy of the recording's metadata.
- **Producer change:** `group-kilo-spikes` copies the recording's registry
  `metadata` into the file. Under API 2.0 that no longer includes `experimenter`
  and `animal`, so it should write them from the recording's record explicitly,
  as top-level `experimenter` and `animal`.

### `spikes-hdf5` (melizalab-tools)

- **Validation:** an HDF5 file with the waveform dataset and spike times that
  `save_waveforms` writes.
- **Metadata:** the root attributes, the same set as the matching pprox file
  except for the recording's metadata, which isn't copied here.
- **Producer change:** `save_waveforms` gets `experimenter` and `animal` (and the
  rest of the recording's metadata, if wanted) as root attributes, so the file is
  as self-describing as the pprox.

## Problems for reader authors to watch for

Found while writing neurobank's side against a fake reader. neurobank refuses
each of these with a message naming the key, rather than storing something odd.

- **numpy and bytes from HDF5:** h5py returns attributes as numpy scalars
  (`np.int64`, `np.float32`), numpy arrays, and sometimes `bytes` or
  `np.bytes_` for strings. None of these are JSON, so a reader for an HDF5-based
  dtype has to convert them (`.item()`, `.tolist()`, `.decode()`). `np.float64`
  happens to pass, as a subclass of `float`, which can hide the problem in tests.
- **NaN and infinity:** HDF5 attributes can hold them, JSON and Postgres can't.
  Leave such keys out.
- **Types that don't match what users type:** `-k pen=1` gives the number 1, so
  a reader returning the string `"1"` disagrees with it and the file fails. Return
  numbers as numbers. A boolean and a number never agree (Python's `True == 1`
  isn't followed); an integer and a float with the same value do.
- **Case in identifiers:** values are compared exactly. An animal UUID in
  capitals on the command line disagrees with the same UUID in lowercase in the
  file, even though the registry would accept either. Producers should write
  identifiers in the registry's form (lowercase UUIDs).
- **Values the registry checks:** an `experimenter` who isn't a user or an
  `animal` that isn't in the animal model is only caught when the file is
  registered, after hashing. That's the registry being the arbiter; a reader
  shouldn't try to check them.
- **The entry point name must match the dtype exactly.** If a dtype is renamed
  in the registry, its reader is silently not found (deposit logs "no metadata
  reader"), and files are deposited without their metadata.
- **One reader per dtype across all installed packages:** two packages claiming
  a dtype (say, two versions of a producer in one environment) stop deposits of
  that dtype until one is removed.
- **A reader that can't be imported** (a missing dependency such as h5py) stops
  every deposit of its dtype; `--no-file-metadata` gets around it. Readers
  should import their heavy dependencies inside the function only if the module
  itself is imported for other reasons.
- **Directories:** if an archive allows directory resources and a dtype has a
  reader, the reader is called with the directory's path. Raise `ValueError` if
  that isn't supported.
- **Errors:** a `ValueError`'s message is shown to the user as the reason the
  file was skipped, so it should say what's wrong with the file. Any other
  exception is reported with its type, and its traceback is logged at debug
  level (`nbank --debug`).

## Testing

- **neurobank:** a fake reader registered as an entry point in the tests
  (monkeypatching `importlib.metadata.entry_points`), covering: merge, conflict,
  override, dry run, `skip_errors`, no reader installed, two readers for one
  dtype, a reader raising `ValueError` and something else, a reader returning a
  key with None, and the opt-out. Live tests deposit through a fake reader to
  check the fields reach the registry.
- **Producers:** each reader tested against small files written by the producer
  (and by older versions, for the legacy names).

## Open questions

- [ ] **ARF differences between entries:** which are acceptable, so the
  attribute is left out, and which make the file fail? For instance, entries with
  different `experimenter` or `animal` values are probably an error, while some
  recording parameters may legitimately vary. Decide while writing the reader.
- [ ] **ARF files without the list of `-k` attributes** (written before the
  `arfx-oephys` change): read nothing, use a fixed list of known keys, or
  something else?
- [x] **ARF attributes:** `arfx-oephys` records which attributes came from `-k`,
  and the reader takes exactly those.
- [ ] **Override:** is refusing on disagreement with an explicit override right,
  or should the given values always win, with a warning?
- [ ] **Inheritance:** pprox and spikes HDF5 files name their `recording`.
  Instead of the producers copying `experimenter` and `animal` in, deposit could
  look the recording up and fill them in. That makes readers depend on the
  registry, which the contract above rules out; copying in the producer keeps
  the files self-describing. Copying is the proposal here.
- [ ] **Later:** `nbank check` could run the readers against stored files and
  report where the registry and the file disagree.
