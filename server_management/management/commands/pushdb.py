from django.core.management.base import BaseCommand

from _core import load_config

from fabric.api import *

import os


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

        with settings(warn_only=True):

            # Create a final dump of the database
            local('pg_dump {name} -cOx -U {user} -f ~/{name}.sql --clean'.format(
                name=config['local']['database']['name'],
                user=os.getlogin()
            ))

            # Push the database from earlier up to the server
            local('scp ~/{}.sql {}@{}:/tmp/{}.sql'.format(
                config['local']['database']['name'],
                env.user,
                config['remote']['server']['ip'],
                config['remote']['database']['name'],
            ))

            # Import the database file
            # sudo("su - {name} -c 'psql -q {name} < /tmp/{name}.sql > /dev/null 2>&1'".format(
            sudo("su - {name} -c 'psql -q {name} < /tmp/{name}.sql'".format(
                name=config['remote']['database']['name']
            ))

            # Remove the database file
            run('rm /tmp/{}.sql'.format(
                config['remote']['database']['name']
            ))

            # Remove the SQL file from the host
            local('rm ~/{}.sql'.format(
                config['local']['database']['name']
            ))
