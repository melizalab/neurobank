## 1.0.0 (unreleased)

Requires django-neurobank 1.0 (registry API 2.x).

### Breaking

- `archive.get_config` is `archive.ArchiveConfig.load`. It and `archive.create`
  return an `archive.ArchiveConfig`, a frozen dataclass (`path`, `registry`, and
  `policy`, an `archive.Policy` with an `archive.Access`), instead of the dict
  read from `nbank.json`: `cfg["path"]` is `cfg.path`, and
  `cfg["policy"]["access"]["umask"]` is `cfg.policy.access.umask`. `nbank.json` is checked when it's read, and a
  missing or wrongly typed setting raises `ValueError` naming it; keys nothing
  reads (`project`, `owner`) are ignored. An archive whose `nbank.json` has no
  `read_only_resources` is read-only, as new archives are: `check archive`
  reports its writable resources, and `--fix` makes them read-only.
- `nbank search -n/--archive` matches the archive's name exactly (the registry's
  `archive` filter) instead of any archive whose name contains it.
- CLI commands are reorganized, with no aliases for the old names: `nbank init`
  is `nbank archive init`, and `nbank archive register-tar` is `nbank archive
  register tape`, the first of a command per archive scheme. `nbank archive
  check` is gone; use `nbank check archive`, which now also takes the archive's
  name in the registry as well as its path. `nbank registry-info` is `nbank
  registry info`.
- `nbank copy` and `nbank archive import-tar` use the registry in the
  destination archive's `nbank.json`, as `deposit` does, instead of `-r` or
  `NBANK_REGISTRY`.
- Every request asks for registry API 2.0 (`Accept: application/json;
  version=2.0`), and nbank checks once per registry that `info/` reports API
  2.x, raising `nbank.util.RegistryVersionError` (an error message in the CLI)
  if it doesn't. `nbank registry info` skips the check, so it can show any
  registry's version. Versions before 1.0 can still read from a django-neurobank
  1.0 registry (`search`, `info`, `locate`, `fetch`, `verify`, `check`), finding
  files without keys, but the registry refuses their changes.
- `core.verify(registry_url, file, id)` returns whether file matches resource
  id; `core.find_by_hash(registry_url, file)` yields the records with file's
  hash, which is what `core.verify` without `id` did.
- `core.get` and `core.find` are removed; use `core.find_resource(s)`, which
  return paths, or `nbank locate` for every location of a resource.
  `archive.iter_resources` is removed; use `check.check_archive_contents`.
- Resources in neurobank archives are found by their location's key, the name
  of the file, instead of by looking for any file named with the id. A location
  without a key, or whose key names no file, isn't found on this host (#60).
  django-neurobank 1.0 sets each existing location's key to the resource's file
  name when it's migrated; then run `nbank check archive --fix --no-hash` on
  each archive to correct the few files named otherwise.
- The `keep_extensions` policy is gone: files always keep their extensions in
  the archive. Archives' `nbank.json` can keep it; `false` is ignored with a
  warning.
- Directory resources are gone (all have been converted to zip files):
  `transfer.receive_directory` is removed, and `export`, `copy`, `archive
  import-tar` and `check tar` report a directory where a resource should be as
  an error for that resource ("is a directory, which isn't a resource"), skipping
  its contents in a tar file.
- `util.DirectoryHasher` and `util.hash_directory` are removed, and `util.hash`
  only hashes files. The `allow_directories` policy is gone (archives' `nbank.json`
  can keep it; it's ignored), and `archive.store_resource` refuses directories.
  `nbank check archive` reports a directory where a resource should be (status
  `DIRECTORY`) without reading it, and `--fix` leaves it alone; `archive register
  tape` refuses a directory with a registered id, and skips its contents; `nbank
  verify` refuses a directory.
- `nbank.util.registry_session()` yields an HTTP client for the registry that
  asks for API 2.0 and checks the registry's version. Code that changes the
  registry with its own client gets a 400 from it; the `query_registry`
  functions still work with any client, as reads need no version. Its `auth`
  is sent only to the registry's host (scheme, host and port), never to hosts
  that locations name, and needs `registry_url`; netrc credentials (the
  default) go to each host that has an entry. `check_version=False` skips the
  version check.
- `nbank search` and `core.search` leave out superseded resources;
  `--only-superseded` (`superseded=True`) finds only them, and
  `--with-superseded` (`superseded=None`) both. `core.find_by_hash` still finds a file's resource if
  it's superseded.
- `nbank deposit --dry-run` needs permission to deposit, as it asks the
  registry to check each resource (see below).
- Automatic ids (`deposit -A`, or the `auto_identifiers` policy) are made by
  nbank rather than the registry: eight random base-36 characters, the form the
  registry used, named `"base36"` in the policy's `auto_id_type` (also what
  `null` means), or `"uuid"`. If the registry already has an id, deposit tries
  another (up to three). Any other `auto_id_type` is an error, and dry runs give
  the id that would be used.

