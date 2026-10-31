"""Spawned worker IPC and event ring; no vendor libraries in the API process."""
from collections import deque
from concurrent.futures import Future
from threading import Lock, Thread
import multiprocessing as mp
import queue
import time
import uuid
from .errors import CommandError


def worker_entry(commands, responses, events):
    from .worker import worker_main
    worker_main(commands, responses, events)


class WorkerManager:
    def __init__(self):
        context=mp.get_context('spawn')
        self.commands=context.Queue();self.responses=context.Queue();self.events=context.Queue(maxsize=128)
        self.process=context.Process(target=worker_entry,args=(self.commands,self.responses,self.events),daemon=True)
        self.pending={};self.lock=Lock();self.ring=deque(maxlen=256);self.closed=False
        self.process.start()
        Thread(target=self._responses,daemon=True).start();Thread(target=self._events,daemon=True).start()
        self.call('status',{},timeout=30)

    def _responses(self):
        while not self.closed:
            try:message=self.responses.get(timeout=.2)
            except queue.Empty:continue
            except (EOFError,OSError,ValueError):return
            with self.lock:future=self.pending.pop(message['id'],None)
            if future and not future.done():
                if 'error' in message:future.set_exception(CommandError(message['error'],message.get('status_code',409)))
                else:future.set_result(message['result'])

    def _events(self):
        while not self.closed:
            try:message=self.events.get(timeout=.2)
            except queue.Empty:continue
            except (EOFError,OSError,ValueError):return
            with self.lock:self.ring.append(message)

    def call(self,name,data=None,timeout=45):
        if not self.process.is_alive():raise RuntimeError('Acquisition worker stopped. Restart the local backend; device state is unknown.')
        identifier=uuid.uuid4().hex;future=Future()
        with self.lock:self.pending[identifier]=future
        self.commands.put(dict(id=identifier,name=name,data=data or {}))
        try:
            deadline=time.monotonic()+timeout
            while True:
                try:return future.result(timeout=min(.2,max(.001,deadline-time.monotonic())))
                except TimeoutError:
                    if not self.process.is_alive():raise RuntimeError('Acquisition worker stopped. Restart the local backend; device state is unknown.')
                    if time.monotonic()>=deadline:raise TimeoutError('Local backend request timed out; operation may still be finishing')
        finally:
            with self.lock:self.pending.pop(identifier,None)

    def after(self,seq):
        with self.lock:
            events=[event for event in self.ring if event['seq']>seq]
            if events and events[0]['seq']>seq+1:
                first=events[0]
                gap={k:first[k] for k in ('schema_version','worker_session_id','timestamp_utc') if k in first}
                events.insert(0,gap|dict(type='stream.gap',seq=first['seq']-1,payload=dict(expected_seq=seq+1,available_from=first['seq'],resync_required=True)))
            return events

    def close(self):
        if self.closed:return
        self.commands.put(dict(name='shutdown'))
        self.process.join(12)
        if self.process.is_alive():self.process.terminate();self.process.join(3)
        self.closed=True
        for q in [self.commands,self.responses,self.events]:
            q.close()
