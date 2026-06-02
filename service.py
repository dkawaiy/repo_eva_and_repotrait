from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from loguru import logger

from api import eva_with_response, RATask, RAResult, RAStatus, CompReq, CompResult, compare

app = FastAPI()

from api_r.api_main import auto_profile_task_worker
from api_r.vo import AutoProfileTaskRequest, TaskAck, TaskStatus
from pydantic import ValidationError



@app.post('/tools/hcl')
async def hcl(req: RATask, background_tasks: BackgroundTasks) -> RAResult:
    logger.info(f'[Service] hcl receive request: {req.model_dump_json()}')
    background_tasks.add_task(eva_with_response, req=req)
    return RAResult(id=req.id, status=RAStatus.received.value, message='task received')


@app.post('/tools/callback')
def test(req: RAResult):
    logger.info(req.model_dump_json())
    return 'ok'


@app.get('/tools/test')
def test2():
    print('hello')
    return 'hello'


@app.post('/tools/comp')
async def comp(req: CompReq, background_tasks: BackgroundTasks) -> CompResult:
    logger.info(f'[Service] comp receive request: {req.model_dump_json()}')
    background_tasks.add_task(compare, req=req)
    return CompResult(requestId=req.requestId, status=RAStatus.received.value, message='task received')


@app.post("/profile/batch", response_model=TaskAck)
async def profile_batch_endpoint(request: Request, background_tasks: BackgroundTasks) -> TaskAck:
    body_bytes = await request.body()
    body_str = body_bytes.decode()
    logger.info(f'[Service] profile_batch raw request: {body_str}')
    try:
        req = AutoProfileTaskRequest.parse_raw(body_str)
    except ValidationError as e:
        logger.error(f'[Service] profile_batch validation error: {e}')
        raise HTTPException(status_code=422, detail=str(e))
    logger.info(f'[Service] profile_batch receive request: {str(req)}')
    background_tasks.add_task(auto_profile_task_worker, req=req)
    return TaskAck(id=req.id, status=TaskStatus.received, message="task received")
