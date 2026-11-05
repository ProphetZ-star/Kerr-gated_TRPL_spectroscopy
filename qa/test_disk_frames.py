"""Bounded frame storage, streaming scientific processing and acquisition lifecycle regressions."""
import copy
import itertools
import os
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

import h5py
import numpy as np

from backend.models import FitRequest, RunRequest
from backend.processing import kinetics
from backend.analysis import fit_run
from backend.drivers.sim import convolved_decay
from backend.storage.disk_frames import DiskFrames, FrameView, PAGE_FRAMES
from backend.storage.frames import FrameTable
from backend.storage.hdf5 import HDFWriter, read_run
from backend.storage.export import export_csv
from backend.storage.schema import COLUMNS
from backend.worker import Engine, shutdown_engine


def metadata(pixels=16):
    return dict(root=dict(schema_version=2,software_version='test',run_id='disk-test',source_mode='simulation',
                         created_utc='2026-11-05T00:00:00+00:00'),instrument={},
                request=dict(mode='repeat',repeats=2,frames_per_point=600,exposure_s=.2,settle_s=0,
                             segments=[dict(start=0,end=3,step=1)]),coordinates={},
                acquisition=dict(delays_ps=[0,1,2,3],targets_mm=[150,151,152,153],pixels=pixels,exposure_s=.2),
                calibration=dict(wavelengths_nm=np.linspace(500,700,pixels).tolist()),background=dict(present=False))


