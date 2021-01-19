"""
Commands provided by the smt tool.

Each command corresponds to a function in this module.


:copyright: Copyright 2006-2015 by the Sumatra team, see doc/authors.txt
:license: BSD 2-clause, see LICENSE for details.
"""
from __future__ import print_function
from __future__ import unicode_literals
from builtins import str

import os.path
import sys
from argparse import ArgumentParser
from textwrap import dedent
import warnings
import logging
import datetime
import sumatra

from sumatra.programs import get_executable
from sumatra.datastore import get_data_store
from sumatra.projects import Project, load_project
from sumatra.launch import get_launch_mode
from sumatra.parameters import build_parameters
from sumatra.recordstore import get_record_store
from sumatra.versioncontrol import get_working_copy, get_repository, UncommittedModificationsError
from sumatra.formatting import get_diff_formatter, get_formatter
from sumatra.records import MissingInformationError
from sumatra.core import TIMESTAMP_FORMAT, STATUS_FORMAT, STATUS_PATTERN

logger = logging.getLogger("Sumatra")
logger.setLevel(logging.CRITICAL)
h = logging.StreamHandler()
h.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(h)

logger.debug("STARTING")

modes = ("init", "configure", "info", "run", "list", "delete", "comment", "tag",
         "repeat", "diff", "help", "export", "upgrade", "sync", "migrate", "version",
         "view")

store_arg_help = "The argument can take the following forms: (1) `/path/to/sqlitedb` - DjangoRecordStore is used with the specified Sqlite database, (2) `http[s]://location` - remote HTTPRecordStore is used with a remote Sumatra server, (3) `postgres://username:password@hostname/databasename` - DjangoRecordStore is used with specified Postgres database."

## recommended method for modifying warning formatting
## see https://docs.python.org/2/library/warnings.html#warnings.showwarning
def _warning(
        message,
        category = UserWarning,
        filename = '',
        lineno = -1,
        file=None,
        line=None):
    print("Warning: ")
    print(message)
warnings.showwarning = _warning


def _convertStr(s):
    """Convert string to either int or float or not both."""
    try:
        ret = int(s)
    except ValueError:
        try:
            ret = float(s)
        except ValueError:
            ret = s
    return ret


def parse_executable_str(exec_str):
    """
    Split the string describing the executable into a path part and an
    options part.
    """
    first_space = exec_str.find(" ")
    if first_space == -1:
        first_space = len(exec_str)
    return exec_str[:first_space], exec_str[first_space:]

def parse_arguments(args, input_datastore, stdin=None, stdout=None,
                    allow_command_line_parameters=True):
    cmdline_parameters = []
    script_args = []
    parameter_sets = []
    input_data = []
    for arg in args:
        have_parameters = False
        if os.path.isfile(arg):  # could be a parameter file or a data file
            parameters = build_parameters(arg)
            if parameters is not None:
                parameter_sets.append(parameters)
                script_args.append("<parameters>")
                have_parameters = True
        if not have_parameters:
            if arg[0] == "/":
                path = arg
            else:
                path = os.path.relpath(arg, input_datastore.root)
            if input_datastore.contains_path(path):
                data_key = input_datastore.generate_keys(path)
                input_data.extend(data_key)
                script_args.append(arg)
            elif allow_command_line_parameters and "=" in arg:  # cmdline parameter
                cmdline_parameters.append(arg)
            else:  # a flag or something, passed on unchanged
                script_args.append(arg)
    if stdin:
        script_args.append("< %s" % stdin)
        if input_datastore.contains_path(stdin):
            data_key = input_datastore.generate_keys(stdin)
            input_data.extend(data_key)
        else:
            raise IOError("File does not exist: %s" % stdin)
    if stdout:
        script_args.append("> %s" % stdout)
    assert len(parameter_sets) < 2, "No more than one parameter file may be supplied."  # temporary restriction
    if cmdline_parameters:
        if parameter_sets:
            ps = parameter_sets[0]
            for cl in cmdline_parameters:
                try:
                    ps.update(ps.parse_command_line_parameter(cl))
                except ValueError as v:
                    message, name, value = v.args
                    warnings.warn(message)
                    warnings.warn("'{0}={1}' not defined in the parameter file".format(name, value))
                    ps.update({name: value}) ## for now, add the command line param anyway
        else:
            raise Exception("Command-line parameters supplied but without a parameter file to put them into.")
            # ought really to have a more specific Exception and to catch it so as to give a helpful error message to user
    return parameter_sets, input_data, " ".join(script_args)


