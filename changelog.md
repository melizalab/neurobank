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
