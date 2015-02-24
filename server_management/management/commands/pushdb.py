import os
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

                project_folder = local("basename $( find {} -name 'wsgi.py' -not -path '*/.venv/*' -not -path '*/venv/*' | xargs -0 -n1 dirname )".format(
                    local_project_path
                ), capture=True)

        with settings(warn_only=True):

            # Create a final dump of the database
            local('pg_dump {name} -cOx -U {user} -f ~/{name}-final-dump.sql --clean'.format(
                name=config['local']['database']['name'],
                user=os.getlogin()
            ))

            # Push the database from earlier up to the server
            local('scp ~/{}-final-dump.sql {}@{}:/tmp/{}.sql'.format(
                config['local']['database']['name'],
                'root',
                config['remote']['server']['ip'],
                config['remote']['database']['name'],
            ))

            # Import the database file
            sudo("su - {name} -c 'psql -q {name} < /tmp/{name}.sql > /dev/null 2>&1'".format(
                name=config['remote']['database']['name']
            ))

            # Remove the database file
            run('rm /tmp/{}.sql'.format(
                config['remote']['database']['name']
            ))

            # Remove the SQL file from the host
            local('rm ~/{}-final-dump.sql'.format(
                config['local']['database']['name']
            ))
