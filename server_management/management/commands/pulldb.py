from django.core.management.base import BaseCommand

from _core import load_config

from fabric.api import *


class Command(BaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        config, remote = load_config(env)

        with settings(warn_only=True):
            # Dump the database on the server.
            sudo("su - {user} -c 'pg_dump {name} -cOx -U {user} -f /home/{user}/{name}.sql --clean'".format(
                name=remote['database']['name'],
                user=remote['database']['user'],
            ))

            # Pull the SQL file down.
            local('scp {} {}@{}:/home/{}/{}.sql ~/{}.sql'.format(
                '' if not hasattr(env, 'key_filename') else ' -i {} '.format(env.key_filename),
                env.user,
                env.host_string,
                remote['database']['user'],
                remote['database']['name'],
                config['local']['database']['name'],
            ))

            # Delete the file on the server.
            sudo('rm -f /home/{}/{}.sql'.format(
                remote['database']['user'],
                remote['database']['name'],
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
