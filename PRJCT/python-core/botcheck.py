from fastapi import FastAPI
from trading.api import router as bot_router

app = FastAPI()
app.include_router(bot_router)
