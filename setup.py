#!/usr/bin/env python
# -*- coding: utf-8 -*-
# -*- mode: python -*-
import sys
from setuptools import setup, find_packages

if sys.hexversion < 0x02060000:
    raise RuntimeError("Python 2.6 or higher required")

# --- Distutils setup and metadata --------------------------------------------

VERSION = '0.7.0'

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
if sys.hexversion < 0x02070000:
    requirements.append("argparse==1.2.1")

setup(
    name='neurobank',
    version=VERSION,
    description=short_desc,
    long_description=long_desc,
    classifiers=[x for x in cls_txt.split("\n") if x],
    author='Dan Meliza',
    author_email="dan@meliza.org",
    maintainer='Dan Meliza',
    maintainer_email="dan@meliza.org",
    url="https://github.com/melizalab/neurobank",

    packages=find_packages(exclude=["*test*"]),

    entry_points={'console_scripts': ['nbank = nbank.script:main'] },

    install_requires=requirements,
    test_suite='nose.collector'
)

# Variables:
# End:
