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

log = logging.getLogger('nbank')


def strip_nulls(d):
    """Removes all keys from a dict that are equal to None"""
    return {k: v for k, v in d.items() if v is not None}


def json(url, **params):
    """Retrieve json data from server and return as a dictionary, or None if no data"""
    r = rq.get(url, params=params, headers={'Accept': 'application/json'}, verify=True)
    log.debug("GET %s", r.url)
    r.raise_for_status()
    log.debug("  %s", r.text)
    return r.json()


def get_datatypes(base_url):
    """ Return a list of known content type names """
    url = path.join(base_url, "datatypes/")
    return json(url)


def get_archives(base_url):
    """ Return a list of known archive names """
    url = path.join(base_url, "archives/")
    return json(url)


def find_archive_by_path(base_url, root):
    """Return the archive name associated with path, or None if no such archive is defined

    Users are required to look up the archive name associated with an archive to
    avoid putting data somewhere it can't be found.

    """
    url = path.join(base_url, "archives/")
    try:
        return json(url, scheme=_neurobank_scheme, root=root)[0]["name"]
    except IndexError:
        return None


def find_resource(base_url, **params):
    """Find all resources that match params

    Because this list may be long and paginated by the server results are yielded one by one
    """
    url = path.join(base_url, "resources/")
    r = rq.get(url, params=params, headers={'Accept': 'application/json'}, verify=True)
    r.raise_for_status()
    for d in r.json():
        yield d
    while "next" in r.links:
        url = r.links["next"]["url"]
        # we may need to throttle request rate
        r = rq.get(url, params=params, headers={'Accept': 'application/json'}, verify=True)
        r.raise_for_status()
        for d in r.json():
            yield d


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
    """Return registry record for id, or None if it doesn't exist"""
    try:
        return json(full_url(base_url, id))
    except rq.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return None
        else:
            raise e


def get_locations(base_url, id):
    """Look up the locations of a resource. Returns an empty list if the resource doesn't exist"""
    url = path.join(base_url, "resources", id, "locations") + "/"
    try:
        return json(url)
    except rq.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return []
        else:
            raise e


def add_datatype(base_url, name, content_type, auth=None):
    """ Add a datatype to the registry """
    url = path.join(base_url, "datatypes/")
    r = rq.post(url, auth=auth, json={"name": name, "content_type": content_type})
    log.debug("POST %s", r.url)
    r.raise_for_status()
    return r.json()


def add_archive(base_url, name, scheme, root, auth=None):
    """ Add a archive to the registry """
    url = path.join(base_url, "archives/")
    log.debug("POST %s", url)
    r = rq.post(url, auth=auth, json={"name": name, "scheme": scheme, "root": root})
    r.raise_for_status()
    return r.json()


def add_resource(base_url, id, dtype, archive, sha1=None, auth=None, **metadata):
    """Add a resource to the registry"""
    # add the resource
    url = path.join(base_url, "resources/")
    data = {"name": id, "dtype": dtype, "sha1": sha1, "locations": [archive],
            "metadata": metadata}
    log.debug("POST %s: %s", url, data)
    r = rq.post(url, auth=auth, json=strip_nulls(data))
    log.debug("  response: %s", r.text)
    r.raise_for_status()
    return r.json()


def update_resource_metadata(base_url, id, auth=None, **metadata):
    """Update metadata for a resource.

    Requires authorization to PATCH in the registry. Set a key to None to delete.

    """
    url = path.join(base_url, "resources", id) + "/"
    data = {"metadata": metadata}
    log.debug("PATCH %s: %s", url, data)
    r = rq.patch(url, auth=auth, json=strip_nulls(data))
    log.debug("  response: %s", r.text)
    r.raise_for_status()
    return r.json()


def log_error(err):
    """Writes error message from server to log

    Reraises the error if its status code is not Bad Request (400)
    """
    if err.response.status_code == 400:
        data = err.response.json()
        for k, v in data.items():
            for vv in v:
                log.error("   error: %s: %s", k, vv)
    else:
        raise err


# def get_datatype(base_url, dtype):
#     """ Return info about dtype """
#     url = path.join(base_url, "datatypes/", dtype) + "/"
#     return json(url)


# def get_archive(base_url, archive):
#     """ Get info about archive, or raise an error if it does not exist """
#     url = path.join(base_url, "archives", archive) + "/"
#     return json(url)
