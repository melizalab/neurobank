# Metadata readers and abfzip (#58)

Deposit reads the registry metadata for each file from the file itself, so that
what's recorded about a resource comes from the program that made it rather than
from what someone types at deposit. A reader is a plugin for one dtype: it checks
that a file really is that dtype and returns the metadata that belongs in the
registry. Readers are owned by the packages that produce the files.

| dtype                  | Package         | Producer                                  |
|------------------------|-----------------|-------------------------------------------|
| `extracellular-arf`    | arfx            | `arfx-oephys`                             |
| `spikes-pprox`         | melizalab-tools | `group-kilo-spikes`                       |
| `spikes-hdf5`          | melizalab-tools | `group-kilo-spikes`                       |
| `intracellular-abfzip` | melizalab-tools | `abf-zip` (new); the `-abfdir` migration  |

## What exists, and where

- **neurobank master (unreleased, 1.0):**
  - **reader support in deposit:** discovery, checking, merging, the CLI options;
  - **`nbank.canonical_zip`:** `write_zip(out_path, members)` and
    `zip_directory(src_dir, out_path, extra=[("metadata.json", bytes)])` write
    the same bytes for the same files;
  - **`nbank.zipped`:** `members`, `member_extent` (offset and size, for
    memory-mapping), `open_member`, `extract_member`;
  - **deposit refuses directories;** `--zip-directories` builds a canonical zip in
    the archive's `staging/` directory and deposits that;
  - **`archive.staging_dir(cfg)`:** archives have `staging/`; `nbank check
    archive --fix` creates it in older archives.

  The last release is 0.12.1, and melizalab-tools pins `neurobank>=0.12,<0.13`.
  Readers don't import neurobank, so they don't care. `abf-zip`, and the
  migration, use `canonical_zip`, so until neurobank 1.0 is released they need
  neurobank from git.
- **django-neurobank 0.12.0 (API 1.2, in production):** the `experimenter` and
  `animal` fields; animals are `birds.Animal` records, by UUID. A resource's
  dtype can be changed through the API, and a superuser can change its sha1, which
  is what the migration needs. `filename` is the name plus the dtype's extension,
  so it follows a change of dtype.
- **melizalab-specs 0.1.0 (lab-specs, on PyPI):** the schemas, including abfzip's.
  `melizalab_specs.validate(document)` checks a document against the schema named
  in its `$schema`; it needs the `validate` extra. melizalab-tools should depend
  on `melizalab-specs[validate]` and drop its copies in `dlab/schemas/`.
- **Not built yet:** the four readers; `abf-zip`; the migration; the
  `intracellular-abfzip` dtype in the registry (extension `zip`, content type
  `application/vnd.meliza-org.abf+zip`; the registry's content types carry no
  version).

## Discovery

- Readers are entry points in the group `nbank.metadata_readers`. The entry
  point's name is the dtype, as registered in the registry:

  ```toml
  [project.entry-points."nbank.metadata_readers"]
  extracellular-arf = "arfx.nbank:read_metadata"
  ```

- Deposit looks up the reader for the dtype given with `-d`/`dtype=`. With no
  dtype, or no reader installed for it, deposit uses only the metadata given, and
  logs that no reader was found.
- Two installed packages claiming the same dtype is an error: deposit refuses to
  start and names both packages.
- Readers are loaded only when needed, so a broken plugin for one dtype doesn't
  affect deposits of another.

## The reader contract

```python
def read_metadata(path: pathlib.Path) -> dict[str, Any]: ...
```

- **No dependency on neurobank.** The reader is a plain function taking a path,
  so the producer packages aren't tied to neurobank's version. Depending on
  melizalab-specs, to validate against a schema, is fine.
- **Returns** the metadata for the registry, as JSON-serializable values:
  - `experimenter` (a registry username) and `animal` (the animal's full
    identifier; a lowercase colony UUID in our lab) are the resource fields;
    every other key is metadata.
  - The agreed names, not legacy ones: a reader for files written before the
    producers were fixed maps `bird` to `animal` and `experiment` to `protocol`.
    The producer knows its own history; neurobank doesn't.
  - Only what's known: a key with no value is left out, never returned as None.
  - Nothing the registry sets itself (name, sha1, dtype, locations).
- **Validates the file.** If the file isn't a well-formed file of the dtype
  (wrong format, missing required structure or attributes, inconsistent
  contents), the reader raises `ValueError` with a message for the user. Deposit
  treats any other exception as a failure of the reader, and reports it with its
  type.
- **Read-only and cheap.** It must not change the file, and should read headers
  and attributes rather than the data, since it runs on every file deposited.
- **Pure.** No registry access and no network: a file's metadata depends only on
  the file.

## Deposit

- **When:** after the existence, symlink, and permission checks, and before
  hashing, so a file that isn't what it claims to be fails before the slow step.
