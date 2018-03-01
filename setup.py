import os
import sys

from setuptools import setup
from setuptools.command.install import install

with open(os.path.join(os.path.dirname(__file__), 'README.md')) as readme:
    README = readme.read()

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

VERSION = '3.0.4'

class VerifyVersionCommand(install):
    """Custom command to verify that the git tag matches our version"""
    description = 'verify that the git tag matches our version'

    def run(self):
        tag = os.getenv('CIRCLE_TAG')

        if tag != VERSION:
            info = "Git tag: {0} does not match the version of this app: {1}".format(
                tag, VERSION
            )
            sys.exit(info)

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
    long_description=README,
    url='https://github.com/onespacemedia/server-management/',
    author='James Foley, Daniel Samuels',
    author_email='developers@onespacemedia.com',
    python_requires='>=3',
    install_requires=['django', 'fabric3', 'requests', 'fabric3-virtualenv'],
    extras_require={
        'testing': [
            'astroid==1.4.8',
            'coveralls',
            'pytest',
            'pytest-cov',
            'pytest-django',
            'pylint==1.6.5',
            'pylint-django==0.7.2',
            'pylint-mccabe==0.1.3',
            'isort==4.2.5',
        ]
    },
    cmdclass={
        'verify': VerifyVersionCommand,
    }
)
