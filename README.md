# onespacemedia-server-management

``onespacemedia-server-management`` allows for very simple deployment and day-to-day management of Django projects.  Primarily used and maintained by the team at [Onespacemedia](http://www.onespacemedia.com/), but should work for most people.  It allows you to deploy a Django application, preferably onto a Ubuntu or Debian server, and maintain the application over time.

The commands are all wrappers around [Ansible's](/ansible/ansible) [Python API](http://docs.ansible.com/developing_api.html) with the tasks themselves ported over from real cookbooks. The calls are made using a combination of Fabric and custom code.

### Features:

* Deployment, using Ansible and Fabric.
* Pushing your local database to the remote server.
* Pushing your local media files to the remote server.
* Pulling the database from the remote server to your local machine.
* Pulling the media files from the remote server to your local machine.
* Update the remote server with the latest version of your project.

### Notes / Assumptions:

* This project currently makes the assumption that your code is hosted on BitBucket.  However, the code can be very easily updated to support generic Git hosts, pull requests are welcomed for this.
* The code takes database credentials from a file on disk, so projects using configuration data stored in environment variables are not currently supported.
* The application stack consists of Nginx, Supervisor, Gunicorn, Memcached and PostgreSQL.
* The deployment process will deploy all services onto one machine, it does not support splitting services across multiple machines, though as we're using Ansible it could be possible in the future.
* Your project is expected to use a virtual environment, with the folder in the same directory as the ``manage.py`` file, and be named ``venv`` or ``.venv``.
* The deploy script currently logs in as root and installs the base packages as root. It then creates a deploy user and disable root access. The application, PostgreSQL server and Supervisor all run under their own users.
* The deployment script does not currently support uploading HTTPS / SSL certificates or automatically configurating nginx to handle HTTPS traffic.
* Specific static and media paths are required, they are documented below.

## Installation

To install ``onespacemedia-server-management`` simply run:

    $ pip install onespacemedia-server-management

## Configuration

We need to add ``onespacemedia-server-management`` to our project, so add ``server_management`` to your ``INSTALLED_APPS``.

    INSTALLED_APPS = [
        ...
        'server_management',
    ]

Next, you need to create a ``server.json`` file which contains the information about your remote server and your database. This will live in the project folder above ``manage.py``, you can print the exact location with ``settings.SITE_ROOT``. Some example files are below:

### Single host

    {
        "local": {
            "database": {
                "name": "example_dev"
            }
        },
        "remote": {
            "server": {
                "build_system": "npm",
                "ip": "12.34.45.78"
                "deploy_user": "deploy",
            },
            "database": {
                "name": "example_prod",
                "user": "example_prod_user",
                "password": ""
            },
            "is_aws": false,
        },
        "slack": {
            "enabled": true,
            "endpoints": [
                {
                    "url": "https://hooks.slack.com/services/endpoint",
                    "channel": "#deployments",
                    "name": "Update Bot",
                    "emoji": ":computer:"
                }
            ]
        }
    }

### Multiple hosts (including AWS)

Please note that the `remote` key changes to `remotes`.

    {
        "local": {
            "database": {
                "name": "example_dev"
            }
        },
        "remotes": {
            "staging": {
                "server": {
                    "ip": "ec2-xx-xx-xx-xx.eu-west-1.compute.amazonaws.com",
                    "identity_file": "~/.ssh/server-key.pem",
                    "initial_user": "ubuntu",
                },
                "database": {
                    "password": "",
                    "name": "example_prod",
                    "user": "example_prod_user"
                }
                "is_aws": false,
            },
            "production": {
                "server": {
                    "ip": "12.34.56.78",
                    "deploy_user": "root",
                },
                "database": {
                    "password": "",
                    "name": "example_prod",
                    "user": "example_prod_user"
                }
            }
        }
    }

When running one of the management commands, you will be prompted for a remote host on which to perform the operation. To skip this prompt, specify the _name_ of the remote as a positional argument. For example, if you wanted to update the host named as `production` above, you would use `manage.py deploy production`.

The default PostgreSQL deployment uses trust authentication for connecting to the database, so a password is not usually required.

Update your ``STATIC_ROOT`` and ``MEDIA_ROOT`` to match the format the scripts expect:

    STATIC_ROOT = "/var/www/example_static"
    MEDIA_ROOT = "/var/www/example_media"

## Usage

Once ``onespacemedia-server-management`` has been added to your project you will have access to a number of ``manage.py`` commands, they are currently as follows:

* [``deploy``](#deploy)
* [``pulldb``](#pulldb)
* [``pullmedia``](#pullmedia)
* [``pushdb``](#pushdb)
* [``pushmedia``](#pushmedia)
* [``update``](#update)

### Deploy

The deploy script is the most complex command in the library, but saves many man-hours upon use.  The steps it takes are as follows:

#### On your machine
* Check if a connection can be made to the remove server using the username ``root`` and the IP specified in the ``server.json``.
* Parses the username and repo name from the current git remote.
* Requests a valid Bitbucket username and password.
* Renders template files for PostgreSQL, Gunicorn and Nginx.

#### On the remote server
* Base actions:
	* Update the apt-cache.
	* Enables unattended-upgrades
	* Installs a set of base packages via apt-get:
	    * ``build-essential``
	    * ``git``
	    * ``python-dev``
	    * ``python-pip``
	    * ``supervisor``
	    * ``libjpeg-dev``
	    * ``libffi-dev``
	    * ``npm``
	    * ``memcached``
	    * ``libgeoip-dev``
	* Installs ``bower`` with ``npm``.
	* Installs ``gulp`` with ``npm``.
	* Installs ``virtualenv`` with pip.
* PostgreSQL actions:
	* Installs PostgreSQL with the following packages:
	    * ``postgresql-9.3``
	    * ``postgresql-contrib-9.3``
	    * ``libpq-dev``
	    * ``python-psycopg2``
	    * ``pgtune``
	* Starts PostgreSQL.
	* Optimises the PostgreSQL config using ``pgtune``.
	* Creates the application database, using the settings provided in the ``server.json``
	* Creates the database user.
	* Adds the database user to the database.
	* Ensures the database user doesn't have unnecessary privileges.
* Application tasks:
	* Creates a group (named ``webapps``) for the application user.
	* Creates a user (with the name being your application name) and adds it to the ``webapps`` group.
	* Adds the server's public SSH key to the Bitbucket repository, if it's not there already.
	* Checks out the Git repository to ``/var/www/<application name>``
	* Creates the static directory at ``/var/www/<application name>_static``
	* Creates the media directory at ``/var/www/<application name>_media``
	* Creates a virtual environment in the project directory.
	* Uploads the Gunicorn file that we made earlier.
	* Creates a log file for Supervisor and Gunicorn with the correct permissions.
	* Installs the project requirements from the ``requirements.txt`` file (if you have one).
	* Installs Gunicorn into the project.
	* Runs ``collectstatic``, making symlinks into the static folder.
	* Updates the permissions of the media folder.
	* Installs ``npm`` packages.
	* Compiles CSS (using ``gulp``).
	* Creates a ``run`` folder for Supervisor.
	* Ensures the ``.venv`` folder has the correct permissions.
* Nginx tasks:
	* Installs nginx.
	* Uploads the nginx config we created earlier.
	* Removes the default nginx site.
	* Enabled the application site.
* Supervisor tasks:
	* Upload the config file we created earlier.
	* Reloads the config files and updates Supervisor (this enables the process).
* Post setup tasks:
	* Dumps the local database, uploads it and imports it.
	* Uploads the local media files to the remote server.


### PullDB
* Dumps the database on the remote server to an SQL file.
* Pulls the database file down the the local machine (using ``scp``).
* Removes the file from the remote server.
* Drops the local database (with ``dropdb``).
* Creates the local database (with ``createdb``).
* Imports the downloaded SQL file into the local database.
* Removes the downloaded file.

### PullMedia
* Ensures the media folder exists on the local machine, creating it if necessary.
* Pulls down the remote uploads folder (using ``rsync``).

### PushDB
* Dumps the database on the local machine to an SQL file.
* Uploads the database to the remote server.
* Imports the SQL file into the remote database.
* Removes the SQL file from the remote server.
* Removes the SQL file from the local machine.

### PushMedia
* Pushes up the local uploads folder to the remote server (using ``rsync``)

### Update
* Ensures the file permissions are correct on the remote server.
* Runs a ``git pull`` in the virtual environment.
* Installs the requirements from the ``requirements.txt``.
* Runs ``collectstatic`` and symlinks the files into the static directory.
* Runs database migrations.
* Restarts the Supervisor instance.
* Ensures the file permissions are still correct.