def table_fixture(pixels=16):
    n=4800
    rng=np.random.default_rng(15)
    spectra=rng.integers(20,500,(n,pixels),dtype=np.int32)
    columns={name:np.zeros((n,width) if width else (n,),dtype=dtype) for name,dtype,width in COLUMNS}
    columns['index'][:]=np.column_stack((np.repeat([0,1],2400),np.tile(np.repeat(np.arange(4),600),2),np.tile(np.arange(600),8)))
    columns['delay_ps'][:]=columns['index'][:,1]
    columns['temperature_state'][::7]=2
    columns['stage_reported_mm'][:]=150+columns['index'][:,1]
    columns['stage_reported_mm'][::11]+=.01
    # The last page of a point marks earlier pages of that same point as saturated too.
    spectra[599, pixels//2]=65535
    spectra[3001, pixels//2]=65535
    return FrameTable.from_arrays(spectra,columns,.2,'simulation')


class DiskFrameTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'experiment.h5'
        self.meta=metadata();self.table=table_fixture()
        writer=HDFWriter(self.path,self.meta)
        for start in range(0,len(self.table),512):writer.call('rows',self.table.block(start,start+512))
        writer.call('close',dict(status='completed',completed_rounds=2))
        self.info,self.disk,_=read_run(self.path)

    def assert_tree_close(self,actual,expected):
        if isinstance(expected,dict):
            self.assertEqual(set(actual),set(expected))
            for key in expected:self.assert_tree_close(actual[key],expected[key])
        elif isinstance(expected,list) and expected and isinstance(expected[0],dict):
            self.assertEqual(len(actual),len(expected))
            for a,e in zip(actual,expected):self.assert_tree_close(a,e)
        elif expected is None or isinstance(expected,(str,bool)):
            self.assertEqual(actual,expected)
        else:np.testing.assert_allclose(actual,expected,rtol=1e-12,atol=1e-10,equal_nan=True)

    def test_open_is_lazy_pages_exact_and_cache_bounded(self):
        self.assertIsInstance(self.disk,DiskFrames)
        self.assertEqual(self.disk.cached_bytes,0)
        self.disk.cache_bytes=70000
        for start,stop in [(0,1),(500,1500),(3000,4000),(1,30),(4800,4800),(4900,5000)]:
            block=self.disk.block(start,stop)
            expected=self.table.block(min(start,len(self.table)),stop)
            for name in ('spectra',)+tuple(n for n,_,_ in COLUMNS):
                np.testing.assert_array_equal(block[name],expected[name])
            self.assertLessEqual(self.disk.cached_bytes,self.disk.cache_bytes)
        self.assertEqual(self.disk.completed_points(600),8)
        self.assertEqual(self.disk.record(-1)['record_id'],4799)
        block=self.disk.block(0,3);block['spectra'][:]=0
        np.testing.assert_array_equal(self.disk.block(0,3)['spectra'],self.table.spectra[:3])

    def test_streaming_processing_matches_array_reference(self):
        axis=self.meta['calibration']['wavelengths_nm']
        full=self.table.block(0,len(self.table))
        for response_kind,exclude_sat,unstable,chosen in itertools.product(range(3),(False,True),(False,True),(None,[1])):
            response=None if response_kind==0 else np.linspace(.2,1,16)
            background=None if response_kind==1 else np.full(16,10.)
            options=FitRequest(wavelength_nm=600,half_window_nm=35,exclude_saturated=exclude_sat,
                               exclude_unstable=unstable,rounds=chosen,subtract_background=response_kind==0,
                               position_tolerance_um=5,min_delay_ps=.5,max_delay_ps=2.5)
            args=(axis,options,background,65535,[150,151,152,153])
            with self.subTest(response=response_kind,saturation=exclude_sat,unstable=unstable,rounds=chosen):
                expected=kinetics(full,*args,response=response)
                actual=kinetics(FrameView(self.disk),*args,response=response)
                self.assert_tree_close(actual,expected)
        options=FitRequest(wavelength_nm=600,half_window_nm=35)
        curve=kinetics(FrameView(self.disk),axis,options,full_scale=65535)
        self.assertEqual(curve['counts']['saturated_points'],2)
        self.assertEqual(curve['counts']['excluded_saturated_frames'],1200)

    def test_csv_is_identical_and_uses_pages(self):
        args=(self.meta['calibration']['wavelengths_nm'],600,35,'simulation',np.full(16,10),65535,[150,151,152,153])
        with patch.object(self.disk,'block',wraps=self.disk.block) as block:
            actual=export_csv(self.disk,*args)
            self.assertTrue(all(call.args[1]-call.args[0]<=PAGE_FRAMES for call in block.call_args_list))
        self.assertEqual(actual,export_csv(self.table,*args))

    def test_cancel_and_file_replacement(self):
        cancel=Event();cancel.set()
        with self.assertRaisesRegex(ValueError,'cancelled'):
            kinetics(FrameView(self.disk),self.meta['calibration']['wavelengths_nm'],FitRequest(),cancel=cancel)
        self.disk.block(0,1)
        with h5py.File(self.path,'r+') as file:file.attrs.modify('run_id','replacement')
        with self.assertRaisesRegex(ValueError,'replaced'):self.disk.block(0,1)

    def test_snapshot_detects_truncation(self):
        snapshot=FrameView(self.disk)
        self.disk.truncate(10)
        with self.assertRaisesRegex(ValueError,'changed'):list(snapshot.iter_blocks())

    def test_fit_matches_and_reads_only_once(self):
        axis=self.meta['calibration']['wavelengths_nm'];table=self.table
        t=np.tile(np.repeat(np.linspace(-1,10,24),100),2)
        columns={n:table.column(n).copy() for n,_,_ in COLUMNS}
        columns['delay_ps']=t;columns['index'][:,1]=np.tile(np.repeat(np.arange(24),100),2)
        columns['temperature_state'][:]=0
        y=np.rint(100+4000*convolved_decay(t,2.,.25/2.354820045)).astype('i4')
        fitted=FrameTable.from_arrays(np.repeat(y[:,None],16,axis=1),columns,.2,'simulation')
        options=FitRequest(model='single',wavelength_nm=600,half_window_nm=35,exclude_saturated=False)
        expected=fit_run(fitted.block(0,len(fitted)),axis,options)
        with patch.object(fitted,'block',wraps=fitted.block) as block:
            actual=fit_run(FrameView(fitted),axis,options)
            self.assertEqual(block.call_count,10)  # 4800 / 512, not repeated for each fitted round.
        self.assert_tree_close(actual,expected)

    def test_live_read_truncate_finalize_and_preserve(self):
        path=Path(self.temp.name)/'live.h5';writer=HDFWriter(path,self.meta)
        disk=DiskFrames(writer.partial,0,16,.2,'simulation','disk-test',writer)
        try:
            for i in range(3):
                record=self.table.record(i);writer.call('record',record);disk.append(record)
                np.testing.assert_array_equal(disk.block(0,i+1)['spectra'],self.table.spectra[:i+1])
            with self.assertRaisesRegex(ValueError,'committed'):writer.call('read',(0,4))
            self.assertIsNone(writer.failure)  # A bad read must not poison acquisition writes.
            writer.call('truncate',2);disk.truncate(2)
            disk.finalize(writer,dict(status='error',completed_rounds=0))
            disk.preserve_unfinished()
            writer.partial.unlink()
            np.testing.assert_array_equal(disk.block(0,2)['spectra'],self.table.spectra[:2])
            self.assertEqual(len(disk),2)
        finally:
            if not writer.finished.is_set():writer.call('close',dict(status='error'))
            if disk._temporary:disk._temporary.cleanup()


class EngineDiskTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,KERR_STATE_DIR=self.temp.name);self.env.start();self.addCleanup(self.env.stop)
        self.engine=Engine(Queue());self.addCleanup(lambda:shutdown_engine(self.engine))
        self.engine.handle('connect',dict(mode='simulation'))

    def scan(self,autosave=True,fault=None):
        request=RunRequest(request_id=str(time.monotonic()),segments=[dict(start=0,end=2,step=1)],
                           exposure_s=.001,settle_s=0,speedup=1000,frames_per_point=2,autosave=autosave,
                           save_path=str(Path(self.temp.name)/('saved.h5' if autosave else 'manual.h5')),
                           fault=fault or dict(kind='none',point_index=0))
        result=self.engine.handle('run',request.model_dump());self.engine.job.join(15)
        self.assertFalse(self.engine.job.is_alive())
        return self.engine.runs[result['run_id']]

    def test_autosave_uses_disk_and_analysis_pages(self):
        run=self.scan()
        self.assertEqual(run['status'],'completed',run['error'])
        self.assertIsInstance(run['frames'],DiskFrames)
        self.assertEqual(len(run['frames']),6)
        self.assertEqual(self.engine.handle('records',dict(run_id=run['run_id'],offset=0,limit=2000))['block']['spectra'].shape,(6,1024))
        opened=self.engine.handle('analysis_open',dict(path=run['path']))
        self.assertEqual(opened['completed_points'],3)
        self.assertIsInstance(self.engine.analysis_runs[opened['run_id']]['frames'],DiskFrames)
        self.engine.handle('analysis_close',dict(run_id=opened['run_id'],analysis=True))

    def test_memory_only_then_save_switches_to_disk(self):
        run=self.scan(False);self.assertIsInstance(run['frames'],FrameTable)
        before=run['frames'].spectra.copy()
        self.engine.handle('save',dict(run_id=run['run_id'],path=str(Path(self.temp.name)/'manual.h5')))
        self.assertIsInstance(run['frames'],DiskFrames)
        np.testing.assert_array_equal(run['frames'].block(0,6)['spectra'],before)

    def test_failed_scan_survives_output_removal_and_resave(self):
        run=self.scan(fault=dict(kind='camera_disconnect',point_index=1))
        self.assertEqual(run['status'],'error')
        self.assertGreater(len(run['frames']),0)
        self.assertFalse(run.get('files_removal_blocked'))
        before=run['frames'].block(0,len(run['frames']))['spectra'].copy()
        Path(run['path']).unlink()
        self.engine.handle('run_files_removed',dict(run_id=run['run_id']))
        self.engine.handle('save',dict(run_id=run['run_id'],path=str(Path(self.temp.name)/'recovered.h5')))
        np.testing.assert_array_equal(run['frames'].block(0,len(before))['spectra'],before)

    def test_preservation_failure_keeps_original_output(self):
        with patch.object(DiskFrames,'preserve_unfinished',side_effect=OSError('No space for private copy')):
            run=self.scan(fault=dict(kind='camera_disconnect',point_index=1))
        self.assertTrue(run['files_removal_blocked'])
        self.assertTrue(Path(run['path']).is_file())
        self.assertGreater(len(run['frames'].block(0,len(run['frames']))['spectra']),0)

    def test_write_failure_retains_only_acknowledged_frames(self):
        run=self.scan(fault=dict(kind='write_failure',point_index=1))
        self.assertEqual(run['status'],'error')
        self.assertEqual(len(run['frames']),run['committed_frames'])
        _,disk,_=read_run(run['path'])
        self.assertEqual(len(disk),len(run['frames']))
        np.testing.assert_array_equal(disk.block(0,len(disk))['spectra'],run['frames'].block(0,len(disk))['spectra'])

    def test_active_paging_pause_and_stop_keep_whole_rounds(self):
        request=RunRequest(request_id='live-stop',mode='repeat',repeats=0,
            segments=[dict(start=0,end=2,step=1)],exposure_s=.001,settle_s=0,speedup=10,
            frames_per_point=3,save_path=str(Path(self.temp.name)/'stopped.h5'))
        result=self.engine.handle('run',request.model_dump());run=self.engine.runs[result['run_id']]
        deadline=time.monotonic()+10
        while run['committed_frames']<12 and self.engine.busy:
            if time.monotonic()>deadline:self.fail('Scan did not advance')
            page=self.engine.handle('records',dict(run_id=run['run_id'],offset=0,limit=2000))
            self.assertLessEqual(len(page['block']['spectra']),page['total'])
            time.sleep(.005)
        self.engine.handle('pause',dict(run_id=run['run_id']))
        while run['status']!='paused':
            if time.monotonic()>deadline:self.fail('Scan did not pause')
            time.sleep(.005)
        count=run['committed_frames']
        expected=run['frames'].block(0,count)['spectra'].copy()
        self.engine.handle('stop',dict(run_id=run['run_id']))
        self.engine.job.join(10)
        self.assertFalse(self.engine.job.is_alive())
        self.assertEqual(run['status'],'stopped',run['error'])
        self.assertEqual(len(run['frames'])%9,0)
        self.assertGreaterEqual(len(run['frames']),9)
        np.testing.assert_array_equal(run['frames'].block(0,len(run['frames']))['spectra'],expected[:len(run['frames'])])
        _,disk,status=read_run(run['path'])
        self.assertEqual(status,'stopped')
        self.assertEqual(len(disk),len(run['frames']))


if __name__=='__main__':unittest.main()
