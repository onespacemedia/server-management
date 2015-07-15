from django.conf import settings as django_settings
from django.core.management.base import BaseCommand
from fabric.contrib.console import confirm

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
        env.user = 'deploy'
        env.disable_known_hosts = True
        env.reject_unknown_hosts = False

        # Ask the user if the server we are hosting on is AWS
        aws_check = confirm('Are we deploying to AWS?', default=False)

        if aws_check:
            env.user = 'ubuntu'
            if aws_check:
                env.user = 'ubuntu'
                key = prompt('Please enter the path to the AWS key pair: ')
                if key:
                    env.key_filename = key

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
                    "basename $( find {} -name 'wsgi.py' -not -path '*/.venv/*' -not -path '*/venv/*' | xargs -0 -n1 dirname )".format(
                        local_project_path
                    ), capture=True)

        with settings(warn_only=True):
            local('rsync -r -v -h{}{}/ {}@{}:/var/www/{}_media/'.format(
                ' ' if not hasattr(env, 'key_filename') else ' -e "ssh -i {}" '.format(env.key_filename),
                django_settings.MEDIA_ROOT,
                env.user,
                env.host_string,
                project_folder,
            ))
