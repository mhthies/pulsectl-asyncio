#!/usr/bin/env python
import os.path
from setuptools import setup, find_packages

with open(os.path.join(os.path.dirname(__file__), 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='pulsectl-asyncio',
    version='0.1.7',
    description='Asyncio frontend for the pulsectl Python bindings of libpulse',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='Michael Thies',
    author_email='mail@mhthies.de',
    url='https://github.com/mhthies/pulsectl-asyncio',
    packages=['pulsectl_asyncio'],
    python_requires='~=3.6',
    install_requires=[
        'pulsectl>=20.5.1,<=21.5.18',
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'License :: OSI Approved :: MIT License',
        'Framework :: AsyncIO',
        'Topic :: Multimedia :: Sound/Audio :: Mixers',
    ],
)
