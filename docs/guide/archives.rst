Setting up archives
===================

An archive is a directory on a local or shared filesystem (which could be an NFS
or SSHFS mount) where neurobank stores resources. Each archive is registered
with one registry, which records which resources it contains. Archives are
created and managed on a POSIX host (Linux or macOS). Windows can only read them
(see :ref:`accessing-windows`).

The registry
------------

Most commands need a registry, given with ``-r registry-url`` or the
``NBANK_REGISTRY`` environment variable. Commands that store resources in an
archive (``deposit``, ``copy`` and ``archive import-tar``) use the registry in
the archive's ``nbank.json`` instead. The registry can be any service that
implements the API defined in
`django-neurobank <https://github.com/melizalab/django-neurobank>`__ (version
1.0 or later, which has API 2.x). The registry URL also determines the domain
for the resource identifiers. For example, if the registry is at
``http://melizalab.org/neurobank/resources/`` and you deposit a resource with
the identifier ``st32_1_2_1``, the full identifier is
``http://melizalab.org/neurobank/resources/st32_1_2_1/``. ``st32_1_2_1`` is
guaranteed to be unique within this domain.

If your registry requires authentication, supply it with ``-a
username:password``, or in your `netrc
<https://www.gnu.org/software/inetutils/manual/html_node/The-_002enetrc-file.html>`__
file (``~/.netrc``, or ``~/_netrc`` on Windows). Credentials are sent with every request, so wrong ones are refused even
for reading. ``nbank registry info`` shows the registry's version, which user
you're connecting as, and what you can change:

.. code:: bash

   nbank -r https://gracula.psyc.virginia.edu/neurobank/ registry info

Creating an archive
-------------------

Initialize an archive with:

.. code:: bash

   nbank [-a username:password] [-r registry-url] archive init [-n name] [-u umask] [-g group] [--shared] my-archive-path

The script contacts the registry and adds the archive. By default, the archive
is named after the basename of the archive path. For example,
``/home/data/intracellular`` would have the name ``intracellular``. You can
override this with the ``-n`` flag; however, the registry may only allow you to
have one archive name for each path to avoid confusion. Then the script creates
and initializes the archive under ``my-archive-path``. You'll get an error if
the target directory already contains an ``nbank.json``, ``README.md``, or
``.gitignore`` file, or if the registry already has an archive with the same
name.

An archive is registered at its real path, with any symbolic links resolved,
and the commands that change or check it (``deposit``, ``copy``, ``archive
import-tar`` and ``check archive``) find it there. A host that mounts the
archive's filesystem somewhere else, such as an NFS share of archives, needs a
bind mount at the registered path rather than a symbolic link to the mount.
``nbank check all`` reports a registered root that goes through a link.

By default, the archive is owned by you and your primary group, and its umask is
002. Use ``-g`` to give the archive a different group, and ``-u`` to set a
different umask (for example, 027 to keep out users who aren't in the group). If
several users will deposit into the archive under their own accounts, add
``--shared`` (see `Shared archives`_).

Each archive has two subdirectories. ``resources`` contains the deposited files,
sorted into subdirectories by the first two characters of their identifiers (see
:doc:`accessing`). ``staging`` contains files while they're prepared for deposit,
such as the zip files made by ``--zip-directories``, and anything left there by an
interrupted deposit is safe to delete.

Archive policies
----------------

Edit the ``README.md`` and ``nbank.json`` files created in the archive directory
to describe your project. The ``nbank.json`` file also holds the archive's
policies. These are the settings you may want to modify:

-  ``auto_identifiers``: If set to false (the default), when files are
   deposited, their names are used as identifiers unless the user asks for an
   automatically generated id. If set to true, every resource is given an
   automatic id.
-  ``auto_id_type``: the form of automatic ids. ``"base36"`` (the default, also
   used if this is ``null`` or not set) gives short, random strings of eight
   base-36 characters, like the ones the registry assigns. ``"uuid"`` gives
   128-bit UUIDv4 identifiers, which are guaranteed to work everywhere but also
   a little painful to manipulate by hand.
- ``require_hash``: If set to true (the default), every resource will have a
   hash value calculated and stored in the registry. The registry will then be
   able to prevent duplicate files from being deposited under multiple
   identifiers, and the client can use the hashes to verify data integrity. Keep
   this on unless you have a good reason not to (like multi-terabyte resources)
   and another mechanism to protect against data corruption.
-  ``access``: Who owns the archive's files and who can read and write them.
   These are usually set by ``nbank archive init``.

   -  ``user``: The account that should own the files, or ``null`` for a shared
      archive (``--shared``).
   -  ``group``: The group that should own the files (``-g``).
   -  ``umask``: Permissions to withhold from files and directories (``-u``),
      as a number or an octal string such as ``"027"``.
   -  ``read_only_resources``: If true (the default, also used if it's missing),
      deposited resources can't be modified.

``group`` and ``umask`` are required. nbank checks the file when it reads it
and reports a missing or wrongly typed setting by name. Other keys, such as
``project`` and ``owner``, are for people and are ignored.

Shared archives
---------------

It's easy to set up neurobank to allow multiple users to share a common set of
data, thereby reducing the need for temporary copies and ensuring that a
canonical, centralized backup of critical data can be maintained. In this case,
the following practices are suggested for POSIX operating systems:

1. For each project, create a separate user group. To give a user access to the
   data, add them to the group.
2. Create the archive with ``nbank archive init --shared -g GROUP``, where ``GROUP`` is
   the project group. Add ``-u 027`` to keep out users who aren't in the group.
3. Run ``nbank check all --fix`` as root on a regular schedule (for example, as
   a nightly cron job) to find and repair problems (see :doc:`managing`).

Users deposit files under their own accounts, and only root can change who owns
a file, so a shared archive has ``user`` set to ``null`` in
``nbank.json``, and ownership by user isn't checked. Instead, neurobank gives
deposited files the archive's group and the permissions allowed by its umask,
and keeps the resource subdirectories group-writable and setgid so that any
member of the group can deposit.

By default, deposited resources are read-only, so they can't be changed after
they're registered. New resources can still be deposited, and resources can
still be removed with ``nbank archive prune``.

``nbank check archive`` reports files and directories whose group or permissions
don't match ``nbank.json``, and ``--fix`` repairs them. Many fixes require
running this command as root, because only the owner of a file or root can
change its permissions. It also reports an archive that has no ``staging``
directory, which ``--fix`` creates (archives made before it existed need this
before directories can be deposited with ``--zip-directories``), and anything
left in ``staging``, which ``--fix`` offers to delete.

In Python
---------

:func:`nbank.archive.create` makes an archive's directories and ``nbank.json``
and :meth:`nbank.archive.ArchiveConfig.load` reads an archive's configuration
and policies. Use :func:`nbank.registry.add_archive` to register it.
