from invoke import run as local
from invoke import UnexpectedExit

from ._core import ServerManagementBaseCommand, load_config


class Command(ServerManagementBaseCommand):
    def handle(self, *args, **options):
        # Load server config from project
        config, connection = load_config(options.get('remote', ''), debug=options.get('debug', False))
        remote = config['remotes'][config['remote_name']]


        # Dump the database on the server.
        connection.sudo("su - {user} -c 'pg_dump {name} -cOx -U {user} -f /home/{user}/{name}.sql --clean'".format(
            name=remote['database']['name'],
            user=remote['database']['user'],
        ))

        # Pull the SQL file down.
        connection.local('scp{} {}@{}:/home/{}/{}.sql ~/{}.sql'.format(
            f' -i {connection.connect_kwargs["key_filename"]}' if connection.connect_kwargs.get('key_filename') else '',
            connection.user,
            connection.host,
            remote['database']['user'],
            remote['database']['name'],
            config['local']['database']['name'],
        ))

        # Delete the file on the server.
        connection.sudo('rm -f /home/{}/{}.sql'.format(
            remote['database']['user'],
            remote['database']['name'],
        ))

        # Drop the local db
        try:
            # We use invoke's run() here (which we've imported as local() for readability) to maintain the PATH envvar so we have access to PSQL commands
            local('dropdb {}'.format(
                config['local']['database']['name']
            ))
        except UnexpectedExit as e:
            if not f'''database "{config['local']['database']['name']}" does not exist''' in e.result.stderr:
                raise e

        # Create a new db, this is an easy way to start fresh
        local('createdb {}'.format(
            config['local']['database']['name']
        ))

        # Import the database locally
        local('psql -q {name} < ~/{name}.sql > /dev/null 2>&1'.format(
            name=config['local']['database']['name'],
        ))

        # Cleanup local files
        connection.local('rm ~/{}.sql'.format(
            config['local']['database']['name'],
        ))
