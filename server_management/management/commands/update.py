from django.conf import settings as django_settings
from django.core.management.base import BaseCommand
from fabric.api import *
from fabvenv import virtualenv

from _core import load_config


class Command(BaseCommand):
    def handle(self, *args, **options):
        # Load server config from project
        config = load_config()

        # Define current host from settings in server config
        env.host_string = config['remote']['server']['ip']
        env.user = 'ubuntu'
        env.disable_known_hosts = True
        env.reject_unknown_hosts = False

        # Make sure we can connect to the server
        with hide('output', 'running', 'warnings'):
            with settings(warn_only=True):
                if not run('whoami'):
                    print "Failed to connect to remote server"
                    exit()

        # Set local project path
        local_project_path = django_settings.SITE_ROOT

        # Change into the local project folder
        with hide('output', 'running', 'warnings'):
            with lcd(local_project_path):

                project_folder = local("basename $( find {} -name 'wsgi.py' -not -path '*/.venv/*' -not -path '*/venv/*' | xargs -0 -n1 dirname )".format(
                    local_project_path
                ), capture=True)

        with settings(warn_only=True):
            with cd('/var/www/{}'.format(project_folder)):
                # Check which venv we need to use.
                result = run("bash -c '[ -d venv ]'")

                if result.return_code == 0:
                    venv = '/var/www/{}/venv/'.format(project_folder)
                else:
                    venv = '/var/www/{}/.venv/'.format(project_folder)

                with virtualenv(venv):
                    with shell_env(DJANGO_SETTINGS_MODULE="{}.settings.production".format(project_folder)):
                        sudo('chown deploy:webapps -R /var/www/*')

                        sudo('git config --global user.email "developers@onespacemedia.com"')
                        sudo('git config --global user.name "Onespacemedia Developers"')
                        sudo('git config --global rebase.autoStash true')
                        sudo('git stash', user='deploy')
                        sudo('git reset --hard HEAD', user='deploy')
                        sudo('git pull', user='deploy')

                        sudo('chown {}:webapps -R /var/www/*'.format(project_folder))
                        sudo('[[ -e requirements.txt ]] && pip install -r requirements.txt', user=project_folder)

                        sudo('[[ -e Gulpfile.js ]] && gulp styles', user=project_folder)

                        sudo('./manage.py collectstatic -l --noinput', user=project_folder)

                        requirements = run('pip freeze')
                        compressor = False
                        watson = False
                        for line in requirements.split('\n'):
                            if line.startswith('django-compressor'):
                                compressor = True
                            if line.startswith('django-watson'):
                                watson = True

                        if not compressor:
                            sudo('./manage.py compileassets', user=project_folder)

                        sudo('./manage.py migrate', user=project_folder)

                        if watson:
                            sudo('./manage.py buildwatson', user=project_folder)

                        sudo('supervisorctl restart {}'.format(project_folder))
                        sudo('chown {}:webapps -R /var/www/*'.format(project_folder))
