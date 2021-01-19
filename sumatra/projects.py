"""
The projects module defines the Project class, which stores information
about a computation-based project and contains a number of methods for managing
and running computational experiments, whether simulations, analyses or whatever.
This is the main class that is used directly when using Sumatra within your own
scripts.

Classes
-------

Project - stores information about a computational project, and enables
          launching, annotating, deleting and retrieving information about
          simulation/analysis runs.

Functions
---------
load_project() - read project information from the working directory and return
                 a Project object.

:copyright: Copyright 2006-2015 by the Sumatra team, see doc/authors.txt
:license: BSD 2-clause, see LICENSE for details.
"""
from __future__ import print_function
from __future__ import unicode_literals
from future import standard_library
standard_library.install_aliases()
from builtins import str
from builtins import object

import os
import re
import importlib
import pickle
from copy import deepcopy
import uuid
import sumatra
import django
import sqlite3
import time
import shutil
import textwrap
from datetime import datetime
from importlib import import_module
from sumatra.records import Record
from sumatra import programs, datastore
from sumatra.formatting import get_formatter, get_diff_formatter
from sumatra.recordstore import DefaultRecordStore
from sumatra.versioncontrol import UncommittedModificationsError, get_working_copy, VersionControlError
from sumatra.core import TIMESTAMP_FORMAT
import mimetypes
import json
import logging

logger = logging.getLogger("Sumatra")

DEFAULT_PROJECT_FILE = "project"

LABEL_GENERATORS = {
    'timestamp': lambda: None,  # this is the default, implemented in the Record class
    'uuid': lambda: str(uuid.uuid4()).split('-')[-1]
}


def _remove_left_margin(s):  # replace this by textwrap.dedent?
    lines = s.strip().split('\n')
    return "\n".join(line.strip() for line in lines)


def _get_project_file(path):
    return os.path.join(path, ".smt", DEFAULT_PROJECT_FILE)


