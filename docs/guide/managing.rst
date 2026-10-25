Managing archives
=================

Checking archives
-----------------

You can check whether an archive contains all the files it's supposed to by
running ``nbank check archive <path_to_archive>``. This command compares each
resource in the archive to its record in the registry and gives a summary of any
resources missing from the archive and files that don't have matches in the
registry (which might indicate corrupted data). It also checks that the
registry's location for each resource has the name of its file as its key, which
is how nbank finds the file, and checks ownership and permissions against the
archive's policy (see :doc:`archives`). It reports how many files are named
differently from the registry's ``filename`` (the name given to copies), which
is informational, not an error.

-  ``--fix`` sets location keys to the names of the files, repairs ownership and
   permissions (changing ownership requires root), creates a missing
   ``staging`` directory, and in an interactive terminal, offers to delete
   leftovers from interrupted transfers and deposits, and to resolve files that
   are registered to other archives.
-  ``--no-hash`` doesn't verify file hashes, which is much faster for large
   archives.
-  ``-v`` shows the results for all resources, not just errors.

``nbank check registry`` reports resources without locations and archives with
no resources, and ``nbank check all`` checks the registry and then every
neurobank archive on this host. It's a good idea to set up a cron job to check
archive integrity every month or so with ``nbank check all``. The checks exit
with an error if they find problems.

Copying and moving resources
----------------------------

To copy resources into another archive, run ``nbank copy <path_of_archive> id-1
[id-2 ...]`` on a host that can read both archives. Each resource is checked
against its registered hash as it's stored, and the new copy is added to the
resource's locations in the registry. Resources the registry already lists in
the destination are skipped, so the same list can be run again after a failure.
Use ``-f`` to read identifiers from a file, ``-a`` to read only from one
archive, and ``-y`` to check the resources without copying them. To move
resources, copy them and then remove them from the old archive with ``nbank
archive prune``.

``nbank archive prune <archive_name> <list_of_identifiers>`` deletes files from
a local archive and updates the records for the resources. It will only do this
for resources that have another location registered.

Exporting resources
-------------------

``nbank export [options] out id-1 [id-2 ...]`` copies resources from the
archives on this host to a tar file (``out.tar``), a zip file (``out.zip``), or a
directory (anything else), for example to share them or put them in cold
storage. Don't use this for local copies (use ``nbank locate -L`` to create
symbolic links instead). Each resource is checked against its registered hash
as it's copied, and any that don't match or can't be read are reported and left
out. Use ``-f`` to read identifiers from a file, ``-a`` to read only from one
archive, and ``--compress`` to compress the members of a zip file (they're
stored as-is by default, since most data files don't compress well). Add
``--manifest`` to include the registry records of the exported resources
(identifier, path, hash, datatype, metadata, and when and by whom they were
created) as ``manifest.json``.

Cold storage
------------

Some resources, like raw extracellular data, can be moved to cold storage when
they are no longer needed. The Meliza lab uses tape for this because of its long
shelf life, low cost, and low environmental impact (no need for power). Moving
resources to cold storage is a multi-step process:

1. Identify the resources to archive, using lists of identifiers from project
   directories or ``nbank search``.
2. Copy the resources to a tar file with ``nbank export -f
   <list_of_identifiers> <name_of_tar_file>``. Each resource is checked against
   its registered hash as it's read, and any that don't match or can't be read
   are reported and left out, so the tar file only holds verified copies. Add
   ``-a <archive_name>`` to read only from that archive.
3. Write the tar file to tape (or some other media), for example with ``dd
   if=<name_of_tar_file> of=/dev/nst0 bs=256k``. Keep two copies of important
   data, one offsite.
4. Optional: check what was written with ``nbank check tar
   <tape_device_or_tar_file>``, which reads every registered resource back and
   compares it to its registered hash. This command works on tar files,
   straight from the tape drive (e.g. ``/dev/nst0`` after positioning the tape
   with ``mt fsf``), or from standard input (``-``).
5. Register what's in the tape archive with neurobank by running ``nbank archive
   register tape -n <name_of_archive> <name_of_tape> <tape_index> <tar_file>``.
   This will create a record for the tape archive and update the records for the
   resources in the tar file. To avoid having to re-read the entire tape, this
   command works on the temporary tar file from step 2, which you can delete
   after this step.
6. To remove the tape-archived resources from live storage, run ``nbank archive
   prune <live_archive_name> <list_of_identifiers>``.

To copy data back to live storage, run ``nbank archive import-tar <tar_file>
<path_of_archive>``. The tar file can be read straight from the tape drive (e.g.
``/dev/nst0`` after positioning the tape with ``mt fsf``) or from standard input
(``-``), so it doesn't need to be extracted first. Each resource is checked
against its registered hash before it's stored. To check a tape without
importing anything, add ``-y``. To import only some of the resources, list their
identifiers after the archive path, or put them in a file and pass it with
``-f``. This mode works much faster when working from a seekable file.

Data on tapes should be checked every year or two using ``nbank check tar``.
Add ``--archive <name_of_archive>`` to also confirm that every resource the
registry thinks is on that tape is actually there. If the tape can't be read
past some point (for example, a damaged stretch), the check stops. Check the
kernel log (``dmesg``) to see whether the drive reported a problem with the tape
(``Medium Error``) or with itself (``Hardware Error``).

Administrative tasks
--------------------

``nbank.admin`` is a separate script for occasional repairs, run with ``python
-m nbank.admin``. It takes the same ``-r`` and ``-a`` options as ``nbank``, and
requires the user to have escalated privileges in the registry.

-  ``delete [--record FILE] resources``: deletes the resources listed in the
   file ``resources`` (one identifier per line) from the archives on this host
   and from the registry, for example after a lot of files were deposited by
   mistake. This can't be undone; ``--record`` appends the deleted registry
   records to a file. Deleting needs permission to delete resources (normally
   superusers).
-  ``update-hash resources``: computes the hash of each listed resource from its
   copy on this host and stores it in the registry (superusers only).
-  ``supersede [--by ID] [--note NOTE] id-1 [id-2 ...]``: marks resources as
   superseded, optionally with the resource to use instead and why; ``--clear``
   unmarks them (superusers only). A superseded resource stays in the registry
   and the archives, for work that cites it, but searches leave it out (see
   :doc:`searching`).

Each command takes ``-y`` to show what it would do without doing it, and exits
with an error if any resource can't be handled. For resources deposited by
mistake that no one has used, deleting them and depositing them again is better
than superseding them.
