import os
from setuptools import setup

with open(os.path.join(os.path.dirname(__file__), 'README.md')) as readme:
    README = readme.read()

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

setup(
    name='onespacemedia-server-management',
    version='3.0.1',
    packages=[
        'server_management',
        'server_management.management',
        'server_management.management.commands',
    ],
    include_package_data=True,
    description='A set of server management tools used by Onespacemedia.',
    long_description=README,
    url='https://github.com/onespacemedia/server-management/',
    author='James Foley, Daniel Samuels',
    author_email='developers@onespacemedia.com',
    install_requires=['django', 'fabric3', 'requests', 'fabric3-virtualenv'],
)