def init(argv):
    """Create a new project in the current directory."""
    usage = "%(prog)s init [options] NAME"
    description = "Create a new project called NAME in the current directory."
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('project_name', metavar='NAME', help="a short name for the project; should not contain spaces.")
    parser.add_argument('-d', '--datapath', metavar='PATH', default='./Data', help="set the path to the directory in which smt will search for output datafiles generated by the simulation/analysis. Defaults to %(default)s.")
    parser.add_argument('-i', '--input', metavar='PATH', default='.', help="set the path to the directory relative to which input datafile paths will be given. Defaults to the filesystem root.")
    parser.add_argument('-l', '--addlabel', choices=['cmdline', 'parameters', None], metavar='OPTION',
                        default=None, help="If this option is set, smt will append the record label either to the command line (option 'cmdline') or to the parameter file (option 'parameters'), and will add the label to the datapath when searching for datafiles. It is up to the user to make use of this label inside their program to ensure files are created in the appropriate location.")
    parser.add_argument('-e', '--executable', metavar='PATH', help="set the path to the executable. If this is not set, smt will try to infer the executable from the value of the --main option, if supplied, and will try to find the executable from the PATH environment variable, then by searching various likely locations on the filesystem.")
    parser.add_argument('-r', '--repository', help="the URL of a Subversion or Mercurial repository containing the code. This will be checked out/cloned into the current directory.")
    parser.add_argument('-m', '--main', help="the name of the script that would be supplied on the command line if running the simulation or analysis normally, e.g. init.hoc.")
    parser.add_argument('-c', '--on-changed', default='error', help="the action to take if the code in the repository or any of the depdendencies has changed. Defaults to %(default)s")  # need to add list of allowed values
    parser.add_argument('-s', '--store', help="Specify the path, URL or URI to the record store (must be specified). This can either be an existing record store or one to be created. {0} Not using the `--store` argument defaults to a DjangoRecordStore with Sqlite in `.smt/records`".format(store_arg_help))
    parser.add_argument('-g', '--labelgenerator', choices=['timestamp', 'uuid'], default='timestamp', metavar='OPTION', help="specify which method Sumatra should use to generate labels (options: timestamp, uuid)")
    parser.add_argument('-t', '--timestamp_format', help="the timestamp format given to strftime", default=TIMESTAMP_FORMAT)
    parser.add_argument('-L', '--launch_mode', choices=['serial', 'distributed', 'slurm-mpi'], default='serial', help="how computations should be launched. Defaults to %(default)s")
    parser.add_argument('-o', '--launch_mode_options', help="extra options for the given launch mode")

    datastore = parser.add_mutually_exclusive_group()
    datastore.add_argument('-W', '--webdav', metavar='URL', help="specify a webdav URL (with username@password: if needed) as the archiving location for data")
    datastore.add_argument('-A', '--archive', metavar='PATH', help="specify a directory in which to archive output datafiles. If not specified, or if 'false', datafiles are not archived.")
    datastore.add_argument('-M', '--mirror', metavar='URL', help="specify a URL at which your datafiles will be mirrored.")

    args = parser.parse_args(argv)

    try:
        project = load_project()
        parser.error("A project already exists in directory '{0}'.".format(project.path))
    except Exception:
        pass

    if not os.path.exists(".smt"):
        os.mkdir(".smt")

    if args.repository:
        repository = get_repository(args.repository)
        repository.checkout()
    else:
        repository = get_working_copy().repository  # if no repository is specified, we assume there is a working copy in the current directory.

    if args.executable:
        executable_path, executable_options = parse_executable_str(args.executable)
        executable = get_executable(path=executable_path)
        executable.args = executable_options
    elif args.main:
        try:
            executable = get_executable(script_file=args.main)
        except Exception:  # assume unrecognized extension - really need more specific exception type
            # should warn that extension unrecognized
            executable = None
    else:
        executable = None
    if args.store:
        record_store = get_record_store(args.store)
    else:
        record_store = 'default'

    if args.webdav:
        # should we care about archive migration??
        output_datastore = get_data_store("DavFsDataStore", {"root": args.datapath, "dav_url": args.webdav})
        args.archive = '.smt/archive'
    elif args.archive and args.archive.lower() != 'false':
        if args.archive.lower() == "true":
            args.archive = ".smt/archive"
        args.archive = os.path.abspath(args.archive)
        output_datastore = get_data_store("ArchivingFileSystemDataStore", {"root": args.datapath, "archive": args.archive})
    elif args.mirror:
        output_datastore = get_data_store("MirroredFileSystemDataStore", {"root": args.datapath, "mirror_base_url": args.mirror})
    else:
        output_datastore = get_data_store("FileSystemDataStore", {"root": args.datapath})
    input_datastore = get_data_store("FileSystemDataStore", {"root": args.input})

    if args.launch_mode_options:
        args.launch_mode_options = args.launch_mode_options.strip()
    launch_mode = get_launch_mode(args.launch_mode)(options=args.launch_mode_options)

    project = Project(name=args.project_name,
                      default_executable=executable,
                      default_repository=repository,
                      default_main_file=args.main,  # what if incompatible with executable?
                      default_launch_mode=launch_mode,
                      data_store=output_datastore,
                      record_store=record_store,
                      on_changed=args.on_changed,
                      data_label=args.addlabel,
                      input_datastore=input_datastore,
                      label_generator=args.labelgenerator,
                      timestamp_format=args.timestamp_format)
    if os.path.exists('.smt') and project.record_store.has_project(project.name):
        with open('.smt/labels', 'w') as f:
            f.write('\n'.join(project.get_labels()))
    project.save()