- **Merging with what the user gives** (`--experimenter`, `--animal`, `-k`; in
  the library, `metadata={...}`):
  - keys only the file has, or only the user gave, are all used;
  - a key both have with the same value is fine;
  - a key with different values fails that file, naming the key and both values,
    unless the user asks for their values to win (`--override-file-metadata`,
    `override_file_metadata=True`), which is logged per key.
- **Failures:** a file whose reader fails isn't registered or moved. With
  `skip_errors` (the CLI default) it yields `{"source", "error"}` and deposit
  moves on, as for other per-file errors.
- **Dry run:** runs the readers and shows the merged metadata for each file, in
  the log and in `-j` output (`"metadata"` in each record).
- **Directories:** deposit refuses them. With `--zip-directories`, the reader is
  given the zip built from the directory, not the directory. Dry runs don't build
  the zip, so they don't run the reader for directories.
- **Opt out:** `--no-file-metadata` (`read_metadata=False`) skips the reader.

## The readers

### `extracellular-arf` (arfx)

- **Validation:** an HDF5 file with the ARF root attributes, at least one entry,
  and in each entry the datasets `arfx-oephys` writes (continuous extracellular
  data with a sampling rate).
- **Which attributes:** entries hold the user's `-k` attributes together with
  attributes from the open-ephys structure and ARF's own (`timestamp`, `uuid`,
  `entry_creator`). `arfx-oephys` records the names of the attributes that came
  from `-k` (and from `--experimenter`/`--animal`, if added) in an entry
  attribute, and the reader takes exactly those. Files written before that have
  no such list; see the open questions.
- **Differences between entries:** the registry describes the file as a whole,
  so an attribute is registered only if it has one value for the file. When
  entries differ, some differences are acceptable (the attribute is left out) and
  others mean the file is inconsistent (the reader raises `ValueError`); see the
  open questions.
- **Producer changes:** `arfx-oephys` records the names of the `-k` attributes in
  each entry. Users write `animal` (a full colony UUID) and `experimenter` (a
  username) instead of `bird`; ideally `arfx-oephys` gets
  `--experimenter`/`--animal` options and checks the UUID form, to catch errors
  when the file is made rather than when it's deposited.

### `spikes-pprox` (melizalab-tools)

- **Validation:** JSON with a known pprox `$schema` and a `pprox` list of trials.
  Checking the whole file against the schema (`melizalab_specs.validate`) is
  optional, since the files can be large; checking the top level is required.
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

### `intracellular-abfzip` (melizalab-tools)

Replaces the `intracellular-abfdir` directories (1,976 resources, all in disk
archives), which 1.0 retires.

