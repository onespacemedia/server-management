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

    slack_enabled = False
    slack_endpoints = []

    current_commit = os.popen('git rev-parse --short HEAD').read().strip()
    remote = os.popen('git config --get remote.origin.url').read().split(':')[1].split('.')[0]
    remote = 'production'

    def _bitbucket_commit_url(self, commit):
        return '<https://bitbucket.org/{}/commits/{commit}|{commit}>'.format(
            self.remote,
            commit=commit,
        )

    def _bitbucket_diff_url(self, commit1, commit2):
        return '<https://bitbucket.org/{}/branches/compare/{}..{}#diff|diff>'.format(
            self.remote,
            commit2,
            commit1,
        )

    def _notify_start(self):
        if not self.slack_enabled:
            return

        self.start_time = datetime.datetime.now()

        for endpoint in self.slack_endpoints:
            requests.post(endpoint['url'], data={
                'payload': json.dumps({
                    'channel': endpoint['channel'],
                    'username': endpoint['name'],
                    'icon_emoji': endpoint['emoji'],
                    'attachments': [{
                        'fallback': 'Update of {} to {} has begun.'.format(
                            django_settings.SITE_NAME,
                            self.remote
                        ),
                        'color': '#22A7F0',
                        'fields': [
                            {
                                'title': 'Project',
                                'value': '{} ({})'.format(
                                    django_settings.SITE_NAME,
                                    self.remote,
                                ),
                                'short': True,
                            },
                            {
                                'title': 'Update status',
                                'value': 'Started',
                                'short': True,
                            },
                            {
                                'title': 'Commit hash',
                                'value': self._bitbucket_commit_url(self.current_commit),
                                'short': True,
                            },
                            {
                                'title': 'User',
                                'value': os.popen("whoami").read().strip(),
                                'short': True,
                            }
                        ]
                    }]
                })
            })

    def _notify_success(self):
        if not self.slack_enabled:
            return

        self.end_time = datetime.datetime.now()

        for endpoint in self.slack_endpoints:
            requests.post(endpoint['url'], data={
                'payload': json.dumps({
                    'channel': endpoint['channel'],
                    'username': endpoint['name'],
                    'icon_emoji': endpoint['emoji'],
                    'attachments': [{
                        'fallback': 'Update of {} to {} has completed successfully.'.format(
                            django_settings.SITE_NAME,
                            self.remote
                        ),
                        'color': 'good',
                        'fields': [
                            {
                                'title': 'Project',
                                'value': '{} ({})'.format(
                                    django_settings.SITE_NAME,
                                    self.remote,
                                ),
                                'short': True,
                            },
                            {
                                'title': 'Update status',
                                'value': 'Successful',
                                'short': True,
                            },
                            {
                                'title': 'Duration',
                                'value': '{}.{} seconds'.format(
                                    (self.end_time - self.start_time).seconds,
                                    str((self.end_time - self.start_time).microseconds)[:2],
                                ),
                                'short': True,
                            },
                            {
                                'title': 'Commit range',
                                'value': '{} to {} ({})'.format(
                                    self._bitbucket_commit_url(self.server_commit),
                                    self._bitbucket_commit_url(self.current_commit),
                                    self._bitbucket_diff_url(self.current_commit, self.server_commit)
                                ),
                                'short': True,
                            }
                        ]
                    }]
                })
            })

    def _notify_failed(self, message):
        if not self.slack_enabled:
            return

        for endpoint in self.slack_endpoints:
            requests.post(endpoint['url'], data={
                'payload': json.dumps({
                    'channel': endpoint['channel'],
                    'username': endpoint['name'],
                    'icon_emoji': endpoint['emoji'],
                    'attachments': [{
                        'fallback': 'Update of {} to {} has failed'.format(
                            django_settings.SITE_NAME,
                            self.remote
                        ),
                        'color': 'danger',
                        'fields': [
                            {
                                'title': 'Project',
                                'value': '{} ({})'.format(
                                    django_settings.SITE_NAME,
                                    self.remote,
                                ),
                                'short': True,
                            },
                            {
                                'title': 'Update status',
                                'value': 'Failed',
                                'short': True,
                            },
                            {
                                'title': 'Error message',
                                'value': message,
                            }
                        ]
                    }]
                })
            })

    def handle_exception(self, exctype, value, traceback):
        self._notify_failed(str(value))
        sys.__excepthook__(exctype, value, traceback)

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

        # Load slack config
        self.slack_enabled = config.get('slack', {'enabled': False})['enabled']
        if self.slack_enabled:
            self.slack_endpoints = config.get('slack', {'endpoints': []})['endpoints']

        self._notify_start()

        # Set local project path
        local_project_path = django_settings.SITE_ROOT

        # Get our python version - we'll need this while rebuilding the
        # virtualenv.
        python_version = remote['server'].get('python_version', '3')

        # Change into the local project folder
        with hide('output', 'running', 'warnings'):
            with lcd(local_project_path):
                project_folder = local("basename $( find {} -name 'wsgi.py' -not -path '*/.venv/*' -not -path '*/venv/*' | xargs -0 -n1 dirname )".format(
                    local_project_path
                ), capture=True)

        with settings(sudo_user=project_folder), cd('/var/www/{}'.format(project_folder)):
            self.server_commit = run('git rev-parse --short HEAD')
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

        self._notify_success()


sys.excepthook = Command().handle_exception
