Development
===========

Use `uv <https://docs.astral.sh/uv/>`__ for development. Run ``uv sync`` to
create a virtual environment and install dependencies, or ``uv sync --no-dev
--frozen`` for deployment.

Testing
-------

``uv run pytest`` runs the unit tests against a mock registry. On Windows, the
tests that need a POSIX host skip themselves: those marked ``posix``, and those
that create an archive (the ``tmp_archive`` fixtures). Tests that only read an
archive use the ``archive_layout`` fixture, which writes an archive's files
directly, so they run on Windows too.

Integration tests run against a live registry (django-neurobank) that the tests
start themselves, using a temporary sqlite database. They are skipped by
default, and you need to install the ``integration`` dependency group, which
``uv sync`` doesn't do by default:

.. code:: bash

   uv run --group integration pytest -m integration

The integration tests also run on GitHub against Postgres (see
``.github/workflows/integration_tests.yml``). To use Postgres locally, add
``--group postgres`` and set ``NBANK_TEST_DB=postgres`` and, if the defaults
don't apply, ``POSTGRES_DB``, ``POSTGRES_USER``, ``POSTGRES_PASSWORD``,
``POSTGRES_HOST`` and ``POSTGRES_PORT``. To use an existing registry, set
``NBANK_TEST_REGISTRY`` to its base URL and ``NBANK_TEST_AUTH`` to
``user:password`` for an account that can write.

Documentation
-------------

The documentation is built with Sphinx from ``docs/``, and published on Read
the Docs (configured in ``.readthedocs.yaml``) for tagged releases only. The
test workflow builds it on every push and pull request, failing on warnings as
Read the Docs does. To build it locally:

.. code:: bash

   uv run --group docs sphinx-build -W docs docs/_build/html

The command-line reference is generated from ``nbank``'s argument parser, and
the Python API reference from the docstrings of the names each module lists in
``__all__``.
