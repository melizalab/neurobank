#!/usr/bin/env python
# -*- coding: utf-8 -*-
# -*- mode: python -*-
import sys
from setuptools import setup, find_packages

if sys.hexversion < 0x02070000:
    raise RuntimeError("Python 2.7 or higher required")

# --- Distutils setup and metadata --------------------------------------------

from nbank import __version__

cls_txt = """
Development Status :: 4 - Beta
Intended Audience :: Science/Research
License :: OSI Approved :: GNU General Public License (GPL)
Programming Language :: Python
Topic :: Scientific/Engineering
Operating System :: Unix
Operating System :: POSIX :: Linux
Operating System :: MacOS :: MacOS X
Natural Language :: English
"""

short_desc = "Simple data management system for neuroscience"

long_desc = """ A simple, low-overhead data management system for neural and behavioral
data. It helps you generate unique identifiers for stimuli, protocols, and
recording units. No more guessing what version of a stimulus you presented in an
experiment, where you stored an important recording, and whether you've backed
it all up yet. Your files are stored in a single directory hierarchy, and you
get nice, human-readable, JSON-based metadata to organize your records and
analysis workflows.

"""

requirements = []

setup(
    name='neurobank',
    version=__version__,
    description=short_desc,
    long_description=long_desc,
    classifiers=[x for x in cls_txt.split("\n") if x],
    author='Dan Meliza',
    author_email="dan@meliza.org",
    maintainer='Dan Meliza',
    maintainer_email="dan@meliza.org",
    url="https://github.com/melizalab/neurobank",

    packages=find_packages(exclude=["*test*"]),

    entry_points={'console_scripts': ['nbank = nbank.script:main',
                                      'nbank-migrate = nbank.migrate:main'] },

    install_requires=["requests>2.18"],
    test_suite='nose.collector'
)

# Variables:
# End:
