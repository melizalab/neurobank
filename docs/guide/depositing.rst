Depositing resources
====================

The core principle behind **neurobank** is that every resource used in an
experiment or analysis has a unique identifier that is immutably linked to the
contents. Thus, before you start an experiment, you need to register all the
resources you plan to use. For example, let's say you're presenting a set of
acoustic stimuli to an animal while recording neural responses. To register the
stimuli:

.. code:: bash

   nbank [-a user:pass] deposit [options] my-archive-path stimfile-1 stimfile-2 ...

Each stimulus will be given an identifier and moved to the archive. Now run your
experiment using the deposited files, making sure to record the identifiers of
the stimuli. The short identifier suffices in most cases, but make sure you
record the registry URL somewhere, too. If you're running the experiment on a
machine that doesn't have direct access to the archive, use ``nbank fetch``
there to retrieve the resources (see :doc:`accessing`).

After the experiment, deposit the data files into the archive using the same
command.

Options
-------

-  ``-d, --dtype``: the datatype of the deposited resources (see `Datatypes`_).
   Your registry may require this.
-  ``--experimenter``: the person who collected or produced the data. This must
   be a user in the registry.
-  ``--animal``: the full identifier of the animal the data came from. This must
   be in the registry's animal database (in our lab, the colony database's UUID
   for the bird).
-  ``-k key=value``: a metadata key and value (see `Metadata`_). Use the flag
   more than once to set several keys.
-  ``-H, --hash``: calculate a SHA1 hash of each file and store it in the
   registry, even if the archive's policy doesn't require it. Use this if you
   expect the contents of the file to be unique.
-  ``-A, --auto-id``: give each file a random identifier instead of its name,
   overriding the ``auto_identifiers`` policy if it is set
   to false (see :doc:`archives`).
-  ``-y, --dry-run``: check everything without registering or moving anything
   (see `Dry runs`_).
-  ``--zip-directories``: deposit directories as zip files (see
   `Directories`_).
-  ``--no-file-metadata`` and ``--override-file-metadata``: control the dtype's
   metadata reader (see `Metadata readers`_).
-  ``-j, --json-out``: output each deposited file as line-delimited JSON, with
   the identifier it was given. This is especially useful with automatic
   identifiers, to make a catalog of the stimuli you'll use in an experiment.
-  ``-@``: read more file names from standard input.

Identifiers
-----------

Unless the archive's policy or ``-A`` says otherwise, a file's identifier is its
name, without the extension: ``st32_1_2_1.wav`` is deposited as ``st32_1_2_1``.
Identifiers can't be changed once they're assigned, and the registry refuses a
name that's already taken (or, if hashes are stored, a file that's already
registered under another name).

Automatic identifiers are useful for blinding a study or to avoid collisions with
similarly-named stimuli. They're eight random base-36 characters, or UUIDs if
the archive's ``auto_id_type`` is ``"uuid"``.

Datatypes
---------

Depending on your registry implementation, you may be required to specify a
datatype for each deposited resource. This feature allows a single registry to
store information about different kinds of resources. Each datatype has a name
and a MIME content-type. Content-types can be from the `official list
<https://www.iana.org/assignments/media-types/media-types.xhtml>`__, or they can
be user-defined, like the content-type for `pprox
<https://meliza.org/spec:2/pprox/>`__. You can get a list of the known datatypes,
with the metadata reader installed for each one, with:

.. code:: bash

   nbank [-r registry-url] dtype list

You may be able to add datatypes to the registry with:

.. code:: bash

   nbank [-a user:pass] [-r registry-url] dtype add dtype-name content-type

Metadata
--------

Each resource has two fields that refer to other database records, and free-form
metadata:

-  ``experimenter`` (``--experimenter``): the username of the person who
   collected or produced the data.
-  ``animal`` (``--animal``): the animal the data came from.
-  metadata (``-k key=value``): anything else, as keys and values.

``-k`` refuses the names of the fields; use their own options. A value is read
as JSON if it parses, and otherwise kept as a string, so ``-k rate=44100``
stores a number and ``-k condition=awake`` stores a string. Lists and objects
work too, but need shell quoting: ``-k 'tags=["a", "b"]'``. To store a string
that looks like a number, quote it as JSON: ``-k 'name="20140603"'``. ``True``,
``False`` and ``None`` are accepted as aliases for ``true``, ``false`` and
``null``.

The metadata given on the command line applies to every file in the deposit, so
you may need to split up deposits of files that differ.

Metadata readers
----------------

