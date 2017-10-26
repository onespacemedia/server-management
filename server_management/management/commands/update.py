import datetime
import json
import os
import sys
import requests
from django.conf import settings as django_settings
from fabric.api import sudo, run, hide, lcd, settings, shell_env, cd, local, env
from fabvenv import virtualenv

from ._core import load_config, ServerManagementBaseCommand


class Command(ServerManagementBaseCommand):

    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser)

        parser.add_argument(
            '--force-update',
            action='store_true',
            dest='force_update',
            default=False,
            help='Force server to update, even if there are no changes detected.',
        )

    def handle(self, *args, **options):
        # Load server config from project
        config, remote = load_config(env, options.get('remote', ''))

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
            settings_module = '{}.settings.{}'.format(
                project_folder,
                remote['server'].get('settings_file', 'production'),
            )

            # Check which venv we need to use.
            with settings(warn_only=True):
                result = run('bash -c \'[ -d venv ]\'')

            if result.return_code == 0:
                venv = '/var/www/{}/venv/'.format(project_folder)
            else:
                venv = '/var/www/{}/.venv/'.format(project_folder)

            sudo('git config --global user.email "developers@onespacemedia.com"')
            sudo('git config --global user.name "Onespacemedia Developers"')
            sudo('git config --global rebase.autoStash true')
            git_changes = sudo('git pull --rebase')

            if 'is up to date.' in git_changes and not options['force_update']:
                self.stdout.write('Server is up to date.')
                exit()

            if ('requirements' in git_changes) or options['force_update']:
                # Rebuild the virtualenv.
                sudo('rm -rf {}'.format(venv))

                # Check if we have PyPy
                with settings(warn_only=True):
                    result = run('test -x /usr/bin/pypy')

                if result.return_code == 0:
                    sudo('virtualenv -p /usr/bin/pypy {}'.format(venv))
                else:
                    sudo('virtualenv -p python{} {}'.format(python_version, venv))

                with virtualenv(venv):
                    with shell_env(DJANGO_SETTINGS_MODULE=settings_module):
                        sudo('[[ -e requirements.txt ]] && pip install -qr requirements.txt')

            with virtualenv(venv):
                with shell_env(DJANGO_SETTINGS_MODULE=settings_module):
                    sudo('pip install -q gunicorn')

                    if remote['server'].get('build_system', 'npm') == 'npm':
                        sudo('. ~/.nvm/nvm.sh && yarn', shell='/bin/bash')
                        sudo('. ~/.nvm/nvm.sh && yarn run build', shell='/bin/bash')

                    sudo('python manage.py collectstatic --noinput')

                    requirements = sudo('pip freeze')
                    compressor = False
                    watson = False
                    for line in requirements.split('\n'):
                        if line.startswith('django-compressor'):
                            compressor = True
                        if line.startswith('django-watson'):
                            watson = True

                    if not compressor:
                        sudo('python manage.py compileassets')

                    sudo('yes yes | python manage.py migrate')

                    if watson:
                        sudo('python manage.py buildwatson')

        sudo("sudo su -c \"find /var/www/{project_folder}_static/ -type f \( -name '*.js' -o -name '*.css' -o -name '*.svg' \) -exec gzip -v -k -f --best {{}} \;\" {project_folder}".format(
            project_folder=project_folder,
        ))
        sudo('supervisorctl restart all')

        # Register the release with Opbeat.
        if 'opbeat' in config and config['opbeat']['app_id'] and config['opbeat']['secret_token']:
            with(lcd(local_project_path)):
                local('curl https://intake.opbeat.com/api/v1/organizations/{}/apps/{}/releases/'
                      ' -H "Authorization: Bearer {}"'
                      ' -d rev=`git log -n 1 --pretty=format:%H`'
                      ' -d branch=`git rev-parse --abbrev-ref HEAD`'
                      ' -d status=completed'.format(
                          config['opbeat']['organization_id'],
                          config['opbeat']['app_id'],
                          config['opbeat']['secret_token'],
                      ))
