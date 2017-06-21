from django.utils.timezone import now
from fabric.api import env, local, settings, sudo

from ._core import load_config, ServerManagementBaseCommand


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        config, remote = load_config(env, options.get('remote', ''))

        with settings(warn_only=True):
            # Dump the database on the server.
            sudo('su - {user} -c \'pg_dump {name} -cOx -U {user} -f /home/{user}/{name}.sql --clean\''.format(
                name=remote['database']['name'],
                user=remote['database']['user'],
            ))

            # Create a backups folder
            local('mkdir -p ~/Backups/')

            # Pull the SQL file down.
            local('scp {} {}@{}:/home/{}/{}.sql ~/Backups/{}-{}.sql'.format(
                '' if not getattr(env, 'key_filename') else ' -i {} '.format(env.key_filename),
                env.user,
                env.host_string,
                remote['database']['user'],
                remote['database']['name'],
                config['local']['database']['name'],
                now().isoformat(),
            ))

            # Delete the file on the server.
            sudo('rm /home/{}/{}.sql'.format(
                remote['database']['user'],
                remote['database']['name'],
            ))