def configure(argv):
    """Modify the settings for the current project."""
    usage = "%(prog)s configure [options]"
    description = "Modify the settings for the current project."
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('-d', '--datapath', metavar='PATH', help="set the path to the directory in which smt will search for datafiles generated by the simulation or analysis.")
    parser.add_argument('-i', '--input', metavar='PATH', default=None, help="set the path to the directory in which smt will search for input datafiles.")
    parser.add_argument('-l', '--addlabel', choices=['cmdline', 'parameters', 'none'], metavar='OPTION',
                        help="If this option is set, smt will append the record label either to the command line (option 'cmdline') or to the parameter file (option 'parameters'), and will add the label to the datapath when searching for datafiles. It is up to the user to make use of this label inside their program to ensure files are created in the appropriate location.")
    parser.add_argument('-e', '--executable', metavar='PATH', help="set the path to the executable.")
    parser.add_argument('-r', '--repository', help="the URL of a Subversion or Mercurial repository containing the code. This will be checked out/cloned into the current directory.")
    parser.add_argument('-m', '--main', help="the name of the script that would be supplied on the command line if running the simulator normally, e.g. init.hoc.")
    parser.add_argument('-c', '--on-changed', help="may be 'store-diff' or 'error': the action to take if the code in the repository or any of the dependencies has changed.", choices=['store-diff', 'error'])
    parser.add_argument('-g', '--labelgenerator', choices=['timestamp', 'uuid'], metavar='OPTION', help="specify which method Sumatra should use to generate labels (options: timestamp, uuid)")
    parser.add_argument('-t', '--timestamp_format', help="the timestamp format given to strftime")
    parser.add_argument('-L', '--launch_mode', choices=['serial', 'distributed', 'slurm-mpi'], help="how computations should be launched.")
    parser.add_argument('-o', '--launch_mode_options', help="extra options for the given launch mode, to be given in quotes with a leading space, e.g. ' --foo=3'")
    parser.add_argument('-p', '--plain', dest='plain', action='store_true', help="pass arguments to the 'run' command straight through to the program. Otherwise arguments of the form name=value can be used to overwrite default parameter values.")
    parser.add_argument('--no-plain', dest='plain', action='store_false', help="arguments to the 'run' command of the form name=value will overwrite default parameter values. This is the opposite of the --plain option.")
    parser.add_argument('-s', '--store', help="Change the record store to the specified path, URL or URI (must be specified). {0}".format(store_arg_help))

    datastore = parser.add_mutually_exclusive_group()
    datastore.add_argument('-W', '--webdav', metavar='URL', help="specify a webdav URL (with username@password: if needed) as the archiving location for data")
    datastore.add_argument('-A', '--archive', metavar='PATH', help="specify a directory in which to archive output datafiles. If not specified, or if 'false', datafiles are not archived.")
    datastore.add_argument('-M', '--mirror', metavar='URL', help="specify a URL at which your datafiles will be mirrored.")

    parser.add_argument('--add-plugin', help="name of a Python module containing one or more plug-ins.")
    parser.add_argument('--remove-plugin', help="name of a plug-in module to remove from the project.")

    args = parser.parse_args(argv)

    project = load_project()
    if args.store:
        new_store = get_record_store(args.store)
        project.change_record_store(new_store)
    if args.datapath:
        project.data_store.root = args.datapath
    if args.archive:
        if args.archive.lower() == "true":
            args.archive = ".smt/archive"
        if hasattr(project.data_store, 'archive_store'):  # current data store is archiving
            if args.archive.lower() == 'false':
                project.data_store = get_data_store("FileSystemDataStore",
                                                    {"root": project.data_store.root})
            else:
                project.data_store.archive_store = args.archive
        else:  # current data store is not archiving
            if args.archive.lower() != 'false':
                project.data_store = get_data_store("ArchivingFileSystemDataStore",
                                                    {"root": project.data_store.root, "archive": args.archive})
    elif args.mirror:
        project.data_store = get_data_store("MirroredFileSystemDataStore",
                                            {"root": project.data_store.root, "mirror_base_url": args.mirror})
    elif args.webdav:
        # should we care about archive migration??
        project.data_store = get_data_store("DavFsDataStore",
                                            {"root": project.data_store.root, "dav_url": args.webdav})
        project.data_store.archive_store = '.smt/archive'
    if args.input:
        project.input_datastore.root = args.input
    if args.repository:
        repository = get_repository(args.repository)
        repository.checkout()
        project.default_repository = repository
    if args.main:
        project.default_main_file = args.main
    if args.executable:
        executable_path, executable_options = parse_executable_str(args.executable)
        project.default_executable = get_executable(executable_path,
                                                    script_file=args.main or project.default_main_file)
        project.default_executable.options = executable_options

    if args.on_changed:
        project.on_changed = args.on_changed
    if args.addlabel:
        if args.addlabel in ('none', 'None', 'NONE'):
            project.data_label = None
        else:
            project.data_label = args.addlabel
    if args.labelgenerator:
        project.label_generator = args.labelgenerator
    if args.timestamp_format:
        project.timestamp_format = args.timestamp_format
    if args.launch_mode:
        project.default_launch_mode = get_launch_mode(args.launch_mode)()
    if args.launch_mode_options:
        project.default_launch_mode.options = args.launch_mode_options.strip()
    if args.plain is not None:
        project.allow_command_line_parameters = not args.plain
    if args.add_plugin:
        project.load_plugins(args.add_plugin)
    if args.remove_plugin:
        project.remove_plugins(args.remove_plugin)
    project.save()


