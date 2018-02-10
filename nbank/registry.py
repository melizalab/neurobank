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

def strip_nulls(d):
    """Removes all keys from a dict that are equal to None"""
    return {k: v for k, v in d.items() if v is not None}

def json(url, **params):
    """Retrieve json data from server and return as a dictionary, or None if no data"""
    r = rq.get(url, params=params, headers={'Accept': 'application/json'}, verify=False)
    log.debug("GET %s", r.url)
    r.raise_for_status()
    log.debug("  %s", r.json())
    return r.json()


def get_datatypes(base_url):
    """ Return a list of known content type names """
    url = path.join(base_url, "datatypes/")
    return json(url)


def get_domains(base_url):
    """ Return a list of known domain names """
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

def find_resource_by_name(base_url, query):
    url = path.join(base_url, "resources/")
    return json(url, name=query)


def parse_resource_id(base_url, id):
    """ Parse a full or short resource identifier into base url and id

    http://domain.name/app/resources/id/ -> (http://domain.name/app/, id)
    id -> (base_url, id)
    """
    import posixpath as pp
    try:
        from urllib.parse import urlparse
    except ImportError:
        from urlparse import urlparse
    pr = urlparse(id)
    if pr.scheme and pr.netloc:
        base, sid = pp.split(id)
        return pp.dirname(base), id
    else:
        return base_url, id


def full_url(base_url, id):
    """ Return the full URL-based identifier for id """
    return path.join(base_url, "resources", id) + "/"


def get_resource(base_url, id):
    """Look up a resource in the registry"""
    return json(full_url(base_url, id))


def get_locations(base_url, id):
    url = path.join(base_url, "resources", id, "locations") + "/"
    return json(url)


def add_datatype(base_url, name, content_type, auth=None):
    """ Add a datatype to the registry """
    url = path.join(base_url, "datatypes/")
    r = rq.post(url, auth=auth, json={"name": name, "content_type": content_type})
    log.debug("POST %s", r.url)
    r.raise_for_status()
    return r.json()


def add_domain(base_url, name, scheme, root, auth=None):
    """ Add a domain to the registry """
    url = path.join(base_url, "domains/")
    log.debug("POST %s", url)
    r = rq.post(url, auth=auth, json={"name": name, "scheme": scheme, "root": root})
    r.raise_for_status()
    return r.json()


def add_resource(base_url, id, dtype, domain, sha1=None, auth=None, **metadata):
    """Add a resource to the registry"""
    # add the resource
    url = path.join(base_url, "resources/")
    data = {"name": id, "dtype": dtype, "sha1": sha1, "locations": [domain],
            "metadata": metadata}
    log.debug("POST %s: %s", url, data)
    r = rq.post(url, auth=auth, json=strip_nulls(data))
    log.debug("  response: %s", r.text)
    r.raise_for_status()
    return r.json()


# def get_datatype(base_url, dtype):
#     """ Return info about dtype """
#     url = path.join(base_url, "datatypes/", dtype) + "/"
#     return json(url)


# def get_domain(base_url, domain):
#     """ Get info about domain, or raise an error if it does not exist """
#     url = path.join(base_url, "domains", domain) + "/"
#     return json(url)