class Project(object):
    valid_name_pattern = r'(?P<project>\w+[\w\- ]*)'

    def __init__(self, name, default_executable=None, default_repository=None,
                 default_main_file=None, default_launch_mode=None,
                 data_store='default', record_store='default',
                 on_changed='error', description='', data_label=None,
                 input_datastore=None, label_generator='timestamp',
                 timestamp_format=TIMESTAMP_FORMAT,
                 allow_command_line_parameters=True, plugins=[]):
        self.path = os.getcwd()
        if not os.path.exists(".smt"):
            os.mkdir(".smt")
        if os.path.exists(_get_project_file(self.path)):
            raise Exception("Sumatra project already exists in this directory.")
        if re.match(Project.valid_name_pattern, name):
            self.name = name
        else:
            raise ValueError("Invalid project name. Names may only contain letters, numbers, spaces and hyphens")
        self.default_executable = default_executable
        self.default_repository = default_repository  # maybe we should be storing the working copy instead, as this has a ref to the repository anyway
        self.default_main_file = default_main_file
        self.default_launch_mode = default_launch_mode
        if data_store == 'default':
            data_store = datastore.FileSystemDataStore(None)
        self.data_store = data_store  # a data store object
        self.input_datastore = input_datastore or self.data_store
        if record_store == 'default':
            record_store = DefaultRecordStore(os.path.abspath(".smt/records"))
        self.record_store = record_store
        self.on_changed = on_changed
        self.description = description
        self.data_label = data_label
        self.label_generator = label_generator
        self.timestamp_format = timestamp_format
        self.sumatra_version = sumatra.__version__
        self.allow_command_line_parameters = allow_command_line_parameters
        self._most_recent = None
        self.plugins = []
        self.load_plugins(*plugins)
        self.save()
        print("Sumatra project successfully set up")

    def __set_data_label(self, value):
        assert value in (None, 'parameters', 'cmdline')
        self._data_label = value

    def __get_data_label(self):
        return self._data_label
    data_label = property(fset=__set_data_label, fget=__get_data_label)

    def save(self):
        """Save state to some form of persistent storage. (file, database)."""
        state = {}
        for name in ('name', 'default_executable', 'default_repository',
                     'default_launch_mode', 'data_store', 'record_store',
                     'default_main_file', 'on_changed', 'description',
                     'data_label', '_most_recent', 'input_datastore',
                     'label_generator', 'timestamp_format', 'sumatra_version',
                     'allow_command_line_parameters', 'plugins'):
            try:
                attr = getattr(self, name)
            except:
                # Some parameters which need special treatment to avoid
                # unexpected behaviour.
                if name == 'allow_command_line_parameters':
                    print(textwrap.dedent("""\
                        Upgrading from a Sumatra version which did not have the --plain configuration
                        option. After this upgrade, arguments to 'smt run' of the form 'name=value'
                        will continue to overwrite default parameter values, but this is now
                        configurable. If it is desired that they should be passed straight through
                        to the program, run the command 'smt configure --plain' after this upgrade.
                        """))
                    attr = True
                else:
                    # Default value for unrecognised parameters
                    attr = None
            if hasattr(attr, "__getstate__"):
                state[name] = {'type': attr.__class__.__module__ + "." + attr.__class__.__name__}
                for key, value in attr.__getstate__().items():
                    if key in ['url','root','working_directory','db_file']:
                        value = os.path.relpath(value)
                    state[name][key] = value
            else:
                state[name] = attr
        f = open(_get_project_file(self.path), 'w')  # should check if file exists?
        json.dump(state, f, indent=2)
        f.close()

    def info(self):
        """Show some basic information about the project."""
        template = """
        Project name        : %(name)s
        Default executable  : %(default_executable)s
        Default repository  : %(default_repository)s
        Default main file   : %(default_main_file)s
        Default launch mode : %(default_launch_mode)s
        Data store (output) : %(data_store)s
        .          (input)  : %(input_datastore)s
        Record store        : %(record_store)s
        Code change policy  : %(on_changed)s
        Append label to     : %(_data_label)s
        Label generator     : %(label_generator)s
        Timestamp format    : %(timestamp_format)s
        Plug-ins            : %(plugins)s
        Sumatra version     : %(sumatra_version)s
        """
        return _remove_left_margin(template % self.__dict__)

    def new_record(self, parameters={}, input_data=[], script_args="",
                   executable='default', repository='default',
                   main_file='default', version='current', launch_mode='default',
                   diff='', label=None, reason=None, timestamp_format='default'):
        logger.debug("Creating new record")
        if executable == 'default':
            executable = deepcopy(self.default_executable)
        if repository == 'default':
            repository = deepcopy(self.default_repository)
        if main_file == 'default':
            main_file = self.default_main_file
        if launch_mode == 'default':
            launch_mode = deepcopy(self.default_launch_mode)
        if timestamp_format == 'default':
            timestamp_format = self.timestamp_format
        working_copy = repository.get_working_copy()
        version, diff = self.update_code(working_copy, version, diff)
        if label is None:
            label = LABEL_GENERATORS[self.label_generator]()
        record = Record(executable, repository, main_file, version, launch_mode,
                        self.data_store, parameters, input_data, script_args,
                        label=label, reason=reason, diff=diff,
                        on_changed=self.on_changed,
                        input_datastore=self.input_datastore,
                        timestamp_format=timestamp_format)

        self.add_record(record)

        if not isinstance(executable, programs.MatlabExecutable):
            record.register(working_copy)

        return record

    def launch(self, parameters={}, input_data=[], script_args="",
               executable='default', repository='default', main_file='default',
               version='current', launch_mode='default', diff='', label=None, reason=None,
               timestamp_format='default', repeats=None):
        """Launch a new simulation or analysis."""
        record = self.new_record(parameters, input_data, script_args,
                                 executable, repository, main_file, version,
                                 launch_mode, diff, label, reason, timestamp_format)
        record.run(with_label=self.data_label, project=self)
        if 'matlab' in record.executable.name.lower():
            record.register(record.repository.get_working_copy())
        if repeats:
            record.repeats = repeats
        self.save_record(record)
        logger.debug("Record saved @ completion.")
        self.save()
        return record.label

    def update_code(self, working_copy, version='current', diff=''):
        """Check if the working copy has modifications and prompt to commit or revert them."""
        # we really need to extend this to the dependencies, but we need to take extra special care that the
        # code ends up in the same condition as before the run
        logger.debug("Updating working copy to use version: %s" % version)
        changed = working_copy.has_changed()
        if (version == 'current' or version == working_copy.current_version) and not diff:
            if changed:
                if self.on_changed == "error":
                    raise UncommittedModificationsError("Code has changed, please commit your changes")
                elif self.on_changed == "store-diff":
                    diff = working_copy.diff()
                else:
                    raise ValueError("store-diff must be either 'error' or 'store-diff'")
        elif diff:
            if changed:
                raise UncommittedModificationsError(
                    "Code has changed. These changes will be lost when switching "
                    "to a different version, so please commit or stash your "
                    "changes and then retry.")
            else:
                working_copy.use_version(version)
                working_copy.patch(diff)
        elif version == 'latest':
            working_copy.use_latest_version()
        else:
            working_copy.use_version(version)
        version = working_copy.current_version()
        return version, diff

    def add_record(self, record):
        """Add a simulation or analysis record."""
        success = False
        cnt = 0
        max_tries = 200
        sleep_seconds = 5
        while not success and cnt < max_tries:
            try:
                self.save_record(record)
                success = True
                self._most_recent = record.label
                logger.debug("Created record: %s" % self.most_recent())
            except (django.db.utils.DatabaseError, sqlite3.OperationalError):
                print("Failed to save record due to database error. Trying again in {0} seconds. (Attempt {1}/{2})".format(sleep_seconds, cnt, max_tries))
                time.sleep(sleep_seconds)
                cnt += 1
        if cnt == max_tries:
            print("Reached maximum number of attempts to save record. Aborting.")

    def save_record(self, record):
        self.record_store.save(self.name, record)

    def get_record(self, label):
        """Search for a record with the supplied label and return it if found.
           Otherwise return None."""
        return self.record_store.get(self.name, label)

    def delete_record(self, label, delete_data=False):
        """Delete a record. Return 1 if the record is found.
           Otherwise return 0."""
        if delete_data:
            self.get_record(label).delete_data()
        self.record_store.delete(self.name, label)
        self._most_recent = self.record_store.most_recent(self.name)

    def delete_by_tag(self, tag, delete_data=False):
        """Delete all records with a given tag. Return the number of records deleted."""
        if delete_data:
            for record in self.record_store.list(self.name, tag):
                record.delete_data()
        n = self.record_store.delete_by_tag(self.name, tag)
        self._most_recent = self.record_store.most_recent(self.name)
        return n

    def get_labels(self, tags=None, reverse=False, *args, **kwargs):
        labels = self.record_store.labels(self.name, tags=tags, *args, **kwargs)
        if reverse:
            labels.reverse()
        return labels

    def find_records(self, tags=None, reverse=False, parameters=None, *args, **kwargs):
        records = self.record_store.list(self.name, tags=tags, *args, **kwargs)
        if reverse:
            records.reverse()
        if parameters is not None:
            records = [rec for rec in records if len(rec.parameters.diff(parameters)[-1]) == 0]
        return records

    def find_input_data(self, *args, **kwargs):
        records = self.find_records(*args, **kwargs)
        if len(records) == 0: return []
        input_data = []
        for record in records:
            for input_file in record.input_data:
                input_data.append(input_file)
        return input_data

    def find_output_data(self, *args, **kwargs):
        records = self.find_records(*args, **kwargs)
        if (records) == 0: return []
        output_data = []
        for record in records:
            for output_file in record.output_data:
                output_data.append(output_file)
        return output_data

    def find_data(self, *args, **kwargs):
        input_data = self.find_input_data(*args, **kwargs)
        output_data = self.find_output_data(*args, **kwargs)
        return {'input_data': input_data, 'output_data': output_data}

    def format_records(self, format='text', mode='short', tags=None, reverse=False, *args, **kwargs):
        if format=='text' and mode=='short' and ('parameters' not in kwargs.keys()):
            return '\n'.join(self.get_labels(tags=tags, reverse=reverse, *args, **kwargs))
        else:
            records = self.find_records(tags=tags, reverse=reverse, *args, **kwargs)
            formatter = get_formatter(format)(records, project=self, tags=tags)
            return formatter.format(mode)

    def most_recent(self):
        try:
            return self.get_record(self._most_recent)
        except KeyError:  # the record pointed to by self._most_recent has been deleted
            self._most_recent = self.record_store.most_recent(self.name)
            return self.most_recent()

    def add_comment(self, label, comment, replace=False):
        try:
            record = self.record_store.get(self.name, label)
        except Exception as e:
            raise Exception("%s. label=<%s>" % (e, label))
        if replace or record.outcome is "":
            record.outcome = comment
        else:
            record.outcome = record.outcome + "\n" + comment
        self.save_record(record)

    def add_tag(self, label, tag):
        record = self.record_store.get(self.name, label)
        record.add_tag(tag)
        self.save_record(record)

    def remove_tag(self, label, tag):
        record = self.record_store.get(self.name, label)
        record.tags.remove(tag)
        self.save_record(record)

    def compare(self, label1, label2, ignore_mimetypes=[], ignore_filenames=[]):
        record1 = self.record_store.get(self.name, label1)
        record2 = self.record_store.get(self.name, label2)
        return record1.difference(record2, ignore_mimetypes, ignore_filenames)

    def show_diff(self, label1, label2, mode='short', ignore_mimetypes=[], ignore_filenames=[]):
        diff = self.compare(label1, label2, ignore_mimetypes, ignore_filenames)
        formatter = get_diff_formatter()(diff)
        return formatter.format(mode)

    def export(self):
        # copy the project data
        shutil.copy(".smt/project", ".smt/project_export.json")
        # export the record data
        f = open(".smt/records_export.json", 'w')
        f.write(self.record_store.export(self.name))
        f.close()

    def repeat(self, original_label, new_label=None):
        if original_label == 'last':
            tmp = self.most_recent()
        else:
            tmp = self.get_record(original_label)
        original = deepcopy(tmp)
        if hasattr(tmp.parameters, '_url'):  # for some reason, _url is not copied.
            original.parameters._url = tmp.parameters._url  # this is a hackish solution - needs fixed properly
        try:
            working_copy = get_working_copy()
        except VersionControlError:
            original.repository.checkout()
            working_copy = original.repository.get_working_copy()
        if working_copy.repository != original.repository:
            raise NotImplementedError("Ability to switch repositories not yet implemented.")
        current_version = working_copy.current_version()
        new_label = self.launch(parameters=original.parameters,
                                input_data=original.input_data,
                                script_args=original.script_arguments,
                                executable=original.executable,
                                main_file=original.main_file,
                                repository=original.repository,
                                version=original.version,
                                launch_mode=original.launch_mode,
                                diff=original.diff,
                                label=new_label,
                                reason="Repeat experiment %s" % original.label,
                                repeats=original.label)
        working_copy.reset()
        working_copy.use_version(current_version)  # ensure we switch back to the original working copy state
        return new_label, original.label

    def backup(self, remove_original=False):
        """
        Create a new backup directory in the same location as the
        project directory and copy the contents of the project
        directory into the backup directory. Uses `_get_project_file`
        to extract the path to the project directory.

        :return:
          - `backup_dir`: the directory used for the backup

        """
        if remove_original:
            self.record_store.remove()  # creates backup then removes original file
        else:
            self.record_store.backup()
        smt_dir = os.path.split(_get_project_file(self.path))[0]
        backup_dir = smt_dir + "_backup_%s" % datetime.now().strftime(TIMESTAMP_FORMAT)
        shutil.copytree(smt_dir, backup_dir)
        if remove_original:
            shutil.rmtree(smt_dir)
        return backup_dir

    def change_record_store(self, new_store):
        """
        Change the record store that is used by this project.
        """
        self.backup()
        old_store = self.record_store
        new_store.sync(old_store, self.name)
        self.record_store = new_store

    def load_plugins(self, *plugins):
        for plugin in plugins:
            import_module(plugin)
            if plugin not in self.plugins:
                self.plugins.append(plugin)

    def remove_plugins(self, *plugins):
        # note that we do not unimport plugins, we just remove them from the list
        # in future, we should unregister the component from the registry
        for plugin in plugins:
            self.plugins.remove(plugin)


