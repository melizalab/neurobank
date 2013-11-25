#!/usr/bin/env python
# -*- coding: utf-8 -*-
# -*- mode: python -*-
import sys
if sys.hexversion < 0x02060000:
    raise RuntimeError, "Python 2.6 or higher required"

# setuptools 0.7+ doesn't play nice with distribute, so try to use existing
# package if possible
try:
    from setuptools import setup, find_packages, Extension
except ImportError:
    from ez_setup import use_setuptools
    use_setuptools()
    from setuptools import setup, find_packages, Extension

import sys

# --- Distutils setup and metadata --------------------------------------------

VERSION = '0.1.0-SNAPSHOT'

cls_txt = """
Development Status :: 5 - Production/Stable
Intended Audience :: Science/Research
License :: OSI Approved :: GNU General Public License (GPL)
Programming Language :: Python
Topic :: Scientific/Engineering
Operating System :: Unix
Operating System :: POSIX :: Linux
Operating System :: MacOS :: MacOS X
Natural Language :: English
"""

short_desc = "Advanced Recording Format Tools"

long_desc = """Commandline tools for reading and writing Advanced Recording Format files.
ARF files are HDF5 files used to store audio and neurophysiological recordings
in a rational, hierarchical format. Data are organized around the concept of an
entry, which is a set of data channels that all start at the same time.

"""

requirements = ["arf==2.1.0", "ewave==1.0.3"]
if sys.hexversion < 0x02070000:
    requirements.append("argparse==1.2.1")

setup(
    name='neurobank',
    version=VERSION,
    description=short_desc,
    long_description=long_desc,
    classifiers=[x for x in cls_txt.split("\n") if x],
    author='Dan Meliza',
    author_email='"dan" at the domain "meliza.org"',
    maintainer='Dan Meliza',
    maintainer_email='"dan" at the domain "meliza.org"',
    url="https://github.com/melizalab/neurobank",

    packages=find_packages(exclude=["*test*"]),

    # entry_points={'arfx.io': ['.pcm = arfx.pcmio:pcmfile',
    #                           '.wav = ewave:wavfile',
    #                           '.pcm_seq2 = arfx.pcmseqio:pseqfile',
    #                           '.pcm_seq = arfx.pcmseqio:pseqfile',
    #                           '.pcmseq2 = arfx.pcmseqio:pseqfile',
    #                           ],
    #               'console_scripts': ['arfx = arfx.arfx:arfx',
    #                                   'arfxplog = arfx.arfxplog:arfxplog'],
    #               },

    install_requires=requirements,
    test_suite='nose.collector'
)
# Variables:
# End:
