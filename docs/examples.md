
This file gives an example of how neurobank is used the [Meliza Lab](https://meliza.org)

To avoid unnecessary duplication of data and stimulus resources, the lab uses a shared archive. Each stimulus and data unit is given a unique identifier, which you should use in data acquisition and analysis. The software that manages the archive and assigns identifiers is [neurobank](https://github.com/melizalab/neurobank).

When you add a file to the archive, it's given an identifier and moved to the archive. At the same time, the identifier and additional data about the resource are stored in a central [registry](https://gracula.psyc.virginia.edu/neurobank/).

Then, when you run an analysis, you should only ever use the version of the resource stored in the archive.

Some quick guides on common tasks are below, but be sure to check the [neurobank](https://github.com/melizalab/neurobank) github page for the most recent manual. These guides assume that your files are on one of our shared Linux machines.

If you're unsure how to proceed, discuss your data management strategy with Dan first.

## Depositing stimuli and data

After finishing your stimulus design, but before using a stimulus file in an experiment, deposit it in the archive. Deposit collected data after an initial quality check; avoid depositing things that no one will ever have a use for.

First, choose the archive where the files will be stored. Archives either store a single kind of data for general use (e.g., microscopy images or song recordings) or are associated with a specific project. New archives can be created in consultation with Dan.

Run `nbank dtype list` to get a list of accepted data types and make a note of which one matches your data files. For example, `vocalization-wav` would be the correct choice for auditory stimuli stored in wave files. New data types can be added in consultation with Dan.

Decide if your identifiers are going to be based on the file names or if you want neurobank to automatically generate new ones for you. Automatic IDs are useful for blinding a study or to avoid collisions with similarly-named stimuli. Collected data resources have sometimes been given automatic IDs in the past, but we now prefer to use names based on dates, animals, etc.

Decide what metadata need to be associated with the resources. You can change metadata later, but it's generally better to plan ahead. Metadata is stored as key/value pairs. For example, it's a good idea to set `experimenter` to your computing id, and to have a `bird` key with the animal's identifier as the value. Try to use values that can be easily looked up in our database (e.g., the uuid for a bird)

The base command to do a deposit is `nbank deposit -d <data-type> <archive-directory> <stimulus-files>`, but you will need to add options depending on your choices above.

To use automatic IDs, add `-A` to the command (e.g. `nbank deposit -A -d vocalization-wav /home/data/my-project stim1.wav stim2.wav`).

To add metadata, add `-k key=value` to the command. For example nbank `deposit -d spikes-pprox -k experimenter=dmeliza /home/data/my-project data1.json`. You can add as many of these as needed. Note that the metadata will apply to all the deposited files, so you may need to split up your deposit commands.

You can also add `-j` to the command to have it output the newly deposited files as line-delimited JSON. This is especially useful with automatic IDs to create a catalog of the stimuli you'll use in an experiment

## Using deposited data

The [neurobank](https://github.com/melizalab/neurobank) github page has detailed instructions on how to search the registry and locate your deposited files, so this section is for more general policies and notes.

Strive to directly use files in the archive rather than making copies. The exception to this is if you need to copy data to a different computer to run an experiment or do an analysis. In your analysis workspace, it's good to have a master list of identifiers that correspond to the specific units (cells, animals, etc) you want to analyze, or to use `nbank search` to generate this list dynamically based on specific search criteria. Then use `nbank locate -l` to retrieve the path of the files.
