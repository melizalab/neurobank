neurobank
=========

**neurobank** is the Meliza lab's data management system for neuroscience data:
stimuli, protocols, recordings, and the data derived from them. Every resource
gets a unique identifier that never changes, so a stimulus called ``xyzzy`` used
in an experiment in 2005 is the same file as the one used last week.

It has two parts. The **registry** is a web service
(`django-neurobank <https://github.com/melizalab/django-neurobank>`__) that
stores identifiers, keeps them unique, and records where each resource is
stored. The **archives** are directories on local or shared filesystems where
the files live. This package is the client: the ``nbank`` command and a Python
library.

.. toctree::
   :caption: Reference
   :maxdepth: 2

   cli
   api/index

.. toctree::
   :caption: Project
   :maxdepth: 1

   changelog
