Command-line reference
======================

The ``nbank`` command's subcommands and options. The registry is given with
``-r`` or the ``NBANK_REGISTRY`` environment variable, and credentials with
``-a username:password`` or a ``.netrc`` file.

.. argparse::
   :module: nbank.script
   :func: make_parser
   :prog: nbank
