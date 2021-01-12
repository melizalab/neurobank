# -*- coding: utf-8 -*-
# -*- mode: python -*-
""" import catalog from old nbank (<0.7.0) into registry

This script performs the following checks:
- catalog is from correct version of nbank
- archive contains the resources referenced in the catalog
- if supplied, hash matches hash of file

"""

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

import logging

from nbank import __version__
from nbank import core

log = logging.getLogger("nbank")  # root logger


def check_catalog(catalog):
    """ Checks catalog for conformance to the nbank namespace and version. Raises ValueError on failure """
    if not catalog.get("namespace", None) == "neurobank.catalog":
        raise ValueError(
            "document does not have 'namespace' field set to 'neurobank.catalog'"
        )
    if not catalog.get("version", None) == "1.0":
        raise ValueError("catalog version is not equal to '1.0'")
    if "resources" not in catalog:
        raise ValueError("document is missing 'resources' field")
    if not isinstance(catalog["resources"], (list, tuple)):
        raise ValueError("'resources' field is not a list or tuple")


def register_resources(
    catalog, archive_path, dtype=None, hash=False, auth=None, **metadata
):
    """ Add resources from catalog (if found in archive_path) to neurobank archive """
    import os
    import requests as rq
    from nbank import util
    from nbank.core import full_url
    from nbank.archive import get_config, find_resource
    from nbank.registry import (
        add_resource,
        get_resource,
        find_archive_by_path,
    )

    archive_path = os.path.abspath(archive_path)
    cfg = get_config(archive_path)
    log.info("archive: %s", archive_path)
    registry_url = cfg["registry"]
    log.info("   registry: %s", registry_url)

    # check that archive exists for this path
    archive = find_archive_by_path(registry_url, archive_path)
    log.info("   archive name: %s", archive)
    if archive is None:
        raise RuntimeError(
            "archive '%s' not in registry. make sure to run nbank init before migrating"
            % archive_path
        )

    for res in catalog["resources"]:
        id = res.pop("id", None)
        if id is None:
            continue
        log.info("processing resource '%s':", id)
        # it's worth it to hit the API here to check whether the resource has
        # already been added
        chk = get_resource(registry_url, id)
        if chk is not None:
            log.info("    skipping: already in registry")
            continue
        resource_path = find_resource(archive_path, id)
        if resource_path is None:
            log.info("   does not exist; skipping")
            continue
        else:
            log.info("   path: %s", resource_path)
        if hash or cfg["policy"]["require_hash"]:
            sha1 = util.hash(resource_path)
            log.info("   sha1: %s", sha1)
        else:
            sha1 = None
        # merge metadata from catalog and arguments:
        res.update(**metadata)
        try:
            result = add_resource(registry_url, id, dtype, archive, sha1, auth, **res)
        except rq.exceptions.HTTPError as e:
            # bad request means the archive name is taken or badly formed
            if e.response.status_code == 400:
                data = e.response.json()
                for k, v in data.items():
                    for vv in v:
                        log.warn("   skipping: %s", vv)
                continue
            else:
                raise e
        registry_id = full_url(result["name"], registry_url)
        log.info("   registered as %s", registry_id)
        yield {"source": resource_path, "id": result["name"]}


def main(argv=None):
    import datetime
    import argparse
    import sys
    import json
    from nbank.script import userpwd, ParseKeyVal

    p = argparse.ArgumentParser(
        description="import catalog from old nbank (<0.7.0) into registry"
    )
    p.add_argument(
        "-v", "--version", action="version", version="%(prog)s " + __version__
    )
    p.add_argument(
        "-r",
        dest="registry_url",
        help="URL of the registry service. "
        "Default is to use the environment variable '%s'" % core.env_registry,
        default=core.default_registry(),
    )
    p.add_argument(
        "-a",
        dest="auth",
        help="username:password to authenticate with registry. "
        "If not supplied, will attempt to use .netrc file",
        type=userpwd,
        default=None,
    )
    p.add_argument("--debug", help="show verbose log messages", action="store_true")

    p.add_argument(
        "-d", "--dtype", help="specify the datatype for the deposited resources"
    )
    p.add_argument(
        "-H",
        "--hash",
        action="store_true",
        help="calculate a SHA1 hash of each file and store in the registry",
    )
    p.add_argument(
        "-k",
        help="specify metadata field (use multiple -k for multiple values)",
        action=ParseKeyVal,
        default=dict(),
        metavar="KEY=VALUE",
        dest="metadata",
    )
    p.add_argument(
        "-j",
        "--json-out",
        action="store_true",
        help="output each deposited file to stdout as line-deliminated JSON",
    )
    p.add_argument(
        "directory",
        help="path of the archive where the files are stored. "
        "This location needs to have been added as a archive to the registry (with nbank init) "
        "before running this script.",
    )
    p.add_argument("catalog", help="the JSON catalog to import")

    args = p.parse_args(argv)

    ch = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    loglevel = logging.DEBUG if args.debug else logging.INFO
    log.setLevel(loglevel)
    ch.setLevel(loglevel)  # change
    ch.setFormatter(formatter)
    log.addHandler(ch)

    log.debug("version: %s", __version__)
    log.debug("run time: %s", datetime.datetime.now())

    try:
        log.debug("checking catalog: %s", args.catalog)
        catalog = json.load(open(args.catalog, "rU"))
        check_catalog(catalog)
        for res in register_resources(
            catalog, args.directory, args.dtype, args.hash, args.auth, **args.metadata
        ):
            if args.json_out:
                json.dump(res, fp=sys.stdout)
                sys.stdout.write("\n")
    except Exception as e:
        log.error("    fatal error: %s", e)
