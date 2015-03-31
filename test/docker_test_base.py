"""
The MIT License (MIT)

Copyright (c) 2015 Red Hat

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import print_function, unicode_literals
from docker import Client
from StringIO import StringIO

import fnmatch
import imp
import inspect
import json
import logging
import os
import re
import sys
import time
import traceback
import uuid
import xml.etree.cElementTree as ET

from docker import Client

d = Client()

class DockerTestRunner(object):

    def __init__(self, image_id, tests, git_repo_path, results_dir, logger=None, **kwargs):
        self.test_file_pattern = "test_*py"
        self.image_id = image_id
        self.tests = tests
        self.git_repo_path = git_repo_path
        self.results_dir = results_dir
        self.kwargs = kwargs

        if logger:
            self.logger = logger
        else:
            self.logger = logging.getLogger("dock.middleware.runner")

    def _log(self, m, level=logging.INFO):
        """ log using logger, or print to stdout """
        if self.logger:
            self.logger.log(level, m)
        else:
            print(m)

    def _run_tests_from_class(self, test_class,  results):
        test_class.setUpClass()
        self._log("Running tests from class '%s'..." % test_class.__class__.__name__, logging.INFO)
        # Loop through all methods from our class
        for test_name, test in inspect.getmembers(test_class, inspect.ismethod):
            # Take only ones which name starts with "test_"
            if test_name.startswith("test_"):
                self._log("Running test '%s'" % test_name, logging.INFO)
                try:
                    test_class.setup()
                    test_result = test()
                    test_class.teardown()
                except Exception as ex:
                    self._log(traceback.format_exc())

                if test_result is not True:
                    self._log("==> Test '%s' failed!" % test_name, logging.ERROR)
                    results[test_name] = False
                else:
                    self._log("==> Test '%s' passed!" % test_name, logging.INFO)
                    results[test_name] = True
        test_class.teardownClass()

    def _generate_xunit_file(self, results):
        root = ET.Element("testsuite", name="mw_docker_tests")
        for test, result in results.items():
            doc = ET.SubElement(root, "testcase", classname=test, name=test)
            if (not result):
                ET.SubElement(doc, "failure", message="error occured")
        tree = ET.ElementTree(root)
        self._log("Creating results dir: " + self.results_dir )
        try:
            os.stat(self.results_dir)
        except:
            os.mkdir(self.results_dir)
        tree.write(self.results_dir +  "/mw_test_out.xml")

    def run(self):
        """ Entry point, run all tests and return results """
        # just hacky to have this module on path
        this_module_path =  os.path.dirname(inspect.getfile(self.__class__))
        sys.path.append(this_module_path)
        results = {}

        if self.tests:
            self._log("Using user provided test location: %s" % self.tests, logging.DEBUG)
            tests_pattern = self.tests
        else:
            self._log("Using default test location: %s" % self.test_file_pattern, logging.DEBUG)
            tests_pattern = self.test_file_pattern

        for path in tests_pattern.split(','):
            dirname, pattern = path.rsplit("/",1)
            # If we get only pattern we use CWD to find classes
            if not dirname:
                dirname = os.getcwd()

            for root, dirs, files in os.walk(dirname):
                # Skip the Git directory itself
                if ".git" in root:
                    continue
                for filename in fnmatch.filter(files, pattern):
                    test_file =  os.path.join(root, filename)
                    module_marker = str(uuid.uuid4())
                    # Load class to unique namespace
                    test_module = imp.load_source(module_marker, test_file)

                    # Get all classes from our module
                    for name, clazz in inspect.getmembers(test_module, inspect.isclass):
                        # Check that class is from our namespace
                        if module_marker == clazz.__module__:
                            # Instantiate class
                            cls = getattr(test_module, name)
                            test_class = cls(runner=self, logger=None)
                            self._run_tests_from_class(test_class, results)

            failed_tests = {k:v for (k,v) in results.items() if results[k] is False}
            passed_tests = {k:v for (k,v) in results.items() if results[k] is True}
        if not failed_tests:
            self._log("==> Summary: All tests passed!", logging.INFO)
        else:
            self._log("==> Summary: %s of %s tests failed!" % (len(failed_tests), len(results.items())), logging.ERROR)
            self._log("Failed tests: %s" % failed_tests.keys(), logging.ERROR)

        self._generate_xunit_file(results)
        return results, not bool(failed_tests)


class DockerTest(object):
    """
    Base class for all Docker integration tests
    Its purpose is to emulate abstract class for CE tests
    """
    def __init__(self, runner, logger=None, **kwargs):
        self.runner = runner
        if logger:
            self.logger = logger
        else:
            self.logger = logging.getLogger("dock.middleware.base")

    def _log(self, m, level=logging.INFO):
        """ log using logger, or print to stdout """
        if self.logger:
            self.logger.log(level, m)
        else:
            print(m)

    def setup(self):
        """ This method is called before every test run """
        pass

    def setUpClass(self):
        """ This method is called when test class is setuped """
        self.container = Container(self.runner.image_id)
        self.container.start()

    def teardown(self):
        """ Called after every test run """
        pass

    def teardownClass(self):
        self.container.stop(directory=self.runner.results_dir)


class Container(object):
    """
    Object representing a docker test container, it is used in tests
    """

    def __init__(self, image_id, name=None):
        self.image_id = image_id
        self.container = None
        self.name = None
        self.ip_address = None
        self.logger = logging.getLogger("dock.middleware.container")

    def start(self, environment = {}):
        """ Starts a detached container for selected image """
        self.logger.debug("Creating container from image '%s'..." % self.image_id)
        self.container = d.create_container(image=self.image_id, environment=environment, detach=True)
        self.logger.debug("Starting container '%s'..." % self.container.get('Id'))
        d.start(container=self.container)
        self.ip_address =  d.inspect_container(container=self.container.get('Id'))['NetworkSettings']['IPAddress']

    def stop(self, directory="target", mark_output=False, save_output=True):
        """
        Stops (and removes) selected container.
        Additionally saves the STDOUT output to a `container_output` file for later investigation.
        """
        if save_output:
            if mark_output:
                out_path = directory + "container_" + self.name + "_" + self.container.get('Id') + "_output.txt"
            else:
                out_path = directory + "container_" + self.container.get('Id') + "output.txt"
            with open(out_path, 'w') as f:
                print(d.attach(container=self.container.get('Id'), stream=False, logs=True), file=f)
            f.closed
        if self.container:
            self.logger.debug("Removing container '%s'" % self.container['Id'])
            d.kill(container=self.container)
            d.remove_container(self.container)
        else:
            self.logger.debug("no container to tear down")

    def execute(self, cmd):
        """ executes cmd in container and return its output """
        return d.execute(self.container, cmd=cmd)

def run(image_id, tests, git_repo_path, results_dir, logger=None, **kwargs):
    e = DockerTestRunner(image_id, tests, git_repo_path, results_dir, logger=None, **kwargs)
    return e.run()
