from django.conf import settings as django_settings
from django.core.management.base import BaseCommand
from fabric.api import *

from _core import load_config


class Command(BaseCommand):
    def handle(self, *args, **options):
        # Load server config from project
        config = load_config()

        # Define current host from settings in server config
        env.host_string = config['remote']['server']['ip']
        env.user = 'deploy'
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
            local('mkdir -p {}/uploads/'.format(
                django_settings.MEDIA_ROOT
            ))

            local('mkdir -p {}'.format(
                django_settings.STATIC_ROOT
            ))

            local('rsync -rh --exclude "assets/" {}@{}:/var/www/{}_media/ {}'.format(
                env.user,
                env.host_string,
                project_folder,
                django_settings.MEDIA_ROOT
            ))
