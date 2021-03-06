from itertools import ifilter
from collections import deque
from datetime import datetime, timedelta
from uuid import uuid4

from ..backend import Backend
from ..activity import *
from ..exceptions import TimedOutException, UnknownActivityException, UnknownDecisionException
from ..events import *
from ..decision import *
from ..task import *
from ..process import *
from ..signal import *
from ..defaults import Defaults

class MemoryBackend(Backend):
    '''
    Non-thread-safe in-memory workflow backend. Primarily for testing purposes.

    TODO: some basic locking on process queues for thread safety would be nice.
    '''

    def __init__(self):
        self.workflows = {}
        self.activities = {}

        self.running_processes = {}
        self.running_activities = {}
        self.running_decisions = {}

        self.scheduled_decisions = {Defaults.DECISION_CATEGORY: deque([])}
        self.scheduled_activities = {Defaults.ACTIVITY_CATEGORY: deque([])}
        
    def _managed_process(self, pid):
        return self.running_processes[pid]

    def _schedule_activity(self, process, activity, id, input, queue=None):
        expiration = datetime.now() + timedelta(seconds=self.activities[activity]['scheduled_timeout'])
        execution = ActivityExecution(activity, id, input=input)
        queue = queue or self.activities[activity]['category']
        self.scheduled_activities[queue].append((execution, process, expiration))

    def _activity_by_id(self, id):
        activity = filter(lambda (key, a): a[0].id == id, self.running_activities.items())
        if not activity:
            for q, queue in self.scheduled_activities.items():
                activity = filter(lambda a: a[0].id == id, queue)
                if activity:
                    break
        return (activity or [None])[0]        

    def _cancel_activity(self, id):
        for q, queue in self.scheduled_activities.items():
            to_cancel = filter(lambda a: a[0].id == id, queue)    
            for a in to_cancel:
                queue.remove(a)

        to_cancel = filter(lambda (key, a): a[0].id == id, self.running_activities.items())
        for (key, a) in to_cancel:
            del self.running_activities[key]
            

    def _schedule_decision(self, process, start=None, timer=None):
        queue = self.workflows[process.workflow]['category']

        existing = filter(lambda a: a[0] == process, self.scheduled_decisions[queue])
        matching = filter(lambda a: not a[1] or a[1] <= (start or datetime.now()), existing)
        if timer or not len(matching):
            if timer:
                expiration = None
            else:
                expiration = datetime.now() + timedelta(seconds=self.workflows[process.workflow]['decision_timeout'])

            self.scheduled_decisions[queue].append((process, start, expiration, timer))
            self.scheduled_decisions[queue] = sorted(self.scheduled_decisions[queue], key=lambda d: d[1] or datetime.now())

    def _cancel_decision(self, process):
        for cat, queue in self.scheduled_decisions.items():
            to_cancel = filter(lambda a: a[0] == process, queue)
            for a in to_cancel:
                queue.remove(a)

    def register_workflow(self, name, category=Defaults.DECISION_CATEGORY,
        timeout=Defaults.WORKFLOW_TIMEOUT, 
        decision_timeout=Defaults.DECISION_TIMEOUT):

        self.workflows[name] = {
            'category': category,
            'timeout': timeout,
            'decision_timeout': decision_timeout
        }

        self.scheduled_decisions.setdefault(category, deque([]))

    def register_activity(self, name, category=Defaults.ACTIVITY_CATEGORY, 
        scheduled_timeout=Defaults.ACTIVITY_SCHEDULED_TIMEOUT, 
        execution_timeout=Defaults.ACTIVITY_EXECUTION_TIMEOUT, 
        heartbeat_timeout=Defaults.ACTIVITY_HEARTBEAT_TIMEOUT):

        self.activities[name] = {
            'category': category,
            'scheduled_timeout': scheduled_timeout,
            'execution_timeout': execution_timeout,
            'heartbeat_timeout': heartbeat_timeout
        }

        self.scheduled_activities.setdefault(category, deque([]))

    def start_process(self, process):
        # register the process
        pid = str(uuid4())
        process = process.copy_with_id(pid, history=[ProcessStartedEvent()])
        self.running_processes[process.id] = process
        # schedule a decision
        self._schedule_decision(process)
        return pid
        
    def signal_process(self, process_id, signal, data=None):
        # find the process as we know it
        managed_process = self._managed_process(process_id)

        # append the signal event
        managed_process.history.append(SignalEvent(Signal(signal, data)))

        # schedule a decision (if needed)
        self._schedule_decision(managed_process)

    def _cancel_process_internal(self, managed_process):

        # remove scheduled decision
        self._cancel_decision(managed_process)

        # remove process
        del self.running_processes[managed_process.id]

        # cancel child processes
        children = [p for p in self.running_processes.values() if p.parent == managed_process.id]
        for c in children:
            self.cancel_process(c.id)

    def cancel_process(self, process_id, details=None):
        # find the process as we know it
        managed_process = self._managed_process(process_id)

        # append the cancelation event
        managed_process.history.append(DecisionEvent(CancelProcess(details=details)))

        self._cancel_process_internal(managed_process)


    def heartbeat_activity_task(self, task):
        self._time_out_activities()

        # find the process as we know it
        activity = self.running_activities.get(task.context['run_id'])
        
        # replace with new heartbeat timeout
        new_timeout = datetime.now() + timedelta(seconds=self.activities[activity[0].activity]['heartbeat_timeout'])
        self.running_activities[task.context['run_id']] = (activity[0],activity[1],activity[2],new_timeout)
            

    def complete_decision_task(self, task, decisions):
        self._time_out_decisions()
        
        if not type(decisions) is list:
            decisions = [decisions]

        # find the process as we know it
        decision = self.running_decisions.get(task.context['run_id'])
        if not decision:
            raise UnknownDecisionException()
            
        del self.running_decisions[task.context['run_id']]
        (managed_process, expiration) = decision

        # append the decision events
        for decision in decisions:
            managed_process.history.append(DecisionEvent(decision))
            
            # schedule activity if needed
            if hasattr(decision, 'activity'):
                self._schedule_activity(managed_process, decision.activity, decision.id, decision.input, queue=decision.category)

            # cancel activity
            if isinstance(decision, CancelActivity):
                activity = self._activity_by_id(decision.id)
                self._cancel_activity(decision.id)
                managed_process.history.append(ActivityEvent(activity[0], ActivityCanceled()))

            # complete process
            if isinstance(decision, CompleteProcess) or isinstance(decision, CancelProcess):
                if managed_process.id in self.running_processes:
                    #del self.running_processes[managed_process.id]
                    #self._cancel_decision(managed_process)
                    self._cancel_process_internal(managed_process)
                    if managed_process.parent:
                        parent = self._managed_process(managed_process.parent)
                        if decision.type == 'complete_process':
                            parent.history.append(ChildProcessEvent(process_id=managed_process.id, result=ProcessCompleted(result=decision.result), workflow=managed_process.workflow, tags=managed_process.tags))
                        elif decision.type == 'cancel_process':
                            parent.history.append(ChildProcessEvent(process_id=managed_process.id, result=ProcessCanceled(details=decision.details), workflow=managed_process.workflow, tags=managed_process.tags))
                        self._schedule_decision(parent)

            # start child process
            if isinstance(decision, StartChildProcess):
                process = Process(workflow=decision.process.workflow, id=decision.process.id or str(uuid4()), input=decision.process.input, tags=decision.process.tags, parent=task.process.id)
                self.running_processes[process.id] = process
                self._schedule_decision(process)

            # schedule timer
            if isinstance(decision, Timer):
                self._schedule_decision(managed_process, start=datetime.now() + timedelta(seconds=decision.delay), timer=decision)


    def complete_activity_task(self, task, result=None):
        self._time_out_activities()

        # find the process as we know it
        activity = self.running_activities.get(task.context['run_id'])
        if not activity:
            raise UnknownActivityException()

        del self.running_activities[task.context['run_id']]
        
        (execution, managed_process, expiration, heartbeat_expiration) = activity

        # append the activity event
        managed_process.history.append(ActivityEvent(execution, result))

        # schedule a decision (if needed)
        self._schedule_decision(managed_process)

    def process_by_id(self, pid):
        return self._managed_process(pid)

    def processes(self, workflow=None, tag=None):
        return ifilter(lambda p: (p.workflow == workflow or not workflow) and (tag in p.tags or not tag), self.running_processes.values())

    def _time_out_activities(self):
        # activities that are past expired scheduling date. they're in scheduled_activities
        for q, queue in self.scheduled_activities.items():
            for expired in filter(lambda a: a[2] < datetime.now(), queue):
                queue.remove(expired)
                self._schedule_decision(expired[1])

                expired[1].history.append(ActivityEvent(expired[0], ActivityTimedOut()))

        # activities that are past expired execution date. they're in running_activities
        for (i, expired) in filter(lambda (i,a): a[2] < datetime.now() or a[3] < datetime.now(), self.running_activities.items()):
            del self.running_activities[i]
            self._schedule_decision(expired[1])

            expired[1].history.append(ActivityEvent(expired[0], ActivityTimedOut()))
        
    def _time_out_decisions(self):
        # decisions that are past expired execution date. they're in running_decisions
        for (i,expired) in filter(lambda (i,a): a[1] < datetime.now(), self.running_decisions.items()):
            del self.running_decisions[i]
            self._schedule_decision(expired[0])

        # sometimes scheduled decisions have been there for too long as well
        for cat, queue in self.scheduled_decisions.items():
            for (expired) in filter(lambda d: d[2] and d[2] < datetime.now(), queue):
                queue.remove(expired)
                self._schedule_decision(expired[0])

    def poll_activity_task(self, category=Defaults.ACTIVITY_CATEGORY, identity=None):
        # find queued activity tasks (that haven't timed out)
        try:
            while True:
                (activity_execution, process, expiration) = self.scheduled_activities[category].popleft()
                if expiration >= datetime.now():
                    break
        except:
            return None
        
        run_id = str(uuid4())
        expiration = datetime.now() + timedelta(seconds=self.activities[activity_execution.activity]['execution_timeout'])
        heartbeat_expiration = datetime.now() + timedelta(seconds=self.activities[activity_execution.activity]['heartbeat_timeout'])
        self.running_activities[run_id] = (activity_execution, process, expiration, heartbeat_expiration)
            
        process.history.append(ActivityStartedEvent(activity_execution))

        return ActivityTask(activity_execution, process_id=process.id, context={'run_id': run_id})

    def poll_decision_task(self, category=Defaults.DECISION_CATEGORY, identity=None):
        # time-out expired activities
        self._time_out_activities()
        self._time_out_decisions()

        # find queued decision tasks (that haven't timed out)
        queue = self.scheduled_decisions[category]
        for d in queue:
            (process, start, expiration, timer) = d
            if start and start > datetime.now():
                continue
                #self.scheduled_decisions.append((process, start, expiration, timer))
            elif not expiration or expiration >= datetime.now():
                if start:
                    process.history.append(TimerEvent(timer))

                queue.remove(d)
                break
        else:
            return None

        run_id = str(uuid4())
        expiration = datetime.now() + timedelta(seconds=self.workflows[process.workflow]['timeout'])
        self.running_decisions[run_id] = (process, expiration)
        
        process.history.append(DecisionStartedEvent())

        return DecisionTask(process, context={'run_id': run_id})
        