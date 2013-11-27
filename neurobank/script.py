# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""Script entry points for neurobank

Copyright (C) 2013 Dan Meliza <dan@meliza.org>
Created Tue Nov 26 22:48:58 2013
"""

def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(description='manage source files and collected data')
    sub = p.add_subparsers(title='subcommands')

    p_init = sub.add_parser('init', help='initialize a data archive')
    p_init.add_argument('directory', help="path of the (possibly non-existent) directory "
                        "for the archive. If the directory does not exist it's created. "
                        "If not supplied the current directory is used. Does not overwrite")

    p_reg = sub.add_parser('register', help='register source files')
    p_reg.add_argument('--suffix', help='add a suffix to the generated identifiers')
    p_reg.add_argument('file', help='path of file(s) to add to the repository')

    p.parse_args(argv)


# Variables:
# End:
