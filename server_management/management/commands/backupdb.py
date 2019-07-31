from django.utils.timezone import now

from ._core import ServerManagementBaseCommand, load_config


def perform_backup(config, connection):
    remote = config['remotes'][config['remote_name']]

    # Dump the database on the server.
    connection.sudo('su - {user} -c \'pg_dump {name} -cOx -U {user} -f /home/{user}/{name}.sql --clean\''.format(
        name=remote['database']['name'],
        user=remote['database']['user'],
    ))

    backup_folder = '~/Backups/{}/{}'.format(
        config['local']['database']['name'],
        config['remote_name'],
    )

    # Create a backups folder
    connection.local('mkdir -p {}'.format(backup_folder))

    # Pull the SQL file down.
    connection.local('scp {} {}@{}:/home/{}/{}.sql {}/{}.sql'.format(
        f' -i {connection.connect_kwargs["key_filename"]} ' if connection.connect_kwargs.get('key_filename') else '',
        connection.user,
        connection.host,
        remote['database']['user'],
        remote['database']['name'],
        backup_folder,
        now().strftime('%Y%m%d%H%M'),
    ))

    # Delete the file on the server.
    connection.sudo('rm /home/{}/{}.sql'.format(
        remote['database']['user'],
        remote['database']['name'],
    ))


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        config, connection = load_config(options.get('remote', ''), debug=options.get('debug', False))

        perform_backup(config, connection)
