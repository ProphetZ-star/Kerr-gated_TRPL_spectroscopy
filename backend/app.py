from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import json
import os
import secrets
import math
import uuid
from urllib.parse import unquote
from datetime import datetime, timezone
from fastapi.exceptions import RequestValidationError
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .manager import WorkerManager
from .models import (RunRequest, MoveRequest, CameraConfig, BackgroundRequest,
                     SpectrographConfig, CalibrationRequest, FitRequest)
from .calibration_models import CaptureRequest, ExtractRequest, MappingRequest, ApplyRequest
from .errors import CommandError
from .identity import identity

ROOT=Path(__file__).resolve().parents[1]


class SmallBody(BaseModel):
    model_config=ConfigDict(extra='forbid')


class ConnectBody(SmallBody):
    mode: str='simulation'


class PathBody(SmallBody):
    path: str=Field(min_length=1,max_length=4096)


class SaveBody(PathBody):
    overwrite_token: str|None=Field(default=None,min_length=64,max_length=64)


class EmptyServiceStopBody(SmallBody):
    checkout_id: str=Field(min_length=64,max_length=64)
    process_id: int=Field(gt=0)
    worker_session_id: str=Field(min_length=1,max_length=128)


def acquire_instance_lock():
    lock_path=ROOT/'.server.lock'
    handle=lock_path.open('a+b')
    handle.seek(0)
    if os.name=='nt':
        import msvcrt
        if lock_path.stat().st_size==0:handle.write(b'0');handle.flush();handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            handle.close();raise RuntimeError('Another Kerr Studio instance is already running')
    else:
        import fcntl
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    return handle


@asynccontextmanager
async def lifespan(app):
    instance=acquire_instance_lock()
    app.state.token=secrets.token_urlsafe(32)
    try:
        app.state.worker=await asyncio.to_thread(WorkerManager)
        yield
    finally:
        if hasattr(app.state,'worker'):await asyncio.to_thread(app.state.worker.close)
        instance.close()


app=FastAPI(title='Kerr Studio local acquisition',version='0.1.1',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])


