neurobank
=========

|ProjectStatus|_ |Version|_ |BuildStatus|_ |Docs|_ |License|_ |PythonVersions|_

.. |ProjectStatus| image:: https://www.repostatus.org/badges/latest/active.svg
.. _ProjectStatus: https://www.repostatus.org/#active

.. |Version| image:: https://img.shields.io/pypi/v/neurobank.svg
.. _Version: https://pypi.python.org/pypi/neurobank/

.. |BuildStatus| image:: https://github.com/melizalab/neurobank/actions/workflows/python_tests.yml/badge.svg
.. _BuildStatus: https://github.com/melizalab/neurobank/actions/workflows/python_tests.yml

.. |Docs| image:: https://readthedocs.org/projects/neurobank/badge/?version=stable
.. _Docs: https://neurobank.readthedocs.io/

.. |License| image:: https://img.shields.io/pypi/l/neurobank.svg
.. _License: https://opensource.org/license/gpl-2-0

.. |PythonVersions| image:: https://img.shields.io/pypi/pyversions/neurobank.svg
.. _PythonVersions: https://pypi.python.org/pypi/neurobank/

**neurobank** is the Meliza lab's data management system. It’s designed for
neural and behavioral data, but could be used for other kinds of
experiments. The software helps generate unique identifiers for
data resources, including stimuli, protocols, and recording units. No
more guessing what version of a stimulus you presented in an experiment,
where you stored an important recording, and whether you’ve backed it up
or archived it.

You can think of **neurobank** like git for your data. Every resource gets a
unique identifier and doesn't get changed once the identifier is assigned. This
provides you with the confidence that some stimulus "xyzzy" that you used in an
experiment in 2005 is the same as the one you used last week.

What are resources? Resources include *sources*, which are used to control an
experiment, and *data*, which result from running the experiment. Resources do
not include things that evolve, like code, figures, or manuscripts. These should
be managed in a standard version control system.

There are two components to the **neurobank** system. One part is the
**registry**, a service that stores identifiers, ensures they’re unique, and
resolves identifiers to the location where the resource is stored.
`django-neurobank <https://github.com/melizalab/django-neurobank>`__ is an
implementation of this service that uses a postgres backend and the django REST
framework. This package provides a simple python and commandline interface for
interacting with the registry.

The second component is an **archive**, a storage and retrieval mechanism, which
is also provided by this package. We keep our archives on a filesystem shared
over NFS to our local compute cluster for fast, filesystem-based access. These
archives have a simple directory structure with a little nesting so that you
don't have single directories filling up with hundreds of thousands of files.
This package also provides some mechanisms for moving older data to and from
cold storage on tape.

The full documentation, with guides to each task and references for the command
line and the Python interface, is at https://neurobank.readthedocs.io/.

Installation
------------

For the **neurobank** CLI, it's recommended to install using `pipx <https://pipx.pypa.io/stable/>`__ or `uv tool <https://docs.astral.sh/uv/concepts/tools/>`__. To use the Python interface, pip install ``neurobank>=1.0,<2`` or add it to your ``pyproject.toml``.

neurobank runs on Linux, macOS and Windows, but archive management requires a
POSIX filesystem, so the Windows client only supports read operations.

A quick tour
------------

Most commands need a registry, given with ``-r`` or the ``NBANK_REGISTRY``
environment variable, and the registry may need credentials, given with ``-a
username:password`` or in your ``.netrc`` file. Check that you can reach it and
who you're authenticating as with

.. code:: bash

   export NBANK_REGISTRY=https://myregistry.org/neurobank/
   nbank registry info

Create an archive, which registers it and sets up its directory (see `Setting up
archives <https://neurobank.readthedocs.io/en/stable/guide/archives.html>`__):

.. code:: bash

   nbank archive init /home/data/my-project

Deposit files. Each one is registered under its filename unless you ask for an
automatic identifier (``-A``), and moved into the archive. Use ``-d`` to specify
the datatype (use ``nbank dtype list`` to query the available options on your
registry), ``--experimenter`` to specify who generated the resource, and
``--animal`` to specify the experimental subject. Use ``-k`` to add additional
free-form, key-value metadata (see `Depositing resources
<https://neurobank.readthedocs.io/en/stable/guide/depositing.html>`__):

.. code:: bash

   nbank deposit -d vocalization-wav --experimenter dmeliza -k condition=awake /home/data/my-project st32_1_2_1.wav

Find resources, look at their records, and change their metadata (see
`Searching the registry
<https://neurobank.readthedocs.io/en/stable/guide/searching.html>`__):

.. code:: bash

   nbank search -d vocalization-wav --experimenter dmeliza
   nbank info st32_1_2_1
   nbank modify -k condition=anesthetized st32_1_2_1

Get the paths of the files, link them into a working directory, or copy them to
a machine that can't read the archive (see `Accessing resources
<https://neurobank.readthedocs.io/en/stable/guide/accessing.html>`__):

.. code:: bash

   nbank locate st32_1_2_1
   nbank locate -L analysis/ st32_1_2_1
   nbank fetch -d stimuli/ st32_1_2_1

Check archives against the registry, and copy resources between archives or to
tape (see `Managing archives
<https://neurobank.readthedocs.io/en/stable/guide/managing.html>`__):

.. code:: bash

   nbank check archive /home/data/my-project

In Python, `nbank.core <https://neurobank.readthedocs.io/en/stable/api/core.html>`__ has the same operations: ``deposit``, ``search``,
``describe``, ``update``, and ``find_resources``, which gives the path of a file
for each resource, from an archive on this host or a cached download.

License
-------

**neurobank** is licensed under the GNU General Public License, version 2. You
still own your data files and any associated metadata. See COPYING for more
details.
