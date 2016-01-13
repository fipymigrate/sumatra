#!/usr/bin/env python


from setuptools import setup
from distutils.command.sdist import sdist
import os
import sys


class sdist_git(sdist):
    """Add revision number to version for development releases."""

    def run(self):
        if "dev" in self.distribution.metadata.version:
            self.distribution.metadata.version += self.get_tip_revision()
        sdist.run(self)

    def get_tip_revision(self, path=os.getcwd()):
        try:
            import git
        except ImportError:
            return ''
        try:
            repo = git.Repo('.')
        except git.InvalidGitRepositoryError:
            return ''
        return repo.head.commit.hexsha[:7]


install_requires = ['Django>=1.6, <1.9', 'django-tagging', 'httplib2',
                    'docutils', 'jinja2', 'parameters', 'future']
major_python_version, minor_python_version, _, _, _ = sys.version_info
if major_python_version < 3 or (major_python_version == 3 and minor_python_version < 4):
    install_requires.append('pathlib')

setup(
    name = "Sumatra",
    version = "0.8dev",
    package_dir = {'sumatra': 'sumatra'},
    packages = ['sumatra', 'sumatra.dependency_finder', 'sumatra.datastore',
                'sumatra.recordstore', 'sumatra.recordstore.django_store',
                'sumatra.versioncontrol', 'sumatra.formatting',
                'sumatra.web', 'sumatra.web.templatetags',
                'sumatra.publishing',
                'sumatra.publishing.latex', 'sumatra.publishing.sphinxext'],
    package_data = {'sumatra': ['web/static/css/*.css', 'web/static/js/*.js', 'web/static/img/*.png',
                                'web/static/fonts/*', 'web/templates/*.html',
                                'publishing/latex/sumatra.sty',
                                'formatting/latex_template.tex', 'external_scripts/script_introspect.R']},
    scripts = ['bin/smt', 'bin/smtweb', 'bin/smt-complete.sh'],
    author = "Sumatra authors and contributors",
    author_email = "andrew.davison@unic.cnrs-gif.fr",
    description = "A tool for automated tracking of computation-based scientific projects",
    long_description = open('README.rst').read(),
    license = "BSD 2 clause",
    keywords = "computational science simulation analysis project-management",
    url = "http://neuralensemble.org/sumatra/",
    classifiers = ['Development Status :: 4 - Beta',
                   'Environment :: Console',
                   'Environment :: Web Environment',
                   'Intended Audience :: Science/Research',
                   'License :: OSI Approved :: BSD License',
                   'Natural Language :: English',
                   'Operating System :: OS Independent',
                   'Programming Language :: Python',
                   'Programming Language :: Python :: 2.6',
                   'Programming Language :: Python :: 2.7',
                   'Programming Language :: Python :: 3.4',
                   'Programming Language :: Python :: 3.5',
                   'Topic :: Scientific/Engineering'],
    cmdclass = {'sdist': sdist_git},
    install_requires = install_requires,
    extras_require = {'svn': 'pysvn',
                      'hg': 'hgapi',
                      'git': 'GitPython',
                      'bzr': 'bzr',
                      'mpi': 'mpi4py'}
)