def json_safe(value):
    if isinstance(value,float) and not math.isfinite(value):return str(value)
    if isinstance(value,dict):return {str(k):json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [json_safe(v) for v in value]
    if value is None or isinstance(value,(str,int,float,bool)):return value
    return str(value)


@app.exception_handler(RequestValidationError)
async def invalid_request(request,exc):
    return JSONResponse(status_code=422,content={'detail':json_safe(exc.errors())})


@app.middleware('http')
async def local_session(request:Request,call_next):
    origin=request.headers.get('origin')
    expected=f'{request.url.scheme}://{request.headers.get("host","")}'
    if origin and origin!=expected:
        return JSONResponse({'detail':'Cross-origin access is disabled'},status_code=403)
    if request.method not in {'GET','HEAD','OPTIONS'}:
        token=request.headers.get('x-session-token','')
        if not secrets.compare_digest(token,getattr(app.state,'token','missing')):
            return JSONResponse({'detail':'Missing local session token'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return response


async def call(name,data=None):
    try:return await asyncio.to_thread(app.state.worker.call,name,data or {})
    except CommandError as exc:raise HTTPException(exc.status_code,str(exc)) from exc
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    except (RuntimeError,TimeoutError) as exc:raise HTTPException(503,str(exc)) from exc


@app.get('/')
async def index():return FileResponse(ROOT/'index.html')


app.mount('/frontend',StaticFiles(directory=ROOT/'frontend'),name='frontend')


@app.get('/api/v1/session')
async def session():
    response=JSONResponse(dict(token=app.state.token))
    response.set_cookie('kerr_session',app.state.token,httponly=True,samesite='strict')
    return response


@app.get('/api/v1/status')
async def status(compact:bool=False,since:str|None=None,latest_version:int=-1):
    return await call('status',dict(compact=True,since=since,latest_version=latest_version)) if compact else await call('status')


@app.get('/api/v1/identity')
async def service_identity():return identity()|dict(process_id=os.getpid(),launch_id=getattr(app.state,'launch_id',None))


@app.get('/api/v1/health')
async def service_health():
    process=getattr(app.state.worker,'process',None)
    return dict(worker_alive=process.is_alive() if process is not None else True)


@app.post('/api/v1/service/stop')
async def stop_service():
    state=await call('status');run=state.get('run')
    if state['busy'] or (run and run['committed_frames'] and not run['saved']):
        raise HTTPException(409,'Stop acquisition and save or discard the memory experiment before stopping the service')
    shutdown=getattr(app.state,'request_shutdown',None)
    if shutdown is None:raise HTTPException(409,'This server must be stopped from its owning console')
    asyncio.get_running_loop().call_later(.2,shutdown)
    return dict(stopping=True)


@app.post('/api/v1/service/stop-if-empty')
async def stop_empty_service(body:EmptyServiceStopBody):
    if body.checkout_id!=identity()['checkout_id'] or body.process_id!=os.getpid():
        raise HTTPException(409,'The local service identity changed; it will remain running')
    shutdown=getattr(app.state,'request_shutdown',None)
    if shutdown is None:raise HTTPException(409,'This server must be stopped from its owning console')
    result=await call('prepare_empty_shutdown',dict(worker_session_id=body.worker_session_id))
    asyncio.get_running_loop().call_later(.2,shutdown)
    return result


@app.get('/api/v1/devices/capabilities')
async def capabilities():return await call('capabilities')


@app.post('/api/v1/devices/connect')
async def connect(body:ConnectBody):return await call('connect',body.model_dump())


@app.post('/api/v1/devices/disconnect')
async def disconnect():return await call('disconnect')


@app.post('/api/v1/camera/configure')
async def configure(body:CameraConfig):return await call('configure',body.model_dump())


@app.post('/api/v1/stage/move')
async def move(body:MoveRequest):return await call('move',body.model_dump())


@app.post('/api/v1/stage/zero')
async def zero():return await call('zero')


@app.post('/api/v1/stage/stop')
async def stage_stop():return await call('stage_stop')


@app.post('/api/v1/preview/{action}')
async def preview(action:str):
    if action not in {'start','stop'}:raise HTTPException(404)
    return await call('preview_'+action)


@app.post('/api/v1/backgrounds')
async def background(body:BackgroundRequest):return await call('background',body.model_dump())


@app.post('/api/v1/backgrounds/stop')
async def background_stop():return await call('background_stop')


@app.post('/api/v1/spectrograph/config')
async def spectrograph(body:SpectrographConfig):return await call('spectrograph',body.model_dump())


@app.post('/api/v1/calibration/load')
async def calibration(body:CalibrationRequest):return await call('calibration',body.model_dump())


@app.get('/api/v1/calibration/frame')
async def calibration_frame():return await call('calibration_frame')


@app.post('/api/v1/calibration/frames')
async def calibration_capture(body:CaptureRequest):return await call('calibration_capture',body.model_dump())


@app.post('/api/v1/calibration/extract')
async def calibration_extract(body:ExtractRequest):return await call('calibration_extract',body.model_dump())


@app.post('/api/v1/calibration/fit')
async def calibration_fit(body:MappingRequest):return await call('calibration_fit',body.model_dump())


@app.get('/api/v1/calibration/solutions/{solution_id}')
async def calibration_solution(solution_id:str):return await call('calibration_solution',dict(solution_id=solution_id))


@app.post('/api/v1/calibration/apply')
async def calibration_apply(body:ApplyRequest):return await call('calibration_apply',body.model_dump())


@app.post('/api/v1/scan/plan')
async def plan(body:RunRequest):return await call('plan',body.model_dump())


@app.post('/api/v1/output/check')
async def check_output(body:PathBody):return await call('output_check',body.model_dump())


@app.post('/api/v1/runs')
async def run(body:RunRequest):return await call('run',body.model_dump())


@app.post('/api/v1/runs/open')
async def open_run(body:PathBody):return await call('open',body.model_dump())


@app.post('/api/v1/analysis/open')
async def open_analysis(body:PathBody):return await call('analysis_open',body.model_dump())


@app.get('/api/v1/analysis/runs/{run_id}/records')
async def analysis_records(run_id:str,offset:int=0,limit:int=100):
    if offset<0 or not 1<=limit<=100:raise HTTPException(422,'Invalid pagination')
    return await call('records',dict(run_id=run_id,offset=offset,limit=limit,analysis=True))


@app.post('/api/v1/analysis/runs/{run_id}/fits')
async def analysis_fit(run_id:str,body:FitRequest):return await call('fit',dict(run_id=run_id,options=body.model_dump(),analysis=True))


@app.post('/api/v1/analysis/runs/{run_id}/close')
async def close_analysis(run_id:str):return await call('analysis_close',dict(run_id=run_id,analysis=True))


@app.post('/api/v1/runs/import')
@app.post('/api/v1/analysis/import')
async def import_run(request:Request):
    name=unquote(request.headers.get('x-file-name','experiment.h5')).replace('\\','/').split('/')[-1]
    if not name.lower().endswith('.h5'):raise HTTPException(422,'Choose an HDF5 .h5 file')
    directory=ROOT/'data'/'imports'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    # Keep a local imported copy for later fit persistence, never rewrite the
    # user's selected source file through a browser upload.
    target=directory/'experiment.h5'
    try:
        size=0
        with target.open('xb') as stream:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>256*1024**2:raise HTTPException(413,'This browser import supports files up to 256 MiB')
                await asyncio.to_thread(stream.write,chunk)
        if not size:raise HTTPException(422,'The selected HDF5 file is empty')
        return await call('analysis_open' if request.url.path.endswith('/analysis/import') else 'open',dict(path=str(target)))
    except BaseException:
        target.unlink(missing_ok=True);directory.rmdir();raise


@app.get('/api/v1/runs/{run_id}')
async def get_run(run_id:str):return await call('get_run',dict(run_id=run_id))


@app.get('/api/v1/runs/{run_id}/records')
async def records(run_id:str,offset:int=0,limit:int=100):
    if offset<0 or not 1<=limit<=100:raise HTTPException(422,'Invalid pagination')
    return await call('records',dict(run_id=run_id,offset=offset,limit=limit))


@app.post('/api/v1/runs/{run_id}/save')
async def save(run_id:str,body:SaveBody):return await call('save',dict(run_id=run_id,**body.model_dump()))


@app.post('/api/v1/runs/{run_id}/fits')
async def fit(run_id:str,body:FitRequest):return await call('fit',dict(run_id=run_id,options=body.model_dump()))


@app.get('/api/v1/tasks/{task_id}')
async def task(task_id:str):return await call('task',dict(task_id=task_id))


@app.post('/api/v1/tasks/{task_id}/cancel')
async def cancel_task(task_id:str):return await call('task_cancel',dict(task_id=task_id))


@app.get('/api/v1/runs/{run_id}/export')
async def export(run_id:str,center_nm:float=600,half_window_nm:float=5):
    if not math.isfinite(center_nm) or center_nm<=0 or not math.isfinite(half_window_nm) or half_window_nm<=0:
        raise HTTPException(422,'Wavelength and half-window must be positive finite numbers')
    text=await call('export',dict(run_id=run_id,center_nm=center_nm,half_window_nm=half_window_nm))
    return PlainTextResponse(text,media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="kerr_{run_id}_simulation.csv"'})


@app.post('/api/v1/runs/{run_id}/{action}')
async def control(run_id:str,action:str):
    if action not in {'pause','resume','stop','discard'}:raise HTTPException(404)
    return await call(action,dict(run_id=run_id))


@app.websocket('/api/v1/events')
async def events(websocket:WebSocket):
    origin=websocket.headers.get('origin')
    expected='http://'+websocket.headers.get('host','')
    if (origin and origin!=expected) or not secrets.compare_digest(websocket.cookies.get('kerr_session',''),app.state.token):
        await websocket.close(code=1008);return
    await websocket.accept()
    try:
        snapshot=await call('status',dict(compact=True));seq=snapshot['seq']
        def envelope(kind,payload=None):
            return dict(type=kind,seq=seq,schema_version=1,worker_session_id=snapshot['worker_session_id'],
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),payload=payload or {})
        await websocket.send_json(envelope('snapshot',snapshot))
        while True:
            process=getattr(app.state.worker,'process',None)
            if process is not None and not process.is_alive():
                raise RuntimeError('Acquisition worker stopped. Restart the local backend.')
            batch=app.state.worker.after(seq)
            if batch:
                for message in batch:
                    await websocket.send_json(message);seq=message['seq']
            else:
                await websocket.send_json(envelope('heartbeat',dict(worker_alive=True)))
            await asyncio.sleep(.2)
    except WebSocketDisconnect:pass
    except (HTTPException,RuntimeError,OSError,TimeoutError):
        try:await websocket.close(code=1011,reason='Acquisition worker unavailable; restart the local backend')
        except (RuntimeError,OSError):pass
