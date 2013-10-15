#!/usr/bin/env python

from distutils.core import setup

execfile('pyauditor/version.py')

kwargs = {
    "name": "pyauditor",
    "version": str(__version__),
    "packages": ["pyauditor"],
    "scripts": ["bin/alog"],
    "description": "Python Client Library for Auditor",
    "long_description": open("README").read(),
    "author": "Gary M. Josack",
    "maintainer": "Gary M. Josack",
    "author_email": "gary@byoteki.com",
    "maintainer_email": "gary@byoteki.com",
    "license": "MIT",
    "url": "https://github.com/gmjosack/pyauditor",
    "download_url": "https://github.com/gmjosack/pyauditor/archive/master.tar.gz",
    "classifiers": [
        "Programming Language :: Python",
        "Topic :: Software Development",
        "Topic :: Software Development :: Libraries",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ]
}

setup(**kwargs)
