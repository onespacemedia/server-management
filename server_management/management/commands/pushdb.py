from _core import load_config, ServerManagementBaseCommand, run_tasks

from fabric.api import *

import os


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        config, remote = load_config(env, options.get('remote', ''))

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

            # Define db tasks
            db_tasks = [
                {
                    'title': "Drop database",
                    'ansible_arguments': {
                        'module_name': 'postgresql_db',
                        'module_args': "name='{}' state=absent".format(
                                       remote['database']['name']
                        ),
                        'sudo_user': 'postgres'
                    }
                },
                {
                    'title': "Ensure database is created",
                    'ansible_arguments': {
                        'module_name': 'postgresql_db',
                        'module_args': "name='{}' encoding='UTF-8' lc_collate='en_GB.UTF-8' lc_ctype='en_GB.UTF-8' "
                                       "template='template0' state=present".format(
                                           remote['database']['name']
                                       ),
                        'sudo_user': 'postgres'
                    }
                },
                {
                    'title': "Ensure user has access to the database",
                    'ansible_arguments': {
                        'module_name': 'postgresql_user',
                        'module_args': "db='{}' name='{}' password='{}' priv=ALL state=present".format(
                            remote['database']['name'],
                            remote['database']['user'],
                            remote['database']['password']
                        ),
                        'sudo_user': 'postgres'
                    }
                },
                {
                    'title': "Ensure user does not have unnecessary privileges",
                    'ansible_arguments': {
                        'module_name': 'postgresql_user',
                        'module_args': 'name={} role_attr_flags=NOSUPERUSER,NOCREATEDB state=present'.format(
                            remote['database']['name']
                        ),
                        'sudo_user': 'postgres'
                    }
                }
            ]
            run_tasks(env, db_tasks)

            # Import the database file
            # sudo("su - {name} -c 'psql -q {name} < /tmp/{name}.sql > /dev/null 2>&1'".format(

            with settings(sudo_user=remote['database']['user']):
                sudo('psql {name} < /tmp/{name}.sql'.format(
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
