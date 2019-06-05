from django.conf import settings as django_settings
from fabric.api import (cd, env, hide, lcd, local, run, settings, shell_env,
                        sudo)
from fabvenv import virtualenv

from ._core import ServerManagementBaseCommand, load_config


class Command(ServerManagementBaseCommand):

    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser)

        parser.add_argument('--commit', default=None)

        parser.add_argument(
            '--force-update',
            action='store_true',
            dest='force_update',
            default=False,
            help='Force server to update, even if there are no changes detected.',
        )

    # DS: I've had a look through this method to see if we can strip it down any
    # further to make pylint happy, but everything which is here is for a reason.
    # It either provides different build environments to be used, or handles
    # different situations on the server. Hard to see where we could reduce code.

    def handle(self, *args, **options):  # pylint: disable=too-many-locals,too-many-statements
        # Load server config from project
        config, remote = load_config(env, options.get('remote', ''), debug=options.get('debug', False))

        # Set remote server name
        self.remote = config.get('remote_name')

        # Set local project path
        local_project_path = django_settings.SITE_ROOT

        # Get our python version - we'll need this while rebuilding the
        # virtualenv.
        python_version = remote['server'].get('python_version', '3')

        # Change into the local project folder
        with hide('output', 'running', 'warnings'), lcd(local_project_path):
            project_folder = local(f"basename $( find {local_project_path} -name 'wsgi.py' -not -path '*/.venv/*' -not -path '*/venv/*' | xargs -0 -n1 dirname )", capture=True)

        with settings(sudo_user=project_folder), cd(f'/var/www/{project_folder}'):
            initial_git_hash = run('git rev-parse --short HEAD')
            old_venv = f'/var/www/{project_folder}/.venv-{initial_git_hash}'

            settings_module = '{}.settings.{}'.format(
                project_folder,
                remote['server'].get('settings_file', 'production'),
            )

            sudo('git config --global user.email "developers@onespacemedia.com"')
            sudo('git config --global user.name "Onespacemedia Developers"')
            sudo('git config --global rebase.autoStash true')

            sudo('git pull')

            if options.get('commit', False):
                print('Pulling to specific commit.')
                sudo('git reset --hard {}'.format(
                    options.get('commit', False),
                ))
            else:
                print('Pulling to HEAD')
                sudo('git reset --hard HEAD')

            new_git_hash = run('git rev-parse --short HEAD')
            new_venv = f'/var/www/{project_folder}/.venv-{new_git_hash}'

            if initial_git_hash == new_git_hash and not options['force_update']:
                print('Server is already up to date.')
                exit()

            # Does the new venv folder already exist?
            with settings(warn_only=True):
                venv_folder = run(f'test -d {new_venv}')

            # Build the virtualenv.
            if venv_folder.return_code == 0:
                print('Using existing venv for this commit hash')

            if venv_folder.return_code > 0:
                print('Creating venv for this commit hash')

                # Check if we have PyPy
                with settings(warn_only=True):
                    pypy = run('test -x /usr/bin/pypy')

                if pypy.return_code == 0:
                    sudo(f'virtualenv -p /usr/bin/pypy {new_venv}')
                else:
                    sudo(f'virtualenv -p python{python_version} {new_venv}')

                with virtualenv(new_venv), shell_env(DJANGO_SETTINGS_MODULE=settings_module):
                    sudo('[[ -e requirements.txt ]] && pip install -r requirements.txt')
                    sudo('pip install gunicorn')

            # Things which need to happen regardless of whether there was a venv already.
            with virtualenv(new_venv), shell_env(DJANGO_SETTINGS_MODULE=settings_module):
                if remote['server'].get('build_system', 'npm') == 'npm':
                    sudo('. ~/.nvm/nvm.sh && yarn', shell='/bin/bash')
                    sudo('. ~/.nvm/nvm.sh && yarn run build', shell='/bin/bash')

                sudo('python manage.py collectstatic --noinput -l')

                sudo('yes yes | python manage.py migrate')

                requirements = sudo('pip freeze')

                for line in requirements.split('\n'):
                    if line.startswith('django-watson'):
                        sudo('python manage.py buildwatson')

        # Point the application to the new venv
        sudo(f'rm -rf /var/www/{project_folder}/.venv')
        sudo(f'ln -sf {new_venv} /var/www/{project_folder}/.venv')
        sudo(f'rm -rf {old_venv}')
        sudo(f'supervisorctl signal HUP {project_folder}')
