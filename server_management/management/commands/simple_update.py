import os
import sys

from django.conf import settings as django_settings
from fabric.api import *
from fabric.contrib.console import confirm
from fabvenv import virtualenv

from ._core import ServerManagementBaseCommand, load_config


class Command(ServerManagementBaseCommand):

    slack_enabled = False
    slack_endpoints = []

    current_commit = os.popen("git rev-parse --short HEAD").read().strip()
    remote = os.popen("git config --get remote.origin.url").read().split(':')[1].split('.')[0]
    remote = 'production'

    def handle_exception(self, exctype, value, traceback):
        self._notify_failed(str(value))
        sys.__excepthook__(exctype, value, traceback)

    def handle(self, *args, **options):
        # Load server config from project
        config, remote = load_config(env, options.get('remote', ''))

        # Set remote server name
        self.remote = config.get('remote_name')

        # Set local project path
        local_project_path = django_settings.SITE_ROOT

        # Change into the local project folder
        with hide('output', 'running', 'warnings'):
            with lcd(local_project_path):

                project_folder = local("basename $( find {} -name 'wsgi.py' -not -path '*/.venv/*' -not -path '*/venv/*' | xargs -0 -n1 dirname )".format(
                    local_project_path
                ), capture=True)

        with cd('/var/www/{}'.format(project_folder)):
            self.server_commit = run("git rev-parse --short HEAD")

            # Check which venv we need to use.
            with settings(warn_only=True):
                result = run("bash -c '[ -d venv ]'")

            if result.return_code == 0:
                venv = '/var/www/{}/venv/'.format(project_folder)
            else:
                venv = '/var/www/{}/.venv/'.format(project_folder)

            sudo('chown {}:webapps -R /var/www/*'.format(project_folder))
            sudo('chmod -R g+w /var/www/{}*'.format(project_folder))
            sudo('chmod ug+rwX -R /var/www/{}/.git'.format(project_folder))

            # Ensure the current user is in the webapps group.
            sudo('usermod -aG webapps {}'.format(env.user))

            run('git config --global user.email "developers@onespacemedia.com"')
            run('git config --global user.name "Onespacemedia Developers"')
            run('git stash')
            git_changes = run('git pull')

            sudo('chmod -R g+w /var/www/{}*'.format(project_folder))

            with virtualenv(venv):
                with shell_env(DJANGO_SETTINGS_MODULE="{}.settings.{}".format(
                    project_folder,
                    remote['server'].get('settings_file', 'production')
                )):

                    if confirm('pip?', default=False):
                        sudo('[[ -e requirements.txt ]] && pip install -qr requirements.txt', user=project_folder)

                    if remote['server'].get('build_system', 'npm') == 'npm' and confirm('npm?', default=False):
                        sudo('npm run build')

                    if confirm('collectstatic?', default=False):
                        run('python manage.py collectstatic --noinput')

                    if confirm('restart memcached too?', default=False):
                        sudo('supervisorctl restart all')
                    else:
                        sudo('supervisorctl restart {}'.format(project_folder))
                    sudo('chown {}:webapps -R /var/www/*'.format(project_folder))

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


sys.excepthook = Command().handle_exception