def info(argv):
    """Print information about the current project."""
    usage = "%(prog)s info"
    description = "Print information about the current project."
    parser = ArgumentParser(usage=usage,
                            description=description)
    args = parser.parse_args(argv)
    try:
        project = load_project()
    except IOError as err:
        print(err)
        sys.exit(1)
    print(project.info())


def run(argv):
    """Run a simulation or analysis."""
    usage = "%(prog)s run [options] [arg1, ...] [param=value, ...]"
    description = dedent("""\
      The list of arguments will be passed on to the simulation/analysis script.
      It should normally contain at least the name of a parameter file, but
      can also contain input files, flags, etc.

      If the parameter file should be in a format that Sumatra understands (see
      documentation), then the parameters will be stored to allow future
      searching, comparison, etc. of records.

      For convenience, it is possible to specify a file with default parameters
      and then specify those parameters that are different from the default values
      on the command line with any number of param=value pairs (note no space
      around the equals sign).""")
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('-v', '--version', metavar='REV',
                        help="use version REV of the code (if this is not the same as the working copy, it will be checked out of the repository). If this option is not specified, the most recent version in the repository will be used. If there are changes in the working copy, the user will be prompted to commit them first")
    parser.add_argument('-l', '--label', help="specify a label for the experiment. If no label is specified, one will be generated automatically.")
    parser.add_argument('-r', '--reason', help="explain the reason for running this simulation/analysis.")
    parser.add_argument('-e', '--executable', metavar='PATH', help="Use this executable for this run. If not specified, the project's default executable will be used.")
    parser.add_argument('-m', '--main', help="the name of the script that would be supplied on the command line if running the simulation/analysis normally, e.g. init.hoc. If not specified, the project's default will be used.")
    parser.add_argument('-n', '--num_processes', metavar='N', type=int,
                        help="run a distributed computation on N processes using MPI. If this option is not used, or if N=0, a normal, serial simulation/analysis is run.")
    parser.add_argument('-t', '--tag', help="tag you want to add to the project")
    parser.add_argument('-D', '--debug', action='store_true', help="print debugging information.")
    parser.add_argument('-i', '--stdin', help="specify the name of a file that should be connected to standard input.")
    parser.add_argument('-o', '--stdout', help="specify the name of a file that should be connected to standard output.")

    args, user_args = parser.parse_known_args(argv)
    user_args = [str(arg) for arg in user_args]  # unifying types for Py2/Py3

    if args.debug:
        logger.setLevel(logging.DEBUG)

    project = load_project()
    parameters, input_data, script_args = parse_arguments(user_args,
                                                          project.input_datastore,
                                                          args.stdin,
                                                          args.stdout,
                                                          project.allow_command_line_parameters)
    if len(parameters) == 0:
        parameters = {}
    elif len(parameters) == 1:
        parameters = parameters[0]
    else:
        parser.error("Only a single parameter file allowed.")  # for now

    if args.executable:
        executable_path, executable_options = parse_executable_str(args.executable)
        executable = get_executable(path=executable_path)
        executable.options = executable_options
    elif args.main:
        executable = get_executable(script_file=args.main)  # should we take the options from project.default_executable, if they match?
    else:
        executable = 'default'
    if args.num_processes:
        if hasattr(project.default_launch_mode, 'n'):
            project.default_launch_mode.n = args.num_processes
        else:
            parser.error("Your current launch mode does not support using multiple processes.")
    reason = args.reason or ''
    if reason:
        reason = reason.strip('\'"')

    label = args.label
    try:
        run_label = project.launch(parameters, input_data, script_args,
                                   label=label, reason=reason,
                                   executable=executable,
                                   main_file=args.main or 'default',
                                   version=args.version or 'current')
    except (UncommittedModificationsError, MissingInformationError) as err:
        print(err)
        sys.exit(1)
    if args.tag:
        project.add_tag(run_label, args.tag)
    if os.path.exists('.smt'):
        with open('.smt/labels', 'w') as f:
            f.write('\n'.join(project.get_labels()))


def list(argv):  # add 'report' and 'log' as aliases
    """List records belonging to the current project."""
    usage = "%(prog)s list [options] [TAGS]"
    description = dedent("""\
      If TAGS (optional) is specified, then only records tagged with all the tags in TAGS
      will be listed.""")
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('tags', metavar='TAGS', nargs='*')
    parser.add_argument('-l', '--long', action="store_const", const="long",
                        dest="mode", default="short",
                        help="prints full information for each record"),
    parser.add_argument('-T', '--table', action="store_const", const="table",
                        dest="mode", help="prints information in tab-separated columns")
    parser.add_argument('-f', '--format', metavar='FMT', choices=['text', 'html', 'latex', 'shell', 'json'],
                        default='text',
                        help="FMT can be 'text' (default), 'html', 'json', 'latex' or 'shell'.")
    parser.add_argument('-r', '--reverse', action="store_true", dest="reverse", default=False,
                        help="list records in reverse order (default: newest first)")
    parser.add_argument('-m', '--main_file', help="filter list of records by main file")
    parser.add_argument('-O', '--output_table', action="store_const", const="output_table",
                        dest="mode", help="list output files from records.")
    parser.add_argument('-P', '--parameter_table', action="store_const", const="parameter_table",
                        dest="mode", help="list records with parameter values")
    parser.add_argument('-p', '--parameters', metavar='parameters', default=None, help="filter records by parameter values, separated by comma")
    parser.add_argument('-d', '--date', dest='timestamp', help="filter records by the date (today or YYYYMMDD)")
    args = parser.parse_args(argv)

    project = load_project()
    if os.path.exists('.smt'):
        with open('.smt/labels', 'w') as f:
            f.write('\n'.join(project.get_labels()))

    kwargs = {'tags':args.tags, 'mode':args.mode, 'format':args.format, 'reverse':args.reverse}

    if args.timestamp:
        if args.timestamp == 'today':
            date = datetime.datetime.today().date()
        else:
            date = datetime.datetime.strptime(args.timestamp, '%Y%m%d')
        kwargs['timestamp__range'] = [date, date+datetime.timedelta(1)]
    if args.main_file is not None: kwargs['main_file__startswith'] = args.main_file
    if args.parameters:
        parameters = {}
        for pp in args.parameters.split(','):
            for operator in ['=',':']:
                if operator in pp:
                    pkey,pval = pp.split(operator)
                    parameters.update({pkey:_convertStr(pval)})
        kwargs.update({'parameters':parameters})
    print(project.format_records(**kwargs))


def delete(argv):
    """Delete records or records with a particular tag from a project."""
    usage = "%(prog)s delete [options] LIST"
    description = dedent("""\
      LIST should be a space-separated list of labels for individual records or
      of tags. If it contains tags, you must set the --tag/-t option (see below).
      The special value "last" allows you to delete the most recent simulation/analysis.
      If you want to delete all records, just delete the .smt directory and use
      smt init to create a new, empty project.""")
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('labels', metavar='LIST', nargs="+", help="a space-separated list of labels for individual records or of tags")
    parser.add_argument('-t', '--tag', action='store_true',
                        help="interpret LIST as containing tags. Records with any of these tags will be deleted.")
    parser.add_argument('-d', '--data', action='store_true',
                        help="also delete any data associated with the record(s).")
    args = parser.parse_args(argv)

    project = load_project()

    if args.tag:
        for tag in args.labels:
            n = project.delete_by_tag(tag, delete_data=args.data)
            print("%s records deleted." % n)
    else:
        for label in args.labels:
            if label == 'last':
                label = project.most_recent().label
            try:
                project.delete_record(label, delete_data=args.data)
            except Exception:  # could be KeyError or DoesNotExist: should create standard NoSuchRecord or RecordDoesNotExist exception
                warnings.warn("Could not delete record '%s' because it does not exist" % label)
    if os.path.exists('.smt'):
        with open('.smt/labels', 'w') as f:
            f.write('\n'.join(project.get_labels()))

def comment(argv):
    """Add a comment to an existing record."""
    usage = "%(prog)s comment [options] [LABEL] COMMENT"
    description = dedent("""\
      This command is used to describe the outcome of the simulation/analysis.
      If LABEL is omitted, the comment will be added to the most recent experiment.
      If the '-f/--file' option is set, COMMENT should be the name of a file
      containing the comment, otherwise it should be a string of text.
      By default, comments will be appended to any existing comments.
      To overwrite existing comments, use the '-r/--replace flag.""")
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('label', nargs='?', metavar='LABEL', help="the record to which the comment will be added")
    parser.add_argument('comment', help="a string of text, or the name of a file containing the comment.")
    parser.add_argument('-r', '--replace', action='store_true',
                        help="if this flag is set, any existing comment will be overwritten, otherwise, the new comment will be appended to the end, starting on a new line")
    parser.add_argument('-f', '--file', action='store_true',
                        help="interpret COMMENT as the path to a file containing the comment")
    args = parser.parse_args(argv)

    if args.file:
        f = open(args.comment, 'r')
        comment = f.read()
        f.close()
    else:
        comment = args.comment

    project = load_project()
    label = args.label or project.most_recent().label
    project.add_comment(label, comment, replace=args.replace)


