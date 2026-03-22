from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from llama_wrapper import run_llama_oneword
from datetime import datetime, timezone
from pymongo import MongoClient
from typing import Optional, List
from trading.api import router as bot_router, market_router, sentiment_router
from trading.config import settings

app = FastAPI(title="AIInvest API", version="0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(bot_router)
app.include_router(market_router)
app.include_router(sentiment_router)



# a nech si původní news routy beze změny

mongo = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=2000)
db = mongo[settings.MONGO_DB]
sentiments = db["sentiments"]
news = db["news"]

class SentimentRequest(BaseModel):
    text: str = Field(min_length=3, max_length=2000)

class SentimentResponse(BaseModel):
    sentiment: str
    
class NewsItem(BaseModel):
    news_id: str
    title: str
    url: str
    published_at: Optional[str] = None
    sentiment: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/sentiment", response_model=SentimentResponse)
def sentiment(req: SentimentRequest):
    prompt = (
        "Return exactly ONE word: Positive, Neutral, or Negative.\n"
        f"Text: {req.text}\n"
        "Answer:"
    )

    s = run_llama_oneword(prompt, timeout_sec=25)

    doc = {
        "text": req.text,
        "sentiment": s,
        "created_at": datetime.now(timezone.utc),
        "source": "api",
    }
    sentiments.insert_one(doc)

    return SentimentResponse(sentiment=s)

@app.get("/news/latest", response_model=List[NewsItem])
def latest_news(limit: int = 10):
    limit = max(1, min(limit, 50))

    items = list(
        news.find({}, {"title": 1, "url": 1, "published_at": 1})
            .sort("published_at", -1)
            .limit(limit)
    )

    if not items:
        return []

    ids = [it["_id"] for it in items]

    # vezmeme poslední sentiment pro news_id (z news_worker)
    sent_map = {}
    for s in sentiments.find(
        {"news_id": {"$in": ids}},
        {"news_id": 1, "sentiment": 1, "created_at": 1}
    ).sort("created_at", -1):
        nid = s.get("news_id")
        if nid not in sent_map:
            sent_map[nid] = s.get("sentiment")

    out = []
    for it in items:
        nid = it["_id"]
        pub = it.get("published_at")
        pub_str = pub.isoformat() if hasattr(pub, "isoformat") else (str(pub) if pub else None)

        out.append(NewsItem(
            news_id=str(nid),
            title=it.get("title", ""),
            url=it.get("url", ""),
            published_at=pub_str,
            sentiment=sent_map.get(nid)
        ))

    return out

@app.get("/whoami")
def whoami():
    return {"file": __file__}
