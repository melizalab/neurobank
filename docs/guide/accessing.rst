Accessing resources
===================

Whenever possible, use the files in the archive directly rather than making
copies. Copies take up space and create uncertainty down the road ("Is this file
subtly different in some way from the other copy? If it is, which one is
right?"). In an analysis, it's good to have a master list of the identifiers of
the units (cells, animals, etc.) you want to analyze, or to make one with
``nbank search``, and then build your scripts to look up where the files are.

In the archive
--------------

The ``deposit`` command moves resource files to the archive under the
``resources`` directory, so you can always manually locate your files based on
the identifier. Resources are sorted into subdirectories using the first two
characters of the identifier to avoid having too many files in one directory.
For example, if the identifier is ``edd0ccae-c34c-48cb-b515-a5e6f9ed91bc``,
you'll find the file under ``resources/ed``.

Locating resources
------------------

``nbank locate`` looks up the locations of the resources associated with each
identifier. This should be your primary way of accessing resources. Don't make
unnecessary copies!

.. code:: bash

   nbank locate [options] id-1 [id-2 ...]

You can supply full URL-based identifiers, or short ids. If short ids are used,
the default registry (specified with the ``-r`` argument or the
``NBANK_REGISTRY`` environment variable) is used to resolve the full URL. Use
the ``-L DIR`` flag to create symbolic links in ``DIR``, or the ``-0`` flag to
pipe the paths to another program:

.. code:: bash

   nbank search -d spikes-pprox --animal ca7cb913 | xargs nbank locate -0 | xargs -0 ls -l

Archives mounted elsewhere
--------------------------

An archive is registered with the path it has on the host that manages it, such
as ``/home/data/starlings``. On a host that mounts it somewhere else, such as a
share at ``/mnt/data/starlings`` (or ``Z:\starlings`` on Windows), give
``locate``, ``fetch``, ``export`` and ``copy`` the directory that holds the
archives with ``--alt-base /mnt/data``, or set ``NBANK_ALT_BASE`` to it. The
archive's registered parent directory is replaced, so the archive keeps its
name. In Python, ``find_resources`` takes the same directory as ``alt_base``.

.. _accessing-windows:

On Windows
----------

Archives rely on ownership, groups and permission bits, so they require a POSIX host (Linux or macOS),
for creation and deposit, and the Windows client is limited to searching and updating the registry, and
finding, fetching, exporting and verifying resources. The ``archive init``, ``deposit``, ``copy``, ``archive import-tar``,
``prune`` and ``archive register tape``, and ``check archive`` and ``check all`` will all refuse
to run on Windows.

To read an archive on a share, map the share's directory of archives and give
it as the alternate base. For an archive registered as
``/home/data/starlings``, with ``/home/data`` mapped to ``Z:``:

.. code:: doscon

   > setx NBANK_ALT_BASE Z:\
   > nbank locate st32_1_2_1
   st32_1_2_1      Z:\starlings\resources\st\st32_1_2_1.wav

``setx`` sets the variable for new terminals; use ``--alt-base Z:\`` for a
single command. A few other differences:

- Credentials can be in ``%USERPROFILE%\_netrc``, the name Windows tools use,
  if there's no ``.netrc`` there.
- ``locate -L`` makes symbolic links, which Windows allows only in Developer
  Mode or with administrator rights. Without either, use the paths ``locate``
  prints, or ``fetch`` copies.
- Downloads are cached in ``%LOCALAPPDATA%\neurobank``.
- Output is written as UTF-8, including when it's redirected to a file.

Fetching copies
---------------

On a machine that doesn't have direct access to the archive, ``nbank fetch``
copies resources from an archive this host can read, or downloads them from the
registry, for the resources it serves:

.. code:: bash

   nbank fetch [-d DEST] [-e EXT] [-f] [--verify] [-@] id-1 [id-2 ...]

Files go to the current directory, or ``-d DEST``. If the registry has a default extension associated with the datatype, this will be added to the filename, or you can override the default with ``-e``. ``-f`` overwrites existing files, ``--verify`` checks each file
against its registered hash (trying the resource's other locations if it doesn't
match), and ``-@`` reads identifiers from standard input.

Verifying copies
----------------

``nbank verify`` computes a hash for each file and searches the registry for a
match:

.. code:: bash

   nbank verify [--metadata] file-1 [file-2 ...]

Running this before starting an experiment is a good idea if you're using copies
(for example, on a data-collection machine), as you'll be able to tell if any of
your stimulus files have changed. It's also useful if the same identifier is used
in more than one domain or if you have a data file that was inadvertently
renamed. A file named for a registered identifier is compared with that
resource's hash; otherwise, the registry is searched for the file's hash. With
``--metadata``, it also reads each file with its dtype's metadata reader (see
:doc:`depositing`) and checks the values against the registry record
(``--debug`` shows what was read).

Zip resources
-------------

Resources made of several files are stored as zip files, with their members
stored without compression, so they can be read without unpacking them.
In Python, use :mod:`nbank.zipped` to list a zip's members, open one as a file, get the offset
and size of its data for memory-mapping (e.g. with ``numpy.memmap``), or extract
one to a directory for libraries that only read files by path:

.. code:: python

   from nbank import zipped

   zipped.members(path)                        # ['metadata.json', 'cell1_0000.abf', ...]
   with zipped.open_member(path, "metadata.json") as fp:
       ...
   offset, size = zipped.member_extent(path, "cell1_0000.abf")
   zipped.extract_member(path, "cell1_0000.abf", tmp_dir)

In Python
---------

:func:`nbank.core.find_resources` gives the path of a file for each resource
that can be read on this host, looking in this order:

1. ``local_dir``, if given: a directory of files named by identifier, such as a
   dataset downloaded from a public repository. The registry isn't asked.
2. The resource's locations, in the order the registry gives them: a copy in an
   archive on this host, or, for one the registry serves, a copy in the local
   cache, and then a download into it.

.. code:: python

   from nbank import core

   for id, path in core.find_resources(registry_url, "st32_1_2_1", "st32_1_2_2"):
       if isinstance(path, FileNotFoundError):
           print(id, "is not available:", path)
       else:
           print(id, path)

   path = core.find_resource(registry_url, "st32_1_2_1")   # raises FileNotFoundError

Results come as they're ready, as downloads run in parallel. The cache is
``neurobank`` in the user's cache directory (``~/.cache`` on Linux,
``~/Library/Caches`` on macOS, ``%LOCALAPPDATA%`` on Windows), or
``NBANK_CACHE`` if it's set. Downloads are
stored under their final names only once they're complete. ``download=False``
leaves out downloads, ``verify=True`` checks each file against its registered
hash, and ``alt_base`` replaces the directory that contains each archive, for
copies of archives mounted somewhere else.
