# pyworkflow

## unified framework for workflow processes

pyworkflow supports the easy implementation of workflows, and handling the
execution of workflow processes, across multiple backends. Its API is largely
based on that of Amazon Simple Workflow Framework (SWF). Different backends
can be used allowing to leverage the full power of workflows without
committing to any single execution environment. pyworkflow was initially
written as an abstraction layer around Amazon SWF. However, different backends
provide alternative execution environments. One could imagine building a
backend on any generic queueing system or database.

## Usage

Workflows determine the particular flow of a process through a set of
activities. The first step is to implement activities by overriding the
Activity, like so:

````python
from pyworkflow.managed import Activity
from pyworkflow.activity import ActivityCanceled

class MultiplicationActivity(Activity):

	scheduled_timeout = 300 # max seconds in queue
	execution_timeout = 10  # max execution duration

	def execute(self):
		if not type(self.input) == list and not len(self.input) == 2:
			raise ValueError("invalid input")

		if self.input[0] > 10:
			return ActivityCanceled("first operand must be <= 10")

		result = 0
		for _ in range(0, self.input[0]):
			# multiplication is repeated addition
			result += self.input[1]

			# give a sign of life
			self.heartbeat()

		return result
````

Next, we define our workflows that guide processes through the activities. A
workflow extends the Workflow class and overrides its decide() method:

````python
from pyworkflow.managed import Workflow
from pyworkflow.decision import CompleteProcess

class FooWorkflow(Workflow):

	activities = [MultiplicationActivity]

	def decide(self, process):
		if foo_bar_conditional(process.history):
			# shorthand for ScheduleActivity(MultiplicationActivity, input=process.input)
			return MultiplicationActivity
		else:
			return CompleteProcess()
````

Then create a manager with a particular backend and register our workflows

````python
from pyworkflow.managed import Manager
from pyworkflow.foo import FooBackend

workflows = [FooWorkflow]
manager = Manager(backend=FooBackend(), workflows=workflows)
````

To start an activity worker (in a separate thread/process; is blocking)
````python
from pyworkflow.managed.worker import WorkerThread, ActivityWorker
WorkerThread(ActivityWorker(manager)).run()
````

Or a decider:
````python
from pyworkflow.managed.worker import WorkerThread, DecisionWorker
WorkerThread(DecisionWorker(manager)).run()
````

To start a new process
````python
process = Process(workflow=FooWorkflow, input=foo_bar)
manager.start_process(process)
````

## Backends

### Memory (included)

MemoryBackend provides a rudimentary in-memory backend. It is mainly useful
for testing and development purposes. Be aware that it is not thread-safe.

````python
from pyworkflow.memory import MemoryBackend
from pyworkflow.managed import Manager

backend = MemoryBackend()
manager = Manager(backend=backend)
````

### Amazon Simple Workflow Framework

[pyworkflow.amazonswf](https://github.com/pyworkflow/pyworkflow.amazonswf) supports integration of pyworkflow with Amazon's Simple
Workflow Framework service.

````python
from pyworkflow.amazonswf import AmazonSWFBackend
from pyworkflow.managed import Manager

backend = AmazonSWFBackend(ACCESS_KEY_ID, SECRET_ACCESS_KEY, region='us-east-1', domain='foo.bar')
manager = Manager(backend=backend)
````

### Datastore

[pyworkflow.datastore](https://github.com/pyworkflow/pyworkflow.datastore) provides a simple backend that stores execution state to a
datastore [https://github.com/datastore/datastore](https://github.com/datastore/datastore). It is mainly useful 
during development.

````python
from datastore.filesystem import FileSystemDatastore()
from pyworkflow.datastore import DatastoreBackend
from pyworkflow.managed import Manager

ds = FileSystemDatastore('/tmp/.pyworkflow_datastore')
backend = DatastoreBackend(ds)
manager = Manager(backend=backend)
````

### Blinker

[pyworkflow.blinker](https://github.com/pyworkflow/pyworkflow.blinker) wraps around any other backend and emits [blinker](http://pythonhosted.org/blinker/) signals on
important runtime events on activities and decisions.

````python
from pyworkflow.memory import MemoryBackend
from pyworkflow.blinker import BlinkerBackend
from pyworkflow.managed import Manager

backend = BlinkerBackend(MemoryBackend())
manager = Manager(backend=backend)

# listen to process started signal
def process_started(sender, **kwargs):
	print 'Started %s' % kwargs['process']

BlinkerBackend.on_process_started.connect(process_started)
````

## Architecture

### pyworkflow.process.Process

A Process is a particular execution of a workflow. It contains the history of
the execution flow in the form of events that contain the decisions taken, the
result  of executed activities, and the signals sent in instances of
pyworkflow.event.Event.

### pyworkflow.task.Task

An ActivityTask stipulates the execution of an activity on some input. It is a
fully independent entity. It does not contain a reference to the process it is
a part of, nor to the invoker who executes it. It is the entity that is
exchanged between the backend and the worker as an identifier. Similarly, a
DecisionTask stipulates that decisions should be made on the execution path of
a particular process.

### pyworkflow.backend.Backend

A backend administers the execution states of workflow processes and
activities. It is responsible for storing active processes and handing out
tasks to be completed. Backend provides the interface to whatever underlying
system is used to drive the processes and uses the Process and Task classes to
communicate.

### pyworkflow.managed.Workflow

A Workflow manages the execution path of a Process for that workflow, which is
a consecutive application of Activities on a certain input. The invocation of
a Workflow is started when a process for that Workflow is created. Workflow
returns decisions on a process by means of Decision objects.

### pyworkflow.managed.Activity

Activity specifies the logic of some business function. It is instantiated to
execute an ActivityTask. It may need to let the invoker know it's still active
from time to time by sending heartbeats. An ActivityMonitor can be set on an
activity for that purpose. Activity returns results by means of an
ActivityResult object.

### pyworkflow.managed.Manager

A Manager sits in front of a Backend and links it together with Activity and
Workflow classes. It is the main intended high level interface when using
pyworkflow. Processes can be started as well as signaled through Manager.
Manager reads Tasks from its Backend and hands those out along with the
required Activity or Workflow class. It can also communicate results of these
tasks back to the Backend. Typically a Worker (linked to the manager) would
receive and execute the tasks.

### pyworkflow.managed.worker.Worker

ActivityWorker executes an ActivityTask it gets from the Manager by executing
the specified Activity and committing the results back to the Manager. It
keeps the Backend informed of progress through heartbeats. A DecisionWorker
executes a DecisionTask by asking the specified Workflow to return a list of
decisions.


## About

### License

pyworkflow is under the MIT License.

### Contact

pyworkflow is written by [Willem Bult](https://github.com/willembult).

Project Homepage: [https://github.com/pyworkflow/pyworkflow](https://github.com/pyworkflow/pyworkflow)

Feel free to contact me. But please file issues in github first. Thanks!