def _load_project_from_json(path):
    f = open(_get_project_file(path), 'r')
    data = json.load(f)
    f.close()
    prj = Project.__new__(Project)
    prj.path = path
    for key, value in data.items():
        if isinstance(value, dict) and "type" in value:
            parts = str(value["type"]).split(".")  # make sure not unicode, see http://stackoverflow.com/questions/1971356/haystack-whoosh-index-generation-error/2683624#2683624
            module_name = ".".join(parts[:-1])
            class_name = parts[-1]
            module = importlib.import_module(module_name)
            cls = getattr(module, class_name)
            args = {}
            for k, v in value.items():
                if k != 'type':
                    args[str(k)] = v  # need to use str() as json module uses all unicode
            setattr(prj, key, cls(**args))
        else:
            setattr(prj, key, value)
    if hasattr(prj, "plugins"):
        prj.load_plugins(*prj.plugins)
    else:
        prj.plugins = []
    return prj


def _load_project_from_pickle(path):
    # earlier versions of Sumatra saved Projects using pickle
    f = open(_get_project_file(path), 'r')
    prj = pickle.load(f)
    f.close()
    return prj


def load_project(path=None):
    """
    Read project from directory passed as the argument and return Project
    object. If no argument is given, the project is read from the current
    directory.
    """
    if not path:
        p = os.getcwd()
    else:
        p = os.path.abspath(path)
    while not os.path.isdir(os.path.join(p, ".smt")):
        oldp, p = p, os.path.dirname(p)
        if p == oldp:
            raise IOError("No Sumatra project exists in the current directory or above it.")
    mimetypes.init(mimetypes.knownfiles + [os.path.join(p, ".smt", "mime.types")])
    # try:
    prj = _load_project_from_json(p)
    # except Exception:
    #    prj = _load_project_from_pickle(p)
    return prj
