# Deployment

Deploy the FastAPI service with:

```text
Build: pip install -r requirements.txt
Start: uvicorn app.main:app --host 0.0.0.0 --port $PORT
Health: /health
```

The only application configuration is `DATABASE_URL`. Call state is intentionally in memory for this single-instance hackathon backend.

WebRTC audio flows directly between the two devices. The backend does not provide media, ICE servers, relays, or credentials.
