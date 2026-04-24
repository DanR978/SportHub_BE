FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Apply pending Alembic migrations before starting the app. Render re-runs
# this on every deploy, so new migrations land automatically without a
# manual shell step. If there's nothing to upgrade, this is a no-op.
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]