FROM python:3.12-slim

WORKDIR /app

# postgresql-client: wird für die Backup-Wiederherstellung (psql) benötigt
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Abhängigkeiten zuerst (besseres Layer-Caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Dedizierter Dienstbenutzer statt root. Die feste UID/GID deckt sich mit dem
# Host-Benutzer, den scripts/lib-common.sh anlegt – docker-compose.yml kann
# den Prozess über APP_UID/APP_GID exakt darauf setzen.
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid "$APP_GID" rechnung \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --no-create-home \
       --shell /usr/sbin/nologin --comment "Rechnungs-App" rechnung

COPY --chown=rechnung:rechnung app ./app
RUN chown -R rechnung:rechnung /app

USER rechnung

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
