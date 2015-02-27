import os
from setuptools import setup

with open(os.path.join(os.path.dirname(__file__), 'README.rst')) as readme:
    README = readme.read()

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

setup(
    name='onespacemedia-server-management',
    version='0.1.1',
    packages=[
        'server_management',
        'server_management.management',
        'server_management.management.commands',
    ],
    include_package_data=True,
    description='A set of server management tools used by Onespacemedia.',
    long_description=README,
    url='http://www.onespacemedia.com/',
    author='James Foley',
    author_email='jamesfoley@onespacemedia.com',
    install_requires=['django', 'fabric', 'ansible', 'requests', 'fabvenv'],
)