- **Spec:** [4/abfzip](https://meliza.org/spec:4/abfzip) in lab-specs
  (`specs/abfzip.md`, schema `specs/abfzip.json`, in melizalab-specs). MIME type
  `application/vnd.meliza-org.abf+zip; version=1.0`.
- **The archive:** `metadata.json` at the top level; at least one ABF file (a
  member named `*.abf`, any case) with a valid header; other members allowed (the
  migration carries over whatever the directories hold); members stored
  uncompressed; relative paths without `..`.
- **`metadata.json`:** an envelope of `$schema`
  (`https://meliza.org/spec:4/abfzip.json#` for 1.x), `version` (`"1.0"`),
  `metadata` (the registry metadata of the recording as a whole), and `catalog`
  (records for individual members: `path`, the acquisition `protocol` the file
  was recorded with, its `start` time, and any other keys; may be empty).
  `metadata.protocol` is the experiment's protocol; `catalog[].protocol` is the
  acquisition protocol for one file. No `null` values anywhere.
- **Reader:** checks `$schema` and `version` (refusing a major version it doesn't
  know), validates `metadata.json` (`melizalab_specs.validate`), checks the ABF
  members as above and that every catalog `path` names a member only once, and
  returns `metadata` as it is. The catalog stays in the file for analysis; it
  isn't registered.
- **Producer, `abf-zip`:** reads the ABF headers for the catalog (protocol and
  start time of each file), takes `--experimenter`, `--animal`, and `-k` for
  `metadata`, validates the result, and builds the zip with
  `canonical_zip.zip_directory(src, out, extra=[("metadata.json", data)])`.
  Everything in `metadata.json` is fixed by the zip's hash once it's built, so
  the catalog should be reviewed first: e.g. `abf-zip` writes `metadata.json` for
  review, then builds the zip from the reviewed file (`--metadata FILE`). It can
  deposit the zip itself for a one-step workflow at the rig. (`nbank deposit
  --zip-directories` doesn't write `metadata.json`, so the abfzip reader rejects
  what it makes; ABF recordings go through `abf-zip`.)
- **Migration of `intracellular-abfdir`:** per resource,
  1. verify the directory against its registered hash;
  2. build the zip in the archive's `staging/` (`archive.staging_dir`) with the
     same builder as `abf-zip`, with `metadata` from the resource's registry
     record, and the catalog from the ABF headers and the epoch lists of earlier
     analyses, reviewed recording by recording;
  3. change the resource's sha1 (as a superuser) and dtype
     (`intracellular-abfdir` to `intracellular-abfzip`) in the registry, store
     `<id>.zip`, and remove the directory.

  Dry run; resumable; one archive at a time. Before it, run `nbank check archive
  --fix` on each archive (it creates `staging/`), and register the
  `intracellular-abfzip` dtype. After it, run `nbank check all`. Whether this
  lives in `nbank.admin` (with melizalab-tools building the zips) or in
  melizalab-tools (calling neurobank) is open.
- **Reading:** `nbank.zipped` opens members, gives their offsets for
  memory-mapping, or extracts them. ABF readers take paths only: neo's
  `AxonRawIO` (which memory-maps, and has lighter dependencies than pyabf) and
  pyabf (which reads into memory, and requires matplotlib and pytest at run
  time). So analysis code starts by extracting members. If memory-mapping matters,
  a small `AxonRawIO` subclass in melizalab-tools can add `member_extent`'s offset
  to the offsets of neo's memory-mapped buffers (pinned to a neo version range),
  and neo could be asked for offset or file-object support.

## Problems for reader authors to watch for

Found while writing neurobank's side against a fake reader. Where a value can't be
stored, neurobank refuses the file with a message naming the key.

- **numpy and bytes from HDF5:** h5py returns attributes as numpy scalars
  (`np.int64`, `np.float32`), numpy arrays, and sometimes `bytes` or
  `np.bytes_` for strings. None of these are JSON, so a reader for an HDF5-based
  dtype has to convert them (`.item()`, `.tolist()`, `.decode()`). `np.float64`
  happens to pass, as a subclass of `float`, which can hide the problem in tests.
- **NaN and infinity:** HDF5 attributes can hold them, JSON and Postgres can't.
  Leave such keys out.
- **Types that don't match what users type:** `-k pen=1` gives the number 1, so a
  reader returning the string `"1"` disagrees with it and the file fails. Return
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
- **The entry point name must match the dtype exactly.** If a dtype is renamed in
  the registry, its reader is silently not found (deposit logs "no metadata
  reader"), and files are deposited without their metadata.
- **One reader per dtype across all installed packages:** two packages claiming a
  dtype (say, two versions of a producer in one environment) stop deposits of
  that dtype until one is removed.
- **A reader that can't be imported** (a missing dependency such as h5py) stops
  every deposit of its dtype; `--no-file-metadata` gets around it. Readers should
  import their heavy dependencies inside the function only if the module itself
  is imported for other reasons.
- **Errors:** a `ValueError`'s message is shown to the user as the reason the
  file was skipped, so it should say what's wrong with the file. Any other
  exception is reported with its type, and its traceback is logged at debug level
  (`nbank --debug`).

## Testing

- **neurobank (done):** a fake reader registered as an entry point
  (monkeypatching `importlib.metadata.entry_points`), covering merging,
  conflicts, override, dry runs, `skip_errors`, no reader, two readers for one
  dtype, failing readers, values that can't be stored, the opt-out, and readers
  given zips of directories. Live tests deposit through a fake reader to check the
  fields reach the registry. `test/test_readers.py` shows the fake reader.
- **melizalab-specs (done):** the abfzip schema rejects malformed
  `metadata.json` and accepts newer minor versions.
- **Producers:** each reader tested against small files written by the producer
  (and by older versions, for the legacy names); `abf-zip` tested for
  reproducible output and a `metadata.json` that validates.

## Open questions

- [ ] **ARF differences between entries:** which are acceptable, so the attribute
  is left out, and which make the file fail? For instance, entries with different
  `experimenter` or `animal` values are probably an error, while some recording
  parameters may legitimately vary. Decide while writing the reader.
- [ ] **ARF files without the list of `-k` attributes** (written before the
  `arfx-oephys` change): read nothing, use a fixed list of known keys, or
  something else?
- [ ] **Override:** deposit refuses on disagreement unless
  `--override-file-metadata` (built). Keep that, or let the given values always
  win, with a warning?
- [ ] **Inheritance:** pprox and spikes HDF5 files name their `recording`.
  Instead of the producers copying `experimenter` and `animal` in, deposit could
  look the recording up and fill them in. That makes readers depend on the
  registry, which the contract rules out; copying in the producer keeps the files
  self-describing. Copying is the proposal here.
- [ ] **Where the abfzip migration lives:** `nbank.admin` with melizalab-tools
  building the zips, or melizalab-tools calling neurobank.
- [ ] **Spec number:** check that `spec:4` isn't already used on meliza.org.
- [ ] **Later:** `nbank check` could run the readers against stored files and
  report where the registry and the file disagree.

Decided:

- [x] **ARF attributes:** `arfx-oephys` records which attributes came from `-k`,
  and the reader takes exactly those.
- [x] **abfzip migration and the catalog:** built with melizalab-tools' zip
  builder; catalogs filled from the ABF headers and the epoch lists of earlier
  analyses, reviewed recording by recording.
