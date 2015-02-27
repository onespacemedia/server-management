# from django.conf import settings as django_settings
from django.core.management.base import BaseCommand

from _core import load_config

from fabric.api import *


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

        with settings(warn_only=True):
            # Dump the database on the server.
            sudo("su - {user} -c 'pg_dump {name} -cOx -U {user} -f /home/{name}/{name}.sql --clean'".format(
                name=config['remote']['database']['name'],
                user=config['remote']['database']['user'],
            ))

            # Pull the SQL file down.
            local('scp {}@{}:/home/{}/{}.sql ~/{}.sql'.format(
                'root',
                env.host_string,
                config['remote']['database']['user'],
                config['remote']['database']['name'],
                config['local']['database']['name'],
            ))

            # Delete the file on the server.
            run('rm /home/{}/{}.sql'.format(
                config['remote']['database']['user'],
                config['remote']['database']['name'],
            ))

            # Drop the local db
            local('dropdb {}'.format(
                config['local']['database']['name']
            ))

            # Create a new db, this is an easy way to start fresh
            local('createdb {}'.format(
                config['local']['database']['name']
            ))

            # Import the database locally
            local('psql -q {name} < ~/{name}.sql > /dev/null 2>&1'.format(
                name=config['local']['database']['name'],
            ))

            # Cleanup local files
            local('rm ~/{}.sql'.format(
                config['local']['database']['name'],
            ))
