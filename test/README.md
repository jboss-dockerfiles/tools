docker test
===========

Pyunit inspired testing framework supposed to be run in a [dock](https://github.com/DBuildService/dock) build system

## Features:
* support for STI build
* xUnit like reporting (used with jenkins)
* dynamic tests discovery and loading


### Installation and Usage
Upstream dock has a plugin for this - so basicly install dock and run build with following plugin json snipplet:

``` json
  "prepublish_plugins": [{
    "name": "test_built_image",
    "args": {
      "image_id": "BUILT_IMAGE_ID",
      "git_uri": "https://bitbucket.org/jboss-dockerfiles/tools",
      "git_commit": "master",
      "tests_git_path": "test/docker_test_base.py",
      "tests": "tests\*py",
      "results_dir": "target"
    }
  }]
```
this will load all python modules located in a "tests" directory and execute any methods which starts with test_ prefix and record it results.


### Writing tests
Writing tests is a very easy task. Basically you just inherit our DockerTest class and implement your tests in method prefixed with Tests.

DockerTests class also offers several methods:

setupClass:
is called when the class is loaded and starts a docker container by default.

teardownClass:
is called after last test in class was run and stops docker container

setup:
method is invoked before each test, NOOP by default

teardown
method is invoked after each test, NOOP by default.

example:
This test case takes an JBoss EAP images builded by Dock and use STI to build image with apllication from 'https://bitbucket.org/goldmann/openshift-eap-examples'. After that JBoss EAP logs are search for the application deployed.

``` python
from docker_test_base import DockerTest
from dokcer_test_helpers import sti_build
import docker_test_helpers

class EapTest(DockerTest):

    @sti_build('https://bitbucket.org/goldmann/openshift-eap-examples', path='binary')
    def test_sti_binary_application(self):
        return docker_test_helpers.expect_message(self.sti_container, ['JBAS015859: Deployed \\"node-info.war\\"'])

``` 
