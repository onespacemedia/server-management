from django.conf import settings as django_settings
from django.core.management.base import BaseCommand

from _core import load_config

from fabric.api import *


class Command(BaseCommand):

    def __init__(self):
        super(Command, self).__init__()

    def handle(self, *args, **options):
        # Load server config from project
        config = load_config()

        # Define current host from settings in server config
        env.host_string = config['remote']['server']['ip']
        env.user = 'root'
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
                project_folder = local(
                    "basename $( find {} -name 'wsgi.py' -not -path '*/.venv/*' -not -path '*/venv/*' | xargs -0 -n1 "
                    "dirname )".format(
                        local_project_path
                    ), capture=True)

        with settings(warn_only=True):
            local('rsync -rh {}/uploads/ root@{}:/var/www/{}_media/uploads/'.format(
                django_settings.MEDIA_ROOT,
                env.host_string,
                project_folder,
            ))
