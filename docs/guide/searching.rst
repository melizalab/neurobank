Searching the registry
======================

A registry isn't much good if you can't find what you need. It's prudent to keep lists of all the identifiers you use or generate in a project, but data entry errors happen, and ``nbank search`` is there to help you out. It prints out identifiers, one per line, so the results can be piped to other commands.

.. code:: bash

   nbank search [options] [name]

The default is to search by identifier (any part of one), but you can also
search by:

-  ``-d, --dtype``: datatype.
-  ``-H, --hash``: hash, full or any part of one. A full hash is an exact match,
   which is much faster.
-  ``-n, --archive``: the archive a resource is in.
-  ``-s, --scheme``: the kind of location a resource has (e.g., ``neurobank``,
   ``tape``, ``https``).
-  ``--experimenter``: the experimenter's username.
-  ``--animal``: the animal, by the start of its identifier, such as the first
   part of a UUID (``--animal ca7cb913``).
-  ``-k key=value``: a metadata value (exact match), and ``-K key=value`` to
   exclude one. Values are read as JSON if they parse, as for ``deposit`` (see
   :doc:`depositing`).

Filters can be combined, and each one narrows the results. Use the ``-j`` flag
to output the full registry records as JSON instead of the identifiers, which is
useful if you want to extract the records for further processing.

.. code:: bash

   nbank search -d spikes-pprox --animal ca7cb913 -k condition=awake

Superseded resources
--------------------

Mistakes happen, so resources can be marked as superseded (i.e., not to be used,
usually because it has a replacement (see :doc:`managing`). Searches leave
superseded resources out; ``--superseded`` finds only them, and ``--all`` finds
both.

``nbank check superseded`` reports which of a list of resources are superseded,
with their replacements and why, or aren't in the registry, and exits with an
error if any are. Use ``-@`` to read identifiers from standard input, e.g. to
check the inputs of an analysis before running it:

.. code:: bash

   nbank check superseded -@ < inputs.txt

Resource records
----------------

``nbank info id-1 [id-2 ...]`` prints the registry record for each resource as
JSON: its hash, datatype, file name, fields, metadata, locations, who created
it and when, and whether it's superseded.

In Python
---------

:func:`nbank.core.search` takes the registry's search parameters as keyword
arguments and yields records; :func:`nbank.core.describe` and
:func:`nbank.core.describe_many` return the records of resources by identifier:

.. code:: python

   from nbank import core

   for record in core.search(registry_url, dtype="spikes-pprox", experimenter="dmeliza"):
       print(record["name"], record["metadata"])

   record = core.describe(registry_url, "st32_1_2_1")   # None if not registered

Metadata searches use ``metadata__<key>=value`` as the parameter (and
``metadata__<key>__neq`` to exclude). Like the command, ``core.search`` leaves
superseded resources out unless given ``superseded=True`` (only those) or
``superseded=None`` (both).
