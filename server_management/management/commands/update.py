import os

from django.conf import settings as django_settings

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
        config, connection = load_config(options.get('remote', ''), debug=options.get('debug', False))
        remote = config['remotes'][config['remote_name']]

        # Get our python version - we'll need this while rebuilding the
        # virtualenv.
        python_version = remote['server'].get('python_version', '3')

        # Change into the local project folder
        project_name = os.path.basename(django_settings.SITE_ROOT)

        settings_module = f"{project_name}.settings.{remote['server'].get('settings_file', 'production')}"
        django_env = {
            'DJANGO_SETTINGS_MODULE': settings_module
        }

        with connection.cd(f'/var/www/{project_name}'):
            initial_git_hash = connection.run('git rev-parse --short HEAD')
            old_venv = f'/var/www/{project_name}/.venv-{initial_git_hash}'

            connection.sudo('git config --global user.email "developers@onespacemedia.com"', user=project_name)
            connection.sudo('git config --global user.name "Onespacemedia Developers"', user=project_name)
            connection.sudo('git config --global rebase.autoStash true', user=project_name)
            connection.sudo('git pull', user=project_name)

            if options.get('commit', False):
                print('Pulling to specific commit.')
                connection.sudo(f"git reset --hard {options.get('commit', False)}", user=project_name)
            else:
                print('Pulling to HEAD')
                connection.sudo('git reset --hard HEAD', user=project_name)

            new_git_hash = connection.run('git rev-parse --short HEAD', user=project_name)
            new_venv = f'/var/www/{project_name}/.venv-{new_git_hash}'

            if initial_git_hash == new_git_hash and not options['force_update']:
                print('Server is already up to date.')
                exit()

            # Does the new venv folder already exist?
            venv_folder = connection.run(f'test -d {new_venv}')

            # Build the virtualenv.
            if venv_folder.return_code == 0:
                print('Using existing venv for this commit hash')

            if venv_folder.return_code > 0:
                print('Creating venv for this commit hash')

                # Check if we have PyPy
                pypy = connection.run('test -x /usr/bin/pypy')

                if pypy.return_code == 0:
                    connection.sudo(f'virtualenv -p /usr/bin/pypy {new_venv}', user=project_name)
                else:
                    connection.sudo(f'virtualenv -p python{python_version} {new_venv}', user=project_name)

                with connection.prefix(f'workon {new_venv}'):
                    connection.sudo('[[ -e requirements.txt ]] && pip install -r requirements.txt', user=project_name, env=django_env)
                    connection.sudo('pip install gunicorn', user=project_name, env=django_env)

            # Things which need to happen regardless of whether there was a venv already.
            with connection.prefix(f'workon {new_venv}'):
                if remote['server'].get('build_system', 'npm') == 'npm':
                    connection.sudo('. ~/.nvm/nvm.sh && yarn', shell='/bin/bash', user=project_name, env=django_env)
                    connection.sudo('. ~/.nvm/nvm.sh && yarn run build', shell='/bin/bash', user=project_name, env=django_env)

                connection.sudo('python manage.py collectstatic --noinput -l', user=project_name, env=django_env)

                connection.sudo('yes yes | python manage.py migrate', user=project_name, env=django_env)

                requirements = connection.sudo('pip freeze', user=project_name, env=django_env)

                for line in requirements.split('\n'):
                    if line.startswith('django-watson'):
                        connection.sudo('python manage.py buildwatson', user=project_name, env=django_env)

        # Point the application to the new venv
        connection.sudo(f'rm -rf /var/www/{project_name}/.venv')
        connection.sudo(f'ln -sf {new_venv} /var/www/{project_name}/.venv')
        connection.sudo(f'rm -rf {old_venv}')
        connection.sudo(f'supervisorctl signal HUP {project_name}')
