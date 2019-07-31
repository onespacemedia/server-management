import os

from django.utils.timezone import now

from ._core import ServerManagementBaseCommand, load_config


def perform_backup(config, connection):
    remote = config['remotes'][config['remote_name']]

    # Dump the database on the server.
    connection.sudo('su - {user} -c \'pg_dump {name} -cOx -U {user} -f /{path} --clean\''.format(
        name=remote['database']['name'],
        user=remote['database']['user'],
        path=os.path.join(
            'home',
            remote['database']['user'],
            f'{remote["database"]["name"]}.sql'
        )
    ))

    backup_folder = '~/Backups/{}/{}'.format(
        config['local']['database']['name'],
        config['remote_name'],
    )

    # Create a backups folder
    connection.local('mkdir -p {}'.format(backup_folder))

    # Pull the SQL file down.
    connection.local('scp{}{}@{}:/{} {}'.format(
        f' -i {connection.connect_kwargs["key_filename"]} ' if connection.connect_kwargs.get('key_filename') else ' ',
        connection.user,
        connection.host,
        os.path.join(
            'home',
            remote['database']['user'],
            f'{remote["database"]["name"]}.sql'
        ),
        os.path.join(
            backup_folder,
            f'{now().strftime("%Y%m%d%H%M")}.sql'
        )
    ))

    # Delete the file on the server.
    connection.sudo('rm /{}'.format(
        os.path.join(
            'home',
            remote['database']['user'],
            f'{remote["database"]["name"]}.sql'
        )
    ))


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        config, connection = load_config(options.get('remote', ''), debug=options.get('debug', False))

        perform_backup(config, connection)
