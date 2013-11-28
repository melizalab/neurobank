# neurobank

**neurobank** is a simple, low-overhead data management system for neural and behavioral data. It helps you generate unique identifiers for stimuli, protocols, and recording units. No more guessing what version of a stimulus you presented in an experiment, where you stored an important recording, and whether you've backed it all up yet.  Your files are stored in a single directory hierarchy, and you get nice, human-readable, JSON-based metadata files to organize your records and analysis workflows.

## Installation

Install the **neurobank** Python package and its dependencies:

```bash
pip install neurobank
```

Initialize the archive. The archive has to live on a locally-accessible filesystem (which may be mounted over NFS, SSHFS).

```bash
nbank init my-archive-path
```

## Usage

The data management strategy behind **neurobank** is simple. First, every file you use to control an experiment gets a unique identifier, renamed to match the identifier, and archived. You run the experiment using the renamed files, so that the identifiers are stored with the data. When the experiment is done, you archive the raw data files and they're assigned their own identifiers. Now every file generated in the experiment is uniquely identified, and points unambiguously to the data used in the experiment. At every stage you get a metadata JSON file that stores the mapping from the original names to the identifiers.

For example, let's say you're presenting a set of acoustic stimuli to an animal while recording neural responses. To register the stimuli:

```bash
nbank register stimset-name stimfile-1 stimfile-2 ...
```

Each stimulus will be renamed to match its identifier (keeping the same extension), and you'll have a file called `stimset-name.json` with the mappings from the identifiers to the new names. You can find your stimuli file in the archive in the `stimuli/` directory under a subdirectory with the first three characters of the identifier. For example, if the identifier is `14955422a0fbe3a2bb45111dc91e46e6`, you'll find the file under `stimuli/149`.

Use the renamed stimulus files to run your experiment. If your data collection program doesn't store the names of the stimuli in the generated data files, you'll need to record the names manually. Next, import the data into the archive:

```bash
nbank deposit dataset-name datafile-1 datafile-2 ...
```

By default, the import script will treat each datafile as a separate recording. Some storage formats may include multiple recordings (for example, from multiple electrodes). **neurobank** understands the structure of ARF (https://github.com/melizalab/arf) files and will assign identifiers to each channel. Support for other multi-recording formats can be added through plugins. The script will move the data to the archive and generate a JSON file (`dataset-name.json`) with the mappings from the original names to the identifiers.

Imported data files can be found in the archive in the `data/` directory in the subdirectory with the first three characters of the identifier.

You can control how identifiers are generated (for example, to include the original filename or a shared suffix) and other aspects of the *nbank* script's behavior with commandline options. See `nbank <command> -h` for more information about each command.

## API Reference

The **neurobank** python module supplies some functions for locating data and stimuli in the archive (maybe?)

### function([args])

docs

## Import plugins

plugin api

## License

**neurobank** is licensed under the GNU Public License, version 2. That means you are free to use the code for anything you want, including a commerical work, but you have to provide the source code, including any modifications you make. You still own your data files and any associated metadata. See COPYING for more details.
