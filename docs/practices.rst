How the Meliza lab uses neurobank
=================================

To avoid unnecessary duplication of data and stimulus resources, the `Meliza
Lab <https://meliza.org>`__ uses shared archives. Each stimulus and data unit is
given a unique identifier, which you should use in data acquisition and analysis.
When you add a file to an archive, it's given an identifier and moved to the
archive. At the same time, the identifier and additional data about the resource
are stored in a central `registry <https://gracula.psyc.virginia.edu/neurobank/>`__.
Then, when you run an analysis, you should only ever use the version of the
resource stored in the archive.

These guides assume that your files are on one of the lab's shared Linux
machines. If you're unsure how to proceed, discuss your data management
strategy with Dan first.

Depositing stimuli and data
---------------------------

After finishing your stimulus design, but before using a stimulus file in an
experiment, deposit it in the archive. Deposit collected data only after an
initial quality check; avoid depositing things that no one will ever have a use
for.

1. Choose the archive where the files will be stored. Archives either store a
   single kind of data for general use (e.g., microscopy images or song
   recordings) or are associated with a specific project. New archives can be
   created in consultation with Dan.
2. Run ``nbank dtype list`` to get a list of accepted data types and make a note
   of which one matches your data files. For example, ``vocalization-wav`` would
   be the correct choice for auditory stimuli stored in wave files. New data
   types can be added in consultation with Dan.
3. Decide if your identifiers are going to be based on the file names or if you
   want neurobank to automatically generate new ones for you. Collected data
   resources have sometimes been given automatic IDs in the past, but we now
   prefer to use names based on dates, animals, etc.
4. Decide what metadata need to be associated with the resources. You can change
   metadata later, but it's generally better to plan ahead. Give
   ``--experimenter`` your computing id and ``--animal`` the bird's UUID in the
   colony database. For other metadata, try to use values that can be easily
   looked up in our databases. Some datatypes have metadata readers that will
   extract these values from the files you're depositing, which limits the scope
   for data entry errors.

The base command to do a deposit is ``nbank deposit -d <data-type>
<archive-directory> <files>``, but you will need to add options depending on
your choices above (see :doc:`guide/depositing`). For example:

.. code:: bash

   nbank deposit -A -d vocalization-wav /home/data/my-project stim1.wav stim2.wav
   nbank deposit -d spikes-pprox --experimenter dmeliza --animal ca7cb913-... -k condition=awake /home/data/my-project data1.json

Add ``-j`` to output the newly deposited files as line-delimited JSON. This is
especially useful with automatic IDs to create a catalog of the stimuli you'll
use in an experiment.

Using deposited data
--------------------

Strive to directly use files in the archive rather than making copies. The
exception to this is if you need to copy data to a different computer to run an
experiment or do an analysis. In your analysis workspace, it's good to have a
master list of identifiers that correspond to the specific units (cells,
animals, etc.) you want to analyze, or to use ``nbank search`` to generate this
list dynamically based on specific search criteria. Then use ``nbank locate`` to
retrieve the path of the files (see :doc:`guide/accessing`).
