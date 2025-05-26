from fastapi import FastAPI, BackgroundTasks
from loguru import logger

from api import eva_with_response, RATask, RAResult, RAStatus, CompReq, CompResult, compare

app = FastAPI()


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
