# from django.conf import settings as django_settings
from django.core.management.base import BaseCommand

from _core import load_config

from fabric.api import *


class Command(BaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        config, remote = load_config(env)

        # Define current host from settings in server config
        env.host_string = remote['server']['ip']
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
                name=remote['database']['name'],
                user=remote['database']['user'],
            ))

            # Create a backups folder
            local('mkdir -p ~/Backups/')

            # Pull the SQL file down.
            local('scp {}@{}:/home/{}/{}.sql ~/backups/{}.sql'.format(
                'root',
                env.host_string,
                remote['database']['user'],
                remote['database']['name'],
                config['local']['database']['name'],
            ))

            # Delete the file on the server.
            run('rm /home/{}/{}.sql'.format(
                remote['database']['user'],
                remote['database']['name'],
            ))
