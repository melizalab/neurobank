# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""interact with the registry API

This module supports a subset of the HTTP endpoints and methods. Users need to
be able to register domains and datatypes, but we're not going to support
deleting them.

"""

# python 3 compatibility
from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

import logging
import posixpath as path
import requests as rq

_neurobank_scheme = 'neurobank'

log = logging.getLogger('registry')

def json(url, **params):
    """Retrieve json data from server and return as a dictionary, or None if no data"""
    r = rq.get(url, params=params, headers={'Accept': 'application/json'}, verify=False)
    log.debug("GET %s", r.url)
    r.raise_for_status()
    return r.json()


def get_datatypes(base_url):
    """ Return a list of known content type names """
    url = path.join(base_url, "datatypes/")
    return json(url)


def get_datatype(base_url, dtype):
    """ Return info about dtype """
    url = path.join(base_url, "datatypes/", dtype, "/")
    return json(url)


def get_domain(base_url, domain):
    """ Get info about domain, or raise an error if it does not exist """
    url = path.join(base_url, "domains", domain, "/")
    return json(url)


def get_domains(base_url):
    """ Return a list of known domains names """
    url = path.join(base_url, "domains/")
    return json(url)


def find_domain_by_path(base_url, root):
    """Return the domain name associated with path, or None if no such domain is defined

    Users are required to look up the domain name associated with an archive to
    avoid putting data somewhere it can't be found.

    """
    url = path.join(base_url, "domains/")
    try:
        return json(url, scheme=_neurobank_scheme, root=root)[0]["name"]
    except IndexError:
        return None


def resource_url(base_url, id):
    """ Return the full URL-based identifier for id """
    return path.join(base_url, "resources", id, "/")


def get_resource(base_url, id):
    """Look up a resource in the registry"""
    return json(resource_url(base_url, id))


def add_datatype(base_url, name, content_type, auth=None):
    """ Add a datatype to the registry """
    url = path.join(base_url, "datatypes/")
    r = rq.post(url, auth=auth, data = {"name": name, "content_type": content_type})
    log.debug("POST %s", r.url)
    r.raise_for_status()
    return r.json()


def add_domain(base_url, name, scheme, root, auth=None):
    """ Add a domain to the registry """
    url = path.join(base_url, "domains/")
    log.debug("POST %s", url)
    r = rq.post(url, auth=auth, data={"name": name, "scheme": scheme, "root": root})
    r.raise_for_status()
    return r.json()


def add_resource(base_url, id, dtype, domain, sha1=None, auth=None, **metadata):
    """Add a resource to the registry"""
    # add the resource
    url = path.join(base_url, "resources/")
    data = {"name": id, "dtype": dtype, "sha1": sha1, "locations": [domain]}
    data.update(metadata)
    r = rq.post(url, auth=auth, data=data)
    r.raise_for_status()
    return r.json()
