# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

# The MIT License
#
# Copyright (c) 2017 Tammo Ippen, tammo.ippen@posteo.de
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
from geopip._geopip import GeoPIP

from py.coverage import get_coverage_geojson_dict

__all__ = [
    "GeoPIP",
    "instance",
    "search",
]

_INSTANCE = {}


def instance(cc):  # noqa: E302
    """Singleton GeoPIP instance (lazy loading) per cc

    Is used in the `search` function.
    """
    global _INSTANCE

    # Check if instance for this cc already exists
    if cc in _INSTANCE:
        return _INSTANCE[cc]

    # If not, create a new instance for this cc
    _INSTANCE[cc] = GeoPIP(geojson_dict=get_coverage_geojson_dict(cc))

    return _INSTANCE[cc]


def search(cc, lng, lat):
    """Reverse geocode lng/lat coordinate within the features from `instance().shapes`.

    Look within the features from the `instance().shapes` function for a polygon that
    contains the point (lng, lat). From the first found feature the `porperties`
    will be returned. `None`, if no feature containes the point.

    Parameters:
        lng: float  Longitude (-180, 180) of point. (WGS84)
        lat: float  Latitude (-90, 90) of point. (WGS84)

    Returns:
        Dict[Any, Any]  `Properties` of found feature. `None` if nothing is found.
    """
    return instance(cc).search(lng, lat)