def tag(argv):
    """Tag, or remove a tag, from a record or records."""
    usage = "%(prog)s tag [options] TAG [LIST]"
    statuses = ('initialized', 'pre_run', 'running', 'finished', 'failed',
                'killed', 'succeeded', 'crashed')
    formatted_statuses = ", ".join((STATUS_FORMAT % s for s in statuses))
    description = dedent("""\
      If TAG contains spaces, it must be enclosed in quotes. LIST should be a
      space-separated list of labels for individual records. If it is omitted,
      only the most recent record will be tagged. If the '-r/--remove' option
      is set, the tag will be removed from the records. TAG can be a status
      from the mutually exclusive list:  %s. """ % formatted_statuses)
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('tag', metavar='TAG', help="tag to add")
    parser.add_argument('labels', metavar='LIST', nargs='*', help="a space-separated list of records to be tagged")
    parser.add_argument('-r', '--remove', action='store_true',
                        help="remove the tag from the record(s), rather than adding it.")
    args = parser.parse_args(argv)

    m = STATUS_PATTERN.match(args.tag)
    if m:
        tag = m.group(1).lower()
        if tag not in statuses:
            raise ValueError("TAG should be one of %s" % formatted_statuses)

    project = load_project()
    if args.remove:
        op = project.remove_tag
    else:
        op = project.add_tag
    labels = args.labels or [project.most_recent().label]
    for label in labels:
        op(label, args.tag)


def repeat(argv):
    """Re-run a previous simulation or analysis."""
    usage = "%(prog)s repeat LABEL"
    description = dedent("""\
        Re-run a previous simulation/analysis under (in theory) identical
        conditions, and check that the results are unchanged.""")
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('original_label', metavar='LABEL', help='label of record to be repeated')
    parser.add_argument('-l', '--label', metavar='NEW_LABEL', help="specify a label for the new experiment. If no label is specified, one will be generated automatically.")

    args = parser.parse_args(argv)
    original_label = args.original_label
    project = load_project()
    new_label, original_label = project.repeat(original_label, args.label)
    diff = project.compare(original_label, new_label)
    if diff:
        formatter = get_diff_formatter()(diff)
        msg = ["The new record does not match the original. It differs as follows.",
               formatter.format('short'),
               "run smt diff --long %s %s to see the differences in detail." % (original_label, new_label)]
        msg = "\n".join(msg)
    else:
        msg = "The new record exactly matches the original."
    print(msg)
    project.add_comment(new_label, msg)


def diff(argv):
    """Show the differences, if any, between two records."""
    usage = "%(prog)s diff [options] LABEL1 LABEL2"
    description = dedent("Show the differences, if any, between two records.")
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('label1')
    parser.add_argument('label2')
    parser.add_argument('-i', '--ignore', action="append",
                        help="a regular expression pattern for filenames to ignore when evaluating differences in output data. To supply multiple patterns, use the -i option multiple times.")
    parser.add_argument('-l', '--long', action="store_const", const="long",
                        dest="mode", default="short",
                        help="prints full information for each record"),
    args = parser.parse_args(argv)
    if args.ignore is None:
        args.ignore = []

    project = load_project()
    print(project.show_diff(args.label1, args.label2, mode=args.mode,
                            ignore_filenames=args.ignore))


def help(argv):
    usage = "%(prog)s help CMD"
    description = dedent("""Get help on an %(prog)s command.""")
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('cmd', nargs='?')
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.error('Please specify a command on which you would like help.\n\nAvailable commands:\n  ' + "\n  ".join(modes))
    else:
        try:
            func = globals()[args.cmd]
            func(['--help'])
        except KeyError:
            parser.error('"%s" is not an smt command.' % args.cmd)


def upgrade(argv):
    usage = "%(prog)s upgrade"
    description = dedent("""\
        Upgrade an existing Sumatra project. You must have previously run
        "smt export" or the standalone 'export.py' script.""")
    parser = ArgumentParser(usage=usage,
                            description=description)
    args = parser.parse_args(argv)

    project = load_project()
    if (hasattr(project, 'sumatra_version')
        and project.sumatra_version == sumatra.__version__
        and "dev" not in sumatra.__version__):

        print("No upgrade needed (project was created with an up-to-date version of Sumatra).")
        sys.exit(1)

    if not os.path.exists(".smt/project_export.json"):
        print("Error: project must have been exported (with the original "
              "version of Sumatra) before upgrading.")
        sys.exit(1)

    # backup and remove .smt
    import shutil
    backup_dir = project.backup(remove_original=True)
    # upgrade the project data
    os.mkdir(".smt")
    shutil.copy("%s/project_export.json" % backup_dir, ".smt/project")
    project.sumatra_version = sumatra.__version__
    project.save()
    # upgrade the record store
    project.record_store.clear()
    filename = "%s/records_export.json" % backup_dir
    if os.path.exists(filename):
        f = open(filename)
        project.record_store.import_(project.name, f.read())
        f.close()
    else:
        print("Record file not found")
        sys.exit(1)
    print("Project successfully upgraded to Sumatra version {0}.".format(project.sumatra_version))


