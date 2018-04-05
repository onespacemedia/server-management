from django.utils.timezone import now
from fabric.api import local, settings, sudo

from ._core import ServerManagementBaseCommand, load_config


def perform_backup(env, config, remote):
    with settings(warn_only=True):
        # Dump the database on the server.
        sudo('su - {user} -c \'pg_dump {name} -cOx -U {user} -f /home/{user}/{name}.sql --clean\''.format(
            name=remote['database']['name'],
            user=remote['database']['user'],
        ))

        backup_folder = '~/Backups/{}/{}'.format(
            config['local']['database']['name'],
            config['remote_name'],
        )

        # Create a backups folder
        local('mkdir -p {}'.format(backup_folder))

        # Pull the SQL file down.
        local('scp {} {}@{}:/home/{}/{}.sql {}/{}.sql'.format(
            '' if not getattr(env, 'key_filename') else ' -i {} '.format(env.key_filename),
            env.user,
            env.host_string,
            remote['database']['user'],
            remote['database']['name'],
            backup_folder,
            now().strftime('%Y%m%d%H%M'),
        ))

        # Delete the file on the server.
        sudo('rm /home/{}/{}.sql'.format(
            remote['database']['user'],
            remote['database']['name'],
        ))


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        from fabric.api import env

        # Load server config from project
        config, remote = load_config(env, options.get('remote', ''), debug=options.get('debug', False))

        perform_backup(env, config, remote)