Some datatypes have a metadata reader: a function, provided by the package that
produces the files, that checks a file and reads its metadata (for example, the
recording an extracellular ARF file comes from, or the animal and experimenter
recorded in a pprox file). When a dtype's reader is installed in the same Python
environment as neurobank, ``deposit`` runs it on each file.

-  A file the reader rejects isn't deposited.
-  The metadata it reads is merged with what's given on the command line. A key
   with a different value in the two is an error for that file, unless
   ``--override-file-metadata`` is given, in which case the command line's value
   is used.
-  ``--no-file-metadata`` skips the reader.
-  Deposit shows the metadata it registers for each file (dry runs, what they
   would register); with ``--debug``, it also shows which reader was used and
   what it read from the file.

``nbank dtype list`` shows which package provides each installed reader, and
``nbank verify --metadata`` reads deposited files again and compares what the
reader finds with the registry (see :doc:`accessing`).

The reader interface
~~~~~~~~~~~~~~~~~~~~

A reader is a function that takes the path of a file and returns a ``dict`` of
metadata. It's registered as an entry point in the group
``nbank.metadata_readers``, named for the dtype it reads, in the package that
produces the files:

.. code:: toml

   [project.entry-points."nbank.metadata_readers"]
   spikes-pprox = "dlab.readers:read_pprox_metadata"

.. code:: python

   def read_pprox_metadata(path: Path) -> dict:
       """Returns the registry metadata of a pprox file."""
       ...

The reader:

-  must raise ``ValueError`` if the file isn't a valid file of its dtype, with a
   message saying why. Any other exception is reported as the reader failing;
-  must return a ``dict`` with string keys and values that can be stored as JSON
   (numbers, strings, booleans, lists and dicts; not, say, numpy arrays). Keys
   with ``None`` values are left out;
-  can return ``experimenter`` and ``animal``, which are stored as the
   resource's fields;
-  doesn't need to import neurobank.

Only one installed package can provide a reader for a dtype.

Directories
-----------

Resources are single files. Directories can't be deposited as they are; bundle
related files into a zip file first, or use ``--zip-directories``, which writes
each directory to a zip file in a reproducible form (see
:mod:`nbank.canonical_zip`), in the archive's ``staging`` directory, and
deposits it under the directory's name. The directory is left in place. Zip
files are stored without compression, so their members can be read without
unpacking them (see :doc:`accessing`). If you deposit other kinds of containers,
you're responsible for organizing the contents and assigning any internal
identifiers. For frequently used data types that are generated in a directory tree, it's a good idea to design a specification for the zip organization (e.g., `abfzip <https://meliza.org/spec:4/abfzip/>`__) and write a script for packaging the raw data into the zip (e.g., ``abf-zip`` in `melizalab-tools <https://github.com/melizalab/melizalab-tools>`__).

Dry runs
--------

``--dry-run`` checks everything without registering or moving anything: the
files, their metadata, and whether the registry would accept each resource (its
name and hash not taken, nor shared with another of the files, and its dtype,
experimenter, animal and metadata valid). The registry checks need permission to
deposit. Another deposit can still take a name before the real one. Dry runs
don't build the zip files for ``--zip-directories``.

Updating metadata
-----------------

Resources can't be changed once they're deposited, but their metadata, fields,
and datatype can:

.. code:: bash

   nbank modify [-k key=value] [-K key] [--experimenter USER] [--animal ID] [--dtype DTYPE] id-1 [id-2 ...]

Multiple ``-k`` flags can be used, and ``-K key`` deletes a key. An empty
``--experimenter`` or ``--animal`` clears the field, and ``--dtype`` changes the
datatype (for a resource deposited with the wrong one). You can't change the
hash or the other fields of the resource. ``modify`` prints each updated record.

A resource that shouldn't be used, but can't be deleted because others have
cited it, can be marked as superseded (see :doc:`managing`).

In Python
---------

:func:`nbank.core.deposit` does what the ``deposit`` command does, yielding a
record for each file, and :func:`nbank.core.update` changes metadata, fields,
and datatypes:

.. code:: python

   from pathlib import Path
   from nbank import core

   for item in core.deposit(
       Path("/home/data/chorus"),
       [Path("st32_1_2_1.wav")],
       dtype="vocalization-wav",
       metadata={"experimenter": "dmeliza", "condition": "awake"},
   ):
       print(item["source"], "->", item["id"])

   list(core.update(registry_url, "st32_1_2_1", metadata={"condition": None}))