def export(argv):
    usage = "%(prog)s export"
    description = dedent("""\
        Export a Sumatra project and its records to JSON. This is needed before running upgrade.""")
    parser = ArgumentParser(usage=usage,
                            description=description)
    args = parser.parse_args(argv)
    project = load_project()
    project.export()


def sync(argv):
    usage = "%(prog)s sync PATH1 [PATH2]"
    description = dedent("""\
        Synchronize two record stores. If both PATH1 and PATH2 are given, the
        record stores at those locations will be synchronized. If only PATH1 is
        given, and the command is run in a directory containing a Sumatra
        project, only that project's records be synchronized with the store at
        PATH1. Note that PATH1 and PATH2 may be either filesystem paths or URLs.
        """)  # need to say what happens if the sync is incomplete due to label collisions
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('path1')
    parser.add_argument('path2', nargs='?')
    args = parser.parse_args(argv)

    store1 = get_record_store(args.path1)
    if args.path2:
        store2 = get_record_store(args.path2)
        collisions = store1.sync_all(store2)
    else:
        project = load_project()
        store2 = project.record_store
        collisions = store1.sync(store2, project.name)

    if collisions:
        print("Synchronization incomplete: there are two records with the same name for the following: %s" % ", ".join(collisions))
        sys.exit(1)


def migrate(argv):
    usage = "%(prog)s migrate [options]"
    description = dedent("""\
        If you have moved your data files to a new location, update the record
        store to reflect the new paths.
        """)
    # might also want to update the repository upstream
    # should we keep a history of such changes?
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('-d', '--datapath', metavar='PATH', help="modify the path to the directory in which your results are stored.")
    parser.add_argument('-i', '--input', metavar='PATH', help="modify the path to the directory in which your input data files are stored.")
    parser.add_argument('-A', '--archive', metavar='PATH', help="modify the directory in which your results are archived.")
    parser.add_argument('-M', '--mirror', metavar='URL', help="modify the URL at which your data files are mirrored.")
    args = parser.parse_args(argv)
    project = load_project()
    field_map = {
        "datapath": "datastore.root",
        "input": "input_datastore.root",
        "archive": "datastore.archive",
        "mirror": "datastore.mirror_base_url"
    }

    if not any(vars(args).values()):
        warnings.warn(
            "Command 'smt migrate' had no effect. Please provide at least one "
            "argument. (Run 'smt help migrate' for help.)")
    else:
        for option_name, field in field_map.items():
            value = getattr(args, option_name)
            if value:
                project.record_store.update(project.name, field, value)
    # should we also change the default values stored in the Project?


def view(argv):
    """View detail of a single record."""
    usage = "%(prog)s view [options] LABEL"
    description = "View detail of a single record."
    parser = ArgumentParser(usage=usage,
                            description=description)
    parser.add_argument('label')
    parser.add_argument('-s', '--script', action='store_true',
        help="show script content.")
    args = parser.parse_args(argv)
    project = load_project()
    record = project.get_record(args.label)
    if args.script:
        print('Main_File\t :',record.main_file)
        print(80*'-')
        print(record.script_content)
        print(80*'-')
    else:
        formatter = get_formatter('text')([record], project=project)
        print(formatter.format('long'))
    # implementation to finish, including other record fields as options
    # todo: use `formatting` module, rather than print statements


def version(argv):
    usage = "%(prog)s version"
    description = "Print the Sumatra version."
    parser = ArgumentParser(usage=usage,
                            description=description)
    args = parser.parse_args(argv)
    print(sumatra.__version__)
