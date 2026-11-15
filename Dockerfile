FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# URL path the app is served under behind a reverse proxy (run.sh sets /visualmcq-survey); empty = /
ENV VMCQ_PREFIX=""
COPY requirements.txt .
RUN pip install -r requirements.txt

# questions + images (made by prepare_data.py), app, default accounts
COPY data/ data/
COPY config/ config/
COPY templates/ templates/
COPY app.py .

# responses live in /app/state; mount a host folder there so they survive rebuilds
RUN mkdir -p /app/state && chmod 777 /app/state
VOLUME /app/state
EXPOSE 8015
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:8015", "--access-logfile", "-", "app:app"]
