import os
import sys

from setuptools import setup
from setuptools.command.install import install

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

VERSION = '3.3.0'


setup(
    name='onespacemedia-server-management',
    version=VERSION,
    packages=[
        'server_management',
        'server_management.management',
        'server_management.management.commands',
    ],
    include_package_data=True,
    description='A set of server management tools used by Onespacemedia.',
    url='https://github.com/onespacemedia/server-management/',
    author='James Foley, Daniel Samuels, Aidan Currah',
    author_email='developers@onespacemedia.com',
    python_requires='>=3',
    install_requires=['django', 'fabric3', 'requests', 'fabric3-virtualenv'],
    extras_require={
        'testing': [
            'astroid==1.5.3',
            'coveralls',
            'pytest',
            'pytest-cov',
            'pytest-django',
            'pylint==1.7.5',
            'pylint-django==0.7.2',
            'pylint-mccabe==0.1.3',
            'isort==4.2.15',
        ]
    }
)
