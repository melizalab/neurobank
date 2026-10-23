## 1.0.0 (unreleased)

Requires django-neurobank 1.0 (registry API 2.x).

### Breaking

- Every request asks for registry API 2.0 (`Accept: application/json;
  version=2.0`), and nbank checks once per registry that `info/` reports API
  2.x, raising `nbank.util.RegistryVersionError` (an error message in the CLI)
  if it doesn't. `nbank registry-info` skips the check, so it can show any
  registry's version.
- `core.get` and `core.find` are removed; use `core.find_resource(s)`, which
  return paths, or `nbank locate` for every location of a resource.
  `archive.iter_resources` is removed; use `check.check_archive_contents`.
- `nbank.util.registry_session()` yields an HTTP client for the registry that
  asks for API 2.0 and checks the registry's version. Code that changes the
  registry with its own client gets a 400 from it; the `query_registry`
  functions still work with any client, as reads need no version.
- `nbank search` and `core.search` leave out superseded resources;
  `--superseded` (`superseded=True`) finds only them, and `--all`
  (`superseded=None`) both. `core.verify` still finds a file's resource if it's
  superseded.

### New

- `nbank.admin supersede` marks resources as superseded, or not to be used.
- `nbank registry-info` shows who the registry sees you as, and what you can
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
  invalid dtype, experimenter, animal or metadata is an error for that file.
  Dry runs now need permission to deposit (#11).
- `nbank fetch --verify` checks each file against its registered hash, trying the
  resource's other locations if it doesn't match (`util.fetch_resource(sha1=)`),
  and warns about resources with no hash; `-@` reads ids from stdin (#8).
- `nbank check superseded` reports superseded and unregistered resources among
  the given ids (or ids on stdin, with `-@`), and exits 1 if there are any.

### Fixed

- `util.query_registry` and `query_registry_bulk` use the session's credentials;
  they sent requests without them unless given `auth`.

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
