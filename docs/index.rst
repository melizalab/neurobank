neurobank
=========

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

Installation
------------

For the **neurobank** CLI, it's recommended to install using `pipx <https://pipx.pypa.io/stable/>`__ or `uv tool <https://docs.astral.sh/uv/concepts/tools/>`__. To use the Python interface, pip install ``neurobank>=1.0,<2`` or add it to your ``pyproject.toml``.

Documentation
-------------

.. toctree::
   :caption: Guides
   :maxdepth: 2

   guide/archives
   guide/depositing
   guide/searching
   guide/accessing
   guide/managing
   practices

.. toctree::
   :caption: Reference
   :maxdepth: 2

   api/index

.. toctree::
   :caption: Project
   :maxdepth: 1

   development
   changelog
