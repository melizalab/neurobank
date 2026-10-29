## 1.0.0 (2026-10-28))

After using neurobank for over ten years, it's finally time for a 1.0 release.
This release is synchronized with and requires django-neurobank 1.0 (registry
API 2.x). A lot of unused or difficult to use features were removed, like
directory resources, optional extensions, and registry-generated identifiers. We
only ever used directories for collections of intracellular ABF files, which now
get stored in zip files. There's tooling for reading zip file members directly
without unpacking them, and additional tooling in melizalab-tools for packaging
this kind of data and accessing it with neo.io functions. The CLI commands for
copying and moving resources between tape and disk archives has been cleaned up
and organized, and it should be easier now to to export datasets for deposit to
dataverse instances and public archives like figshare and DANDI. Management of
ownership and access control in neurobank archives has been improved, and the
`nbank check` commands first introduced in 0.13 have been organized and expanded
to deal with a wider range of problems. The documentation has also been
organized and updated.

Licensed under GPL v2 again (0.13 said BSD 3-Clause), not that the clankers care.

### Breaking

- Every request asks for registry API 2.0, and nbank checks once per registry
  that it's 2.x (`util.RegistryVersionError` otherwise; `nbank registry info`
  skips the check). Versions before 1.0 can still read from a 1.0 registry,
  but it refuses their changes.
- `util.registry_session()` is the registry's client: it asks for API 2.0 and
  checks the version (`check_version=False` skips it). Its `auth` goes only to
  the registry's host; netrc credentials go to each host that has an entry.
- Files in neurobank archives are found by their location's key, the file's
  name, without a glob (#60). A location without a key, or whose key names no
  file, isn't found. django-neurobank 1.0's migration sets the keys; then run
  `nbank check archive --fix --no-hash` on each archive.
- Directory resources are gone (all are now zip files). A directory where a
  resource should be is an error in `export`, `copy`, `archive import-tar`,
  `check tar`, `check archive` (`DIRECTORY`), `archive register tape` and
  `verify`. `transfer.receive_directory`, `util.DirectoryHasher` and
  `util.hash_directory` are removed, `util.hash` and `archive.store_resource`
  take only files, and the `allow_directories` policy is ignored.
- The `keep_extensions` policy is gone: files always keep their extensions.
- CLI commands are reorganized, without aliases: `nbank init` is `nbank archive
  init`, `archive register-tar` is `archive register tape`, `registry-info` is
  `registry info`, and `archive check` is gone (`check archive` also takes the
  archive's name in the registry).
- `nbank copy` and `archive import-tar` use the registry in the destination
  archive's `nbank.json`, as `deposit` does.
- `nbank search` and `core.search` leave out superseded resources
  (`--only-superseded` or `superseded=True` for only them, `--with-superseded`
  or `superseded=None` for both).
- `nbank search -n/--archive` matches the archive's name exactly.
- Automatic ids are made by nbank: `auto_id_type` is `"base36"` (8 characters,
  the default) or `"uuid"`, and deposit tries another if one is taken.
- `nbank deposit --dry-run` needs permission to deposit, as the registry checks
  each resource.
- `archive.get_config` is `archive.ArchiveConfig.load`. It and `archive.create`
  return a frozen `ArchiveConfig` dataclass instead of a dict (`cfg.path`,
  `cfg.policy.access.umask`), and `nbank.json` is validated when read. An
  archive without `read_only_resources` is read-only.
- `core.get`, `core.find` and `archive.iter_resources` are removed: use
  `core.find_resource(s)`, `nbank locate`, or `check.check_archive_contents`.
- `core.verify(registry_url, file, id)` checks a file against one resource;
  `core.find_by_hash` does what `core.verify` without an id did.
- `registry.log_error` logs every client error (4xx) and re-raises server
  errors (5xx), which `nbank` and `nbank.admin` report as `registry error:
  <status>`, exiting 1.

### New

- Documentation at https://neurobank.readthedocs.io: guides, and references for
  the API.
- `core.find_resources` and `find_resource` (from melizalab-tools'
  `dlab.neurobank`) give a readable path for each resource: in `local_dir`, an
  archive on this host, or a cached download (`NBANK_CACHE`). `verify=True`
  checks hashes.
- `--alt-base DIR` (or `NBANK_ALT_BASE`) for `locate`, `fetch`, `export` and
  `copy` reads archives mounted somewhere other than their registered roots.
- `nbank fetch --verify` checks hashes, trying other locations, and `-@` reads
  ids from stdin (#8). `fetch --tar` reads resources from a tar file or tape
  without importing them (#65).
- `copy --restore` and `archive import-tar --restore` restore files missing
  from the destination archive (#65).
- New locations record their key: deposit, `copy`, `archive import-tar`, `check
  archive --fix`, and `archive register tape` (the tar member) (#60).
- `nbank check archive` reports missing or wrong keys (`--fix` sets them,
  `--fix --rename` renames files to the registry's `filename`), and checks each
  file with its dtype's metadata reader unless `--no-hash`.
- `nbank deposit --dry-run` checks each resource with the registry (#11).
- `nbank modify --dtype` changes resources' datatype (#62).
- `nbank verify --metadata` checks files' metadata against the registry.
- `nbank check superseded` reports superseded and unregistered ids, exiting 1
  if there are any; `nbank.admin supersede` marks resources superseded.
- `nbank registry info` shows your username and permissions (#61).
- `nbank dtype list` shows which package provides each metadata reader;
  `deposit` warns without a dtype, and shows what the reader read with
  `--debug`.
- Windows as a client: commands that write or check archives refuse there.
  Output is UTF-8, and credentials can be in `~/_netrc`.
- `registry.check_resources`, `registry.field_messages` and
  `script.ParseKeyVal` are public.

### Changed

- Searches and `nbank check` get every result in one streamed response when the
  registry can stream them, which is much faster for large archives.
- `nbank check archive PATH` uses the registry in the archive's `nbank.json`
  unless given `-r`. A different registry is a warning, so archives can be
  checked against a copy.
- `archive prune` also takes the archive's path, which, like `check archive`,
  uses the registry in its `nbank.json` unless given `-r`.
- When the registry has no archive for a directory, the error says archives
  are registered at their real path, so a host reaching one through a symbolic
  link needs a bind mount (#65).
- `archive import-tar` ends with a summary; `copy` and `export` name the
  offline archives holding a resource they can't read.

### Fixed

- `archive register tape` goes on past a location it can't add, exiting 1.
- `archive prune` reports a registry error that isn't JSON instead of crashing.
- `archive prune` and `check archive` given a path that isn't a directory say
  so, instead of crashing.
- `core.describe_many` asks for 500 resources at a time.
- A `-k` without exactly one `=` is a usage error, and `ParseKeyVal` no longer
  carries values from one parse to the next.
- `nbank registry info` on an address that isn't a registry is an error, not a
  crash.
- The `repr` of an http(s) resource no longer raises.
- Output to a reader that stops early (`| head`) exits quietly (status 141).
- `nbank.admin` exits 1 when a resource can't be handled, and reports registry
  errors without a traceback.
- `util.query_registry` and `query_registry_bulk` use the session's credentials.
- A copy in an archive this user can't read is not found, instead of a
  `PermissionError` before Python 3.14.

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
