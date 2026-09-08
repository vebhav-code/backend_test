import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.models
from app.database import Base, engine
from app.routers import contacts, noise_removal, users, voice_detection
from app.ws import router as ws_router


app = FastAPI(title="Calling App Signaling Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    """Create the existing user/contact tables on startup."""
    Base.metadata.create_all(bind=engine)


app.include_router(users.router)
app.include_router(users.login_router)
app.include_router(contacts.router)
app.include_router(voice_detection.router)
app.include_router(noise_removal.router)
app.include_router(ws_router)


@app.get("/")
def health_check():
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.head("/headhealth")
def health_head():
    return {"status" : "ok"}


if __name__ == "__main__":
    import os

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
