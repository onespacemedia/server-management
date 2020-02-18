from contextlib import contextmanager
import posixpath

from fabric.api import run, env
from fabric.context_managers import prefix, settings, hide
from fabric.contrib.files import exists

# Credits to Andreas Nüßlein. This a copy of his fabric3-virtualenv package which
# has been deleted from pypi. When I get around to updating this package to use
# Fabric2, then we can remove this package

# Default virtualenv command
env.virtualenv = 'virtualenv'

# URL of the standalone virtualenv.py
VIRTUALENV_PY_URL = \
    'https://raw.github.com/pypa/virtualenv/master/virtualenv.py'


@contextmanager
def virtualenv(path):
    """Context manager that performs commands with an active virtualenv, eg:

    path is the path to the virtualenv to apply

    >>> with virtualenv(env):
            run('python foo')

    It is highly recommended to use an absolute path, as Fabric's with cd()
    feature is always applied BEFORE virtualenv(), regardless of how they are
    nested.

    """
    activate = posixpath.join(path, 'bin/activate')
    if not exists(activate):
        raise OSError("Cannot activate virtualenv %s" % path)
    with prefix('. %s' % activate):
        yield


def _wget(url, out):
    """Helper for downloading a file, without requiring wget/curl etc."""
    cmd = (
        "python -c 'import urllib2,sys; "
        "print urllib2.urlopen(sys.argv[1]).read()' '{url}' >{out}"
    )
    run(cmd.format(url=url, out=out))


def prepare_virtualenv():
    """Prepare a working virtualenv command.

    The command will be available as env.virtualenv.
    """
    with hide('output', 'running'):
        venv = run('which virtualenv ; :')
        if venv:
            env.virtualenv = venv
            return

        if not exists('~/virtualenv.py'):
            _wget(VIRTUALENV_PY_URL, '~/virtualenv.py')
            run('chmod 755 ~/virtualenv.py')
        else:
            mode = int(run('stat -c %a ~/virtualenv.py'), 8)
            if mode & 0o2:
                raise IOError(
                    "~/virtualenv.py is world-writable. "
                    "Not using for security reasons."
                )
        env.virtualenv = '~/virtualenv.py'


def make_virtualenv(path, dependencies=[], eggs=[], system_site_packages=True,
                    python_binary=None):
    """Create or update a virtualenv in path.

    :param path: The path to the virtualenv. This path will be created if it
        does not already exist.
    :param dependencies: a list of paths or URLs to python packages to install.
    :param eggs: a list of paths or URLs to eggs to install. Eggs can be used
        to speed up deployments that require libraries to be compiled.
    :param system_site_packages: If True, the newly-created virtualenv will
        expose the system site package. If False, these will be hidden.
    :param python_binary: If not None, should be the path to python binary
        that will be used to create the virtualenv.

    """
    if not exists(path):
        version = tuple(run('%s --version' % env.virtualenv).split('.'))
        if version >= (1, 7):
            args = '--system-site-packages' if system_site_packages else ''
        else:
            args = '--no-site-packages' if not system_site_packages else ''

        if python_binary:
            args += '-p {}'.format(python_binary)

        run('{virtualenv} {args} {path}'.format(
            virtualenv=env.virtualenv,
            args=args,
            path=path
        ))
    else:
        # Update system-site-packages
        no_global_path = posixpath.join(
            path, 'lib/python*/no-global-site-packages.txt'
        )
        if system_site_packages:
            run('rm -f ' + no_global_path)
        else:
            run('touch ' + no_global_path)

    with virtualenv(path):
        for e in eggs:
            with settings(warn_only=True):
                run("easy_install '%s'" % e)
        for d in dependencies:
            run("pip install '%s'" % d)
