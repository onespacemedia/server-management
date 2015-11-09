from _core import load_config, ServerManagementBaseCommand

from fabric.api import *

import os


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        config, remote = load_config(env, options["remote"])

        with settings(warn_only=True):

            # Create a final dump of the database
            local('pg_dump {name} -cOx -U {user} -f ~/{name}.sql --clean'.format(
                name=config['local']['database']['name'],
                user=os.getlogin()
            ))

            # Push the database from earlier up to the server
            local('scp{}~/{}.sql {}@{}:/tmp/{}.sql'.format(
                ' ' if not getattr(env, 'key_filename') else ' -i {} '.format(env.key_filename),
                config['local']['database']['name'],
                env.user,
                remote['server']['ip'],
                remote['database']['name'],
            ))

            # Import the database file
            # sudo("su - {name} -c 'psql -q {name} < /tmp/{name}.sql > /dev/null 2>&1'".format(
            sudo("su - {user} -c 'psql -q {name} < /tmp/{name}.sql'".format(
                user=remote['database']['user'],
                name=remote['database']['name']
            ))

            # Remove the database file
            run('rm /tmp/{}.sql'.format(
                remote['database']['name']
            ))

            # Remove the SQL file from the host
            local('rm ~/{}.sql'.format(
                config['local']['database']['name']
            ))