### New

- `nbank.script.ParseKeyVal`, the argparse action behind `-k`, is public, for
  programs that take metadata the way `nbank deposit` does. The API reference
  has a page for `nbank.script`.
- `nbank fetch --tar TAR` gets resources out of a tar file, a tape device, or
  standard input, without importing them into an archive (#65). A member is a
  resource if its name without directories or extension is the id, wherever
  it is in the tar file; each is checked against its registered hash, and one
  that doesn't match is kept with `.mismatch` added to its name.
- `nbank copy --restore` and `nbank archive import-tar --restore` restore a
  resource the registry lists in the destination archive whose file is missing
  there: it's stored again (checked against its hash) and its location's key is
  set, and the summary counts these separately (#65). Without `--restore`,
  such resources are still skipped as already in the destination.
- New locations in neurobank archives record the stored file's name as their
  key: deposit, `copy`, `archive import-tar`, and adding a location with `check
  archive --fix`. `archive register tape` records each resource's tar member as
  the key of its tape location (#60).
- `nbank check archive` (and `check all`) reports a resource whose location has
  no key, or a key that isn't the name of its file, as an error, and `--fix`
  sets it. It also counts the files named otherwise than the registry's
  `filename`. `check.check_archive_contents` takes the keys as `keys=`.
  `--fix --rename` renames those files to the registry's `filename` and sets
  their keys.
- `nbank check archive` runs the metadata reader of each file's dtype, if one
  is installed, and reports a file it rejects as not valid for its dtype (an
  error), suggesting any other dtype with the file's extension whose reader
  accepts it. `--fix` and `--rename` leave such files' keys and names alone.
  `--no-hash` skips the readers. `check.check_archive_contents` takes the
  dtypes and a `check.DtypeReaders` as `dtypes=` and `dtype_readers=`. Needs
  the registry's `locations/` to give each resource's dtype.
- Documentation on Read the Docs (https://neurobank.readthedocs.io): guides to
  archives, depositing, searching, accessing and managing resources, and
  references for the CLI and the Python API. The README is now a short tour.
- nbank and `nbank.admin` run on Windows as clients: commands that write or check
  archives (`archive init`, `deposit`, `copy`, `archive import-tar`, `prune` and
  `register tape`, `check archive` and `check all`, and `nbank.admin delete` and
  `update-hash`) refuse there with a message, as do the library functions that
  write or check archives (`archive.ArchiveHostError`). Output is written as
  UTF-8, so names and notes print even when output is redirected. Credentials
  are read from `~/_netrc` when there's no `~/.netrc`. `nbank locate -L` says
  when Windows won't let you create symbolic links, and reports a link it can't
  make rather than failing with a traceback. Downloads work there: URLs are
  built with forward slashes, and the cache's directory for a host with a port
  is named `host_port` on every platform, as Windows doesn't allow `:` in file
  names.
- `nbank locate`, `fetch`, `export` and `copy` take `--alt-base DIR` (or the
  `NBANK_ALT_BASE` environment variable), to read archives mounted under `DIR`
  instead of at their registered roots, as `find_resources(alt_base=)` does.
- `nbank.admin supersede` marks resources as superseded, or not to be used.
- `nbank registry info` shows who the registry sees you as, and what you can
  change: a username and permissions, a superuser, or anonymous (#61).
- `core.find_resources` and `find_resource` give the path of a file for each
  resource that can be read on this host: from a directory of files named by id
  (`local_dir`), an archive on this host, or a download into a local cache
  (`NBANK_CACHE`, or `neurobank` in the user cache directory). Downloads are
  stored under the cached name only when complete; `verify=True` checks hashes.
  These come from `dlab.neurobank` in melizalab-tools.
- `nbank deposit --dry-run` (`core.deposit(dry_run=True)`) asks the registry
  whether it would accept each resource (`check/resources/`, in batches of
  1000): a taken name or hash, one shared with another file in the deposit, or an
  invalid dtype, experimenter, animal or metadata is an error for that file
  (#11).
- `nbank modify --dtype` (`core.update(dtype=)`) changes resources' datatype
  (#62). `metadata` is optional in `core.update`.
- `nbank fetch --verify` checks each file against its registered hash, trying the
  resource's other locations if it doesn't match (`util.fetch_resource(sha1=)`),
  and warns about resources with no hash; `-@` reads ids from stdin (#8).
- `nbank check superseded` reports superseded and unregistered resources among
  the given ids (or ids on stdin, with `-@`), and exits 1 if there are any.
- `nbank verify --metadata` reads each file with its dtype's metadata reader and
  checks what it reads against the registry; a disagreement, or a file the
  reader can't read, exits 1.
- `nbank dtype list` shows the package and version providing each installed
  metadata reader.
- `nbank deposit` warns when no dtype is given, as the files' metadata isn't
  read; with `--debug`, it shows which reader handles the dtype and what it
  read from each file.

### Changed

- When the registry has no archive for a directory (`deposit`, `copy`,
  `archive import-tar`, `check archive`), the error says that an archive is
  registered at its real path, and that a host reaching it through a symbolic
  link needs a bind mount at the registered path instead (#65).
- `nbank check archive PATH` checks against the registry given with `-r`, or
  else the one in the archive's `nbank.json`, and needs neither `-r` nor
  `NBANK_REGISTRY`. An archive whose `nbank.json` names a different registry
  from the one checking it is a warning, not an error, in `check archive` and
  `check all`, so archives can be checked against a copy of the registry.
- Searches (`nbank search`, `core.search`) and `nbank check` get every result in
  one streamed response (`util.query_registry_stream`), instead of a page at a
  time, which is much faster for large archives. Registries that can't stream
  are paged through as before. `nbank check archive` gets an archive's
  resources, hashes and location keys in one streamed request to the
  registry's `locations/` (`registry.find_locations`), instead of a search and
  then a `bulk/locations/` request per 1,000 resources.
- `nbank archive import-tar` ends with a summary line, as `copy` and `export`
  do. When a resource's only copies are offline (on tape), `copy` and `export`
  name those archives and say to read it with `nbank archive import-tar`.

### Fixed

- A `-k` argument without exactly one `=` is a usage error naming the option,
  instead of a traceback. `ParseKeyVal` no longer changes the parser's default
  dict, which carried values from one parse to the next.
- `nbank registry info` reports an address that isn't a registry (no `info/`)
  as an error instead of crashing.
- The `repr` of an http(s) resource no longer raises `AttributeError`.
- A command whose output goes to a reader that stops early (`nbank search ... |
  head`) exits quietly with status 141, as if killed by SIGPIPE, instead of
  printing a BrokenPipeError.
- `nbank.admin` exits with status 1 when a resource can't be deleted, updated or
  marked (including one that isn't in the registry, or for `update-hash` has no
  copy on this host), and reports registry errors instead of a traceback.
- `util.query_registry` and `query_registry_bulk` use the session's credentials;
  they sent requests without them unless given `auth`.
- A copy in an archive whose directories this user can't read is treated as not
  on this host; before Python 3.14, `locate`, `fetch` and
  `core.find_resources` stopped with a PermissionError.

## 0.13.0

This is a transitional release implementing the machinery to migrate
intracellular-abfdir resources to intracellular-abfzip. Not intended for general
use as a CLI or a dependency, as there are lots of breaking API changes that
should not be considered finalized. Requires django-neurobank 0.12.0 (registry
API 1.2) or later.

### New

- `nbank deposit`, `search` and `modify` take `--experimenter` (a registry
  username) and `--animal`, which the registry stores as resource fields rather
  than metadata. `search --animal` matches the start of the animal's id, and
  `modify` clears a field given an empty value. In the library they're the
  `experimenter` and `animal` keys of `metadata=`.
- Deposit reads registry metadata from the files themselves, using reader plugins
  that the packages producing each dtype register as `nbank.metadata_readers`
  entry points. A file the reader rejects isn't deposited, and metadata given
  with `-k` that disagrees with the file fails it. `--no-file-metadata` skips the
  reader, and `--override-file-metadata` lets the given values win. Dry runs show
  the metadata that would be registered.
- `nbank deposit --zip-directories` deposits each directory as a zip file, built
  in the archive's new `staging/` directory. `nbank check archive` reports a
  missing `staging/` (`--fix` creates it) and leftovers in it.
- `nbank.canonical_zip` writes zip files that are the same bytes for the same
  contents: members stored uncompressed and sorted, with fixed timestamps and
  permissions.
- `nbank.zipped` reads members of zip resources without unpacking them: their
  offsets for memory-mapping, file objects, or extracting one member.
- `nbank.admin delete --record FILE` appends the deleted registry records to a
  JSON lines file.

### Changed

These may break scripts or code that calls nbank:

- `nbank deposit` refuses directories, whatever the archive's `allow_directories`
  policy. Convert them to zip files first (for ABF recordings, `abf-zip` in
  melizalab-tools), or use `--zip-directories`.
- `-k` and `-K` values are read as JSON, and anything that isn't JSON is kept as a
  string (#64). Before, they were read as Python literals, so `20140603_1_1`
  became the number 2014060311. `True`, `False` and `None` are still accepted.
  Python-only forms are now strings: hex, octal and underscored numbers, `1.`,
  `.5`, `+2`, and single-quoted strings. A value starting with `[` or `{` that
  doesn't parse is an error. Quote a string that looks like a number:
  `-k 'name="20140603"'`.
- `-k` and `-K` refuse `experimenter` and `animal`; use the options. The lab alias
  `bird` isn't translated.
- `core.deposit` and `core.update` take metadata as a dict (`metadata=`) instead of
  keyword arguments, so any key can be metadata.
- Optional arguments are keyword-only in `core.deposit`, `core.find`, `core.get`,
  `core.verify`, `archive.create`, `archive.store_resource`,
  `check.check_archive_contents`, `check.check_archive_permissions`,
  `util.query_registry` and `util.query_registry_bulk`. The registry URL builders'
  and `core.search`'s fixed arguments are positional-only.
- Deposit checks that nothing is already stored under the name before
  registering it (#59). `archive.verify_permissions` raises FileExistsError for
  that case.

### Fixed

- `-k` with a key such as `archive`, `dtype`, `sha1` or `id` crashed deposit.

## 0.12.1

### New

- `nbank archive import-tar` can import only some of the resources in a tar file:
  list their identifiers after the archive path, or in a file with `-f`. Reading
  stops once they've all been found, and requested resources that aren't in the
  tar file are reported as failures.

### Fixed

- When a tar file ends partway through a resource (for example, a tape that ran
  out of space), `check tar` and `import-tar` now report that resource as failed.
  Before, it wasn't counted at all, and `check tar --archive` didn't report it
  as missing.
- Tar files on disk are read with seeking, so `import-tar` skips resources it
  doesn't need without reading them, and `check tar` and `import-tar` hash files
  in tar files faster. Tapes and standard input are still read in order.

## 0.12.0

Requires Python 3.11 and django-neurobank 0.11.0 (registry API 1.1) or later.

### New

- `nbank check`: `check registry` finds resources without locations and empty
  archives; `check archive` checks an archive's files, hashes, ownership, and
  permissions (`--fix`, `--no-hash`); `check all` checks the registry and then every
  archive on this host; `check tar` checks a tar file or tape against the registry
  (`--archive` also finds resources missing from it). `nbank archive check` is kept
  as an alias.
- `nbank export` writes resources to a tar file, zip file, or directory, checking
  their hashes as it reads them. `--manifest` adds their registry records as
  `manifest.json` (#29).
- `nbank copy` copies resources between archives on this host, checking hashes and
  adding the new locations (#30).
- `nbank archive import-tar` checks each resource against its registered hash before
  storing it, reads directly from a tape drive or stdin, and shows progress.
- `nbank init --shared` and `-g/--group` set up archives that several users deposit
  into. New archives make deposited resources read-only (`read_only_resources`).
- `nbank deposit -y/--dry-run` checks the files, dtype, and archive without
  registering anything (#14).
- `nbank search -s/--scheme` filters by location scheme, and `-H` with a full hash
  is an exact match.
- Locations can record a `key`, and location scheme classes are registered with
  the `nbank.types.location_scheme` decorator.

### Changed

These may break scripts or code that calls nbank:

- Commands exit with status 1 when they fail (130 when interrupted).
  `nbank archive check` now fails on ownership and permission problems.
- `nbank deposit` continues past files it can't deposit and exits 1 at the end. It
  refuses sources that are or contain symbolic links, and stops before hashing
  anything if the dtype isn't registered.
- `nbank init` refuses to overwrite an existing archive's files, and doesn't set a
  default ACL.
- Deposits set ownership and permissions from the archive's policy, adding group
  read and setgid where the policy requires them.
- `core.deposit` takes `dry_run` and `skip_errors`, and raises RuntimeError for an
  unregistered dtype, ValueError for symbolic links, and PermissionError naming
  the path.
- `archive.check_permissions` is removed; `archive.verify_permissions` raises
  PermissionError instead of returning False.
- `archive.resource_path`'s second parameter is renamed from `id` to `name`.
- `archive.store_resource` raises KeyError if a file is already stored for the id
  under any extension, and copies instead of moving when the source's directory
  isn't writable.
- `archive.iter_resources` is deprecated; use `check.check_archive_contents`.
- nbank versions before 0.12 can't deposit into shared archives as root.
- With django-neurobank 0.11, searching for part of a hash (`nbank search -H`, or
  `core.search(sha1=...)`) finds nothing in nbank versions before 0.12, which send
  it as an exact match. Full hashes work in every version.

### Fixed

- Wheels no longer install README.rst into site-packages.
- `nbank archive check` reported resources as missing when another archive's name
  contained the archive's name, and crashed on stray files in `resources/`.
- Deposit errors name the path and the problem (#36).

## 0.3.0

- rename `files` field to `resources` in catalog files
- all data files are stored under `resources` instead of `sources` and `data`
- `id` subcommand is now `search`
