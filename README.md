# neurobank

**neurobank** is a simple, low-overhead data management system for neural and behavioral data. It helps you generate unique identifiers for stimuli, protocols, and recording units. No more guessing what version of a stimulus you presented in an experiment, where you stored an important recording, and whether you've backed it all up yet.  Your files are stored in a single directory hierarchy, and you get nice, human-readable, JSON-based metadata files to organize your records and analysis workflows.

## Installation

Install the **neurobank** Python package and its dependencies:

```bash
pip install neurobank
```

Initialize an archive. The archive has to live on a locally-accessible filesystem (however, it can be mounted over NFS, SSHFS, etc).

```bash
nbank init my-archive-path
```

You should edit the `README.md` and `package.json` files created in the archive directory to describe your project and set policies. You should also set permissions and default access control lists at this time so that files are added to the repository with the appropriate access restrictions.

## Usage

The data management strategy behind **neurobank** is simple. First, every file you use to control an experiment gets a unique identifier. Files are renamed to match the identifier and archived. You run the experiment using the renamed files, so that the identifiers are stored with the data. When the experiment is done, you archive the raw data files and they're assigned their own identifiers. Now every file generated in the experiment is uniquely identified, and points unambiguously to the data sources used in the experiment. At every stage you get a metadata JSON file that stores the mapping from the original names to the identifiers.

For example, let's say you're presenting a set of acoustic stimuli to an animal while recording neural responses. To register the stimuli:

```bash
nbank register stimset.json stimfile-1 stimfile-2 ...
```

Each stimulus will be renamed to match its identifier (keeping the same extension), and you'll have a file called `stimset.json` with the mappings from the identifiers to the new names. You can find your stimulus files in the archive in the `sources/` directory under subdirectories with the first two characters of the identifier. For example, if the identifier is `14955422a0fbe3a2bb45111dc91e46e6.wav`, you'll find the file under `sources/14`.

Use the renamed stimulus files to run your experiment. If your data collection program doesn't store the names of the stimuli in the generated data files, you'll need to record the names manually. After the experiment, import the data into the archive:

```bash
nbank deposit dataset.json datafile-1 datafile-2 ...
```

As with the `register` command, `deposit` will assign a unique identifier to each file. Files may be containers (e.g., ARF files, https://github.com/melizalab/arf), in which case you are responsible for assigning identifiers within the files. The script will move the data to the archive and generate a JSON file (`dataset.json`) with the mappings from the original names to the identifiers.

Imported data files can be found in the archive in the `data/` directory in the subdirectory with the first two characters of the identifier.

You can control how identifiers are generated (for example, to include the original filename or a shared suffix) and other policies for a data archive by editing the `project.json` file.

## API Reference

The **neurobank** python module supplies some functions for locating data and stimuli in the archive (maybe?)

### function([args])

docs

## Best practices

One of the primary uses for neurobank is to allow multiple users to share a common set of data, thereby reducing the need for temporary copies and ensuring that a canonical, centralized backup of critical data can be maintained. In this case, the following practices are suggested (on POSIX operating systems):

1. For each project, create a separate group and make the archived owned by the group. To give a user access to the data, add them to the group.
2. To restrict access to users not in the project group, set the umask to 027 before creating the archive.
3. Set the setgid (or setuid) bit on the subdirectories of the archive, so that files added to the archive become owned by the group. (`chmod 3770 sources data metadata`). You may also consider setting the sticky bit so that files and directories can't be accidentally deleted.
4. If your filesystem supports it, set the default ACL on subdirectories so that added files are accessible only to the group. (`setfacl -d -m u::rwx,g::rwx,o::- sources data metadata`).


## License

**neurobank** is licensed under the GNU Public License, version 2. That means you are free to use the code for anything you want, including a commerical work, but you have to provide the source code, including any modifications you make. You still own your data files and any associated metadata. See COPYING for more details.
