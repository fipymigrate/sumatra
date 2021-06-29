"""
Tests using a MySQL-based record store.


Usage:
    nosetests -v test_mysql.py
or:
    python test_mysql.py
"""
from __future__ import print_function
from __future__ import unicode_literals
from future import standard_library
standard_library.install_aliases()
from builtins import input

import os
from urllib.parse import urlparse
try:
    import docker
    if "DOCKER_HOST" in os.environ:
        have_docker = True
    else:
        have_docker = False
except ImportError:
    have_docker = False
from unittest import SkipTest
import utils
from utils import setup, teardown as default_teardown, run_test, build_command

ctr = None
dkr = None
image = "mysql_test"


def create_script():
    with open(os.path.join(utils.working_dir, "main.py"), "w") as fp:
        fp.write("print('Hello world!')\n")


def get_url():
    info = dkr.containers()[0]
    assert info["Image"] == image
    host = urlparse(dkr.base_url).hostname
    return "{0}:{1}".format(host, info["Ports"][0]["PublicPort"])


def start_pg_container():
    """
    Launch a Docker container running MySQL, return the URL.

    Requires the "mysql_test" image. If this does not yet exist, run

        docker build -t mysql_test - < fixtures/Dockerfile.mysql
    """
    global ctr, dkr
    env = docker.utils.kwargs_from_env(assert_hostname=False)
    dkr = docker.Client(timeout=60, **env)

    host_config = dkr.create_host_config(publish_all_ports=True)
    # docker run -rm -P -name ms_test mysql_test
    ctr = dkr.create_container(image, command=None, hostname=None, user=None,
                               detach=False, stdin_open=False, tty=False,
                               ports=None, environment=None, dns=None,
                               volumes=None, volumes_from=None,
                               network_disabled=False, name=None,
                               entrypoint=None, cpu_shares=None,
                               working_dir=None, host_config=host_config)
    dkr.start(ctr)
    utils.env["url"] = get_url()


def teardown():
    global ctr, dkr
    default_teardown()
    if dkr is not None:
        dkr.stop(ctr)


test_steps = [
    create_script,
    ("Create repository", "git init"),
    ("Add main file", "git add main.py"),
    ("Commit main file", "git commit -m 'initial'"),
    start_pg_container,
    ("Set up a Sumatra project",
     build_command("smt init --store=mysql://docker:docker@{}/sumatra_test -m main.py -e python MyProject", "url")),
    ("Show project configuration", "smt info"),  # TODO: add assert
    ("Run a computation", "smt run")
]


# TODO: add test skips where docker not found


def test_all():
    """Test generator for Nose."""
    if not have_docker:
        raise SkipTest("Tests require docker")
    for step in test_steps:
        if callable(step):
            step()
        else:
            run_test.description = step[0]
            yield tuple([run_test] + list(step[1:]))


if __name__ == '__main__':
    # Run the tests without using Nose.
    setup()
    for step in test_steps:
        if callable(step):
            step()
        else:
            print(step[0])  # description
            run_test(*step[1:])
    response = input("Do you want to delete the temporary directory (default: yes)? ")
    if response not in ["n", "N", "no", "No"]:
        teardown()
    else:
        print("Temporary directory %s not removed" % utils.temporary_dir)